"""Hook dispatchers for Claude Code events.

Thin shell scripts read stdin and delegate to ``vox hook <event>``.
All business logic lives here as testable pure functions.

Audio playback goes through voxd via ``VoxClientSync``. Hooks never
do in-process synthesis, caching, or direct playback.

Events:
- **stop**: task-completion notification (decision-block pattern)
- **post-bash**: signal accumulator for auto-vibe
- **notification**: permission/idle prompt audio alerts
- **pre-compact**: playful 'be right back' before context compaction
- **user-prompt-submit**: acknowledgment in continuous mode
- **subagent-start**: subagent spawn announcement in continuous mode
- **subagent-stop**: subagent completion announcement in continuous mode
- **session-end**: farewell speech
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import random
import select
import sys
from pathlib import Path

import typer

from punt_vox.client import VoxClientSync, VoxdConnectionError
from punt_vox.config import (
    DEFAULT_CONFIG_PATH,
    VoxConfig,
    find_config,
    read_config,
    write_field,
    write_fields,
)
from punt_vox.quips import (
    ACKNOWLEDGE_PHRASES,
    FAREWELL_PHRASES,
    IDLE_PHRASES,
    PERMISSION_PHRASES,
    PRE_COMPACT_PHRASES,
    STOP_PHRASES,
    SUBAGENT_START_PHRASES,
    SUBAGENT_STOP_PHRASES,
)

logger = logging.getLogger(__name__)

MAX_VIBE_SIGNALS = 20

hook_app = typer.Typer(
    help="Hook dispatchers (called by hook scripts).",
    no_args_is_help=True,
)


@hook_app.callback(invoke_without_command=True)
def _hook_callback(ctx: typer.Context) -> None:  # pyright: ignore[reportUnusedFunction]
    """Initialize logging for all hook subcommands."""
    if ctx.invoked_subcommand is not None:
        from punt_vox.logging_config import configure_logging

        configure_logging(stderr_level="WARNING")


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _read_hook_input() -> dict[str, object]:
    """Read JSON hook payload from stdin (non-blocking).

    Uses ``select`` + ``os.read`` to avoid blocking forever when
    Claude Code does not close the stdin pipe.  See biff DES-027.
    """
    try:
        fd = sys.stdin.fileno()
        if not select.select([fd], [], [], 0.1)[0]:
            return {}
        chunks: list[bytes] = []
        while True:
            chunk = os.read(fd, 65536)
            if not chunk:
                break
            chunks.append(chunk)
            if not select.select([fd], [], [], 0.05)[0]:
                break
        raw = b"".join(chunks).decode()
        if not raw.strip():
            return {}
        data: object = json.loads(raw)
    except (json.JSONDecodeError, OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return dict(data)  # pyright: ignore[reportUnknownArgumentType]


def _emit(output: dict[str, object]) -> None:
    """Write JSON response to stdout."""
    typer.echo(json.dumps(output))


# ---------------------------------------------------------------------------
# Voxd client helpers
# ---------------------------------------------------------------------------


def _make_client() -> VoxClientSync:
    """Create a VoxClientSync for hook use."""
    return VoxClientSync()


def _speak_via_voxd(
    text: str,
    config: VoxConfig,
) -> None:
    """Synthesize and play a phrase via voxd.

    Catches ``VoxdConnectionError`` so a missing daemon never crashes
    a hook.
    """
    try:
        client = _make_client()
        kwargs: dict[str, str] = {}
        if config.voice:
            kwargs["voice"] = config.voice
        if config.provider:
            kwargs["provider"] = config.provider
        client.synthesize(text, **kwargs)
    except VoxdConnectionError:
        logger.warning("voxd not running, skipping speech")


def _chime_via_voxd(signal: str) -> None:
    """Play a chime via voxd.

    Catches ``VoxdConnectionError`` so a missing daemon never crashes
    a hook.
    """
    try:
        client = _make_client()
        client.chime(signal)
    except VoxdConnectionError:
        logger.warning("voxd not running, skipping chime")


# ---------------------------------------------------------------------------
# Stop handler — decision-block pattern
# ---------------------------------------------------------------------------


def resolve_tags_from_signals(signals: str) -> str:
    """Pick expressive tags from accumulated session signals.

    Deterministic mapping — no LLM needed. Examines signal counts and
    trajectory (how the session ended) to choose 1-2 ElevenLabs tags.
    """
    parts = [s.split("@")[0] for s in signals.split(",") if s]
    if not parts:
        return "[calm]"

    counts: dict[str, int] = {}
    for p in parts:
        counts[p] = counts.get(p, 0) + 1

    # Trajectory: what happened at the end matters most
    last_few = parts[-3:]
    ended_with_fail = any(s.endswith("-fail") for s in last_few)
    ended_with_pass = any(s.endswith("-pass") for s in last_few)
    had_push = "git-push-ok" in counts
    had_pr = "pr-created" in counts
    had_fails = sum(c for k, c in counts.items() if k.endswith("-fail"))
    had_passes = sum(c for k, c in counts.items() if k.endswith("-pass"))

    # Recovery arc: fails followed by passes
    if had_fails > 0 and ended_with_pass:
        return "[relieved]"

    # Shipped something
    if had_push or had_pr:
        if had_fails == 0:
            return "[satisfied]"
        return "[relieved] [satisfied]"

    # Mostly failing
    if ended_with_fail and had_fails > had_passes:
        return "[frustrated] [sighs]"

    # Productive session, all green
    if had_passes > 3 and had_fails == 0:
        return "[excited]"

    # Some passes, no drama
    if had_passes > 0:
        return "[calm]"

    return "[calm]"


def handle_stop(data: dict[str, object], config: VoxConfig) -> dict[str, object] | None:
    """Decide whether to block Claude from stopping.

    Returns a decision-block dict if Claude should speak a summary,
    or None to let it stop normally.
    """
    # Not enabled
    if config.notify == "n":
        logger.info("Stop hook: skip (notify=n)")
        return None

    # Already continuing from a previous Stop hook — prevent infinite loop
    stop_active = data.get("stop_hook_active", False)
    if stop_active is True:
        logger.info("Stop hook: skip (stop_hook_active=True, preventing loop)")
        return None

    # No signals = no meaningful work to summarize
    if not config.vibe_signals:
        logger.info("Stop hook: skip (no vibe_signals)")
        return None

    # Chime mode: play chime via voxd, let Claude stop
    if config.speak == "n":
        logger.info("Stop hook: chime mode, requesting done chime from voxd")
        _chime_via_voxd("done")
        return None

    # Voice mode: block the stop, ask Claude to summarize and speak.
    # Resolve tags and write to config so apply_vibe picks them up
    # automatically — no data in the user-visible reason string.
    phrase = random.choice(STOP_PHRASES)
    logger.info(
        "Stop hook: blocking for voice summary (signals=%s)",
        config.vibe_signals,
    )
    if config.vibe_mode == "off":
        pass  # User disabled vibe — don't write tags
    elif config.vibe_mode == "manual" and config.vibe_tags:
        pass  # Manual mode with existing tags — already set
    else:
        tags = resolve_tags_from_signals(config.vibe_signals)
        config_path = find_config() or DEFAULT_CONFIG_PATH
        write_fields({"vibe_tags": tags, "vibe_signals": ""}, config_path)
    return {"decision": "block", "reason": phrase}


# ---------------------------------------------------------------------------
# PostToolUse Bash — signal accumulator
# ---------------------------------------------------------------------------

_SIGNAL_PATTERNS: list[tuple[str, list[str]]] = [
    # Lint patterns before tests — "errors" appears in both contexts,
    # so the more specific "Found N error" and "0 errors" must match first.
    ("lint-fail", [r"Found [0-9]+ error"]),
    ("lint-pass", [r"All checks passed", r"0 errors"]),
    ("tests-pass", [r"[0-9]+ passed", r"tests? ok", "\u2713.*passed"]),
    ("tests-fail", [r"FAILED", r"AssertionError", r"ERRORS?\b"]),
    ("merge-conflict", [r"CONFLICT"]),
    ("git-push-ok", [r"Everything up-to-date", r"->.*main"]),
    ("git-commit", [r"^\[.+\] .+", r"^create mode"]),
    ("pr-created", [r"pull/[0-9]+", r"created pull request"]),
]


def classify_signal(exit_code: int | None, stdout: str) -> str | None:
    """Classify a bash command's output into a signal token.

    Returns a signal name like ``"tests-pass"`` or None if no pattern
    matches.
    """
    import re

    # Truncate to prevent regex DoS on large outputs
    text = stdout[:500]

    for signal, patterns in _SIGNAL_PATTERNS:
        for pattern in patterns:
            if re.search(pattern, text, re.IGNORECASE | re.MULTILINE):
                return signal

    # Generic failure if nothing matched but exit code is non-zero
    if exit_code is not None and exit_code != 0:
        return "cmd-fail"

    return None


def handle_post_bash(data: dict[str, object], config_path: Path) -> None:
    """Accumulate vibe signals from Bash tool execution.

    Appends a signal token to ``vibe_signals`` in ``.vox/config.md``.

    .. todo:: Signal accumulation should move to the MCP server so hooks
       don't write to config. Kept here as the one exception to preserve
       auto-vibe detection until a proper communication channel exists.
    """
    tool_response = data.get("tool_response", {})
    if not isinstance(tool_response, dict):
        return

    exit_code_raw = tool_response.get("exit_code")  # pyright: ignore[reportUnknownVariableType, reportUnknownMemberType]
    exit_code: int | None = None
    if isinstance(exit_code_raw, int):
        exit_code = exit_code_raw
    elif isinstance(exit_code_raw, str):
        with contextlib.suppress(ValueError):
            exit_code = int(exit_code_raw)

    stdout = tool_response.get("stdout", "")  # pyright: ignore[reportUnknownVariableType, reportUnknownMemberType]
    if not isinstance(stdout, str):
        stdout = str(stdout)  # pyright: ignore[reportUnknownArgumentType]

    signal = classify_signal(exit_code, stdout)
    if signal is None:
        return

    from datetime import datetime

    timestamp = datetime.now().strftime("%H:%M")
    token = f"{signal}@{timestamp}"

    current = read_config(config_path).vibe_signals or ""
    new_signals = f"{current},{token}" if current else token

    parts = new_signals.split(",")
    if len(parts) > MAX_VIBE_SIGNALS:
        new_signals = ",".join(parts[-MAX_VIBE_SIGNALS:])

    write_field("vibe_signals", new_signals, config_path)


# ---------------------------------------------------------------------------
# Notification — permission/idle prompt audio alerts
# ---------------------------------------------------------------------------


def _pick_notification_phrase(notification_type: str, message: str) -> str:
    """Pick a phrase for a notification type."""
    if notification_type == "permission_prompt":
        return random.choice(PERMISSION_PHRASES)
    if notification_type == "idle_prompt":
        return random.choice(IDLE_PHRASES)
    return f"Notification: {message[:80]}"


def handle_notification(data: dict[str, object], config: VoxConfig) -> None:
    """Handle permission/idle prompt notifications.

    In chime mode, plays a chime via voxd. In voice mode, synthesizes
    and plays a short spoken phrase via voxd.
    """
    # Not enabled
    if config.notify == "n":
        logger.info("Notification hook: skip (notify=n)")
        return

    notification_type = data.get("notification_type", "unknown")
    if not isinstance(notification_type, str):
        notification_type = "unknown"

    message = data.get("message", "Needs your attention")
    if not isinstance(message, str):
        message = "Needs your attention"

    logger.info("Notification hook: type=%s", notification_type)

    # Chime mode
    if config.speak == "n":
        logger.info("Notification hook: chime mode, requesting prompt chime from voxd")
        _chime_via_voxd("prompt")
        return

    # Voice mode: synthesize via voxd
    text = _pick_notification_phrase(notification_type, message)
    _speak_via_voxd(text, config)


# ---------------------------------------------------------------------------
# Shared speech helper — used by continuous mode hooks
# ---------------------------------------------------------------------------


def _speak_phrase(
    phrases: tuple[str, ...] | list[str],
    config: VoxConfig,
    *,
    chime_signal: str = "done",
) -> None:
    """Pick a random phrase and speak or chime it via voxd.

    Common pattern for async continuous-mode hooks: check speak mode,
    play a chime or synthesize speech.
    """
    if config.speak == "n":
        _chime_via_voxd(chime_signal)
        return

    text = random.choice(phrases)
    _speak_via_voxd(text, config)


# ---------------------------------------------------------------------------
# PreCompact — playful 'be right back' before context compaction
# ---------------------------------------------------------------------------


def handle_pre_compact(config: VoxConfig) -> None:
    """Play a playful message before context compaction.

    Only fires in continuous mode (notify=c). In on-demand (y) or
    off (n), compaction happens silently.
    """
    if config.notify != "c":
        logger.info("PreCompact hook: skip (notify=%s, not continuous)", config.notify)
        return

    logger.info("PreCompact hook: speaking")
    _speak_phrase(PRE_COMPACT_PHRASES, config, chime_signal="compact")


# ---------------------------------------------------------------------------
# UserPromptSubmit — acknowledgment in continuous mode
# ---------------------------------------------------------------------------


def handle_user_prompt_submit(config: VoxConfig) -> None:
    """Speak a short acknowledgment when the user submits a prompt.

    Only fires in continuous mode (notify=c).  Async — does not block
    prompt processing.
    """
    if config.notify != "c":
        logger.info(
            "UserPromptSubmit hook: skip (notify=%s, not continuous)",
            config.notify,
        )
        return

    logger.info("UserPromptSubmit hook: acknowledging")
    _speak_phrase(ACKNOWLEDGE_PHRASES, config, chime_signal="acknowledge")


# ---------------------------------------------------------------------------
# SubagentStart / SubagentStop — continuous mode announcements
# ---------------------------------------------------------------------------


def handle_subagent_start(config: VoxConfig) -> None:
    """Announce that a subagent is being spawned.

    Only fires in continuous mode (notify=c).  Async.
    """
    if config.notify != "c":
        logger.info(
            "SubagentStart hook: skip (notify=%s, not continuous)",
            config.notify,
        )
        return

    logger.info("SubagentStart hook: announcing")
    _speak_phrase(SUBAGENT_START_PHRASES, config, chime_signal="subagent")


def handle_subagent_stop(config: VoxConfig) -> None:
    """Announce that a subagent has completed.

    Only fires in continuous mode (notify=c).  Async.
    """
    if config.notify != "c":
        logger.info(
            "SubagentStop hook: skip (notify=%s, not continuous)",
            config.notify,
        )
        return

    logger.info("SubagentStop hook: announcing")
    _speak_phrase(SUBAGENT_STOP_PHRASES, config, chime_signal="subagent")


# ---------------------------------------------------------------------------
# SessionEnd — farewell speech
# ---------------------------------------------------------------------------


def handle_session_end(config: VoxConfig, config_path: Path) -> None:
    """Speak a farewell and clean up session state.

    Fires when notify != 'n' (both on-demand and continuous).
    Clears vibe_signals to prevent stale signals leaking to the
    next session.
    """
    if config.notify == "n":
        logger.info("SessionEnd hook: skip (notify=n)")
        return

    logger.info("SessionEnd hook: farewell")
    _speak_phrase(FAREWELL_PHRASES, config, chime_signal="farewell")

    # Clean slate for next session
    if config.vibe_signals:
        write_field("vibe_signals", "", config_path)


# ---------------------------------------------------------------------------
# CLI commands
# ---------------------------------------------------------------------------


@hook_app.command("stop")
def stop_cmd() -> None:  # pyright: ignore[reportUnusedFunction]
    """Stop hook: task-completion notification."""
    config_path = find_config() or DEFAULT_CONFIG_PATH
    if not config_path.exists():
        return

    config = read_config(config_path)
    data = _read_hook_input()
    result = handle_stop(data, config)
    if result is not None:
        _emit(result)


@hook_app.command("post-bash")
def post_bash_cmd() -> None:  # pyright: ignore[reportUnusedFunction]
    """PostToolUse hook: accumulate vibe signals from Bash."""
    config_path = find_config() or DEFAULT_CONFIG_PATH
    if not config_path.exists():
        return

    data = _read_hook_input()
    handle_post_bash(data, config_path)


@hook_app.command("notification")
def notification_cmd() -> None:  # pyright: ignore[reportUnusedFunction]
    """Notification hook: permission/idle prompt audio alerts."""
    config_path = find_config() or DEFAULT_CONFIG_PATH
    if not config_path.exists():
        return

    config = read_config(config_path)
    data = _read_hook_input()
    handle_notification(data, config)


@hook_app.command("pre-compact")
def pre_compact_cmd() -> None:  # pyright: ignore[reportUnusedFunction]
    """PreCompact hook: playful 'be right back' message."""
    config_path = find_config() or DEFAULT_CONFIG_PATH
    if not config_path.exists():
        return

    config = read_config(config_path)
    handle_pre_compact(config)


@hook_app.command("user-prompt-submit")
def user_prompt_submit_cmd() -> None:  # pyright: ignore[reportUnusedFunction]
    """UserPromptSubmit hook: acknowledgment in continuous mode."""
    config_path = find_config() or DEFAULT_CONFIG_PATH
    if not config_path.exists():
        return

    config = read_config(config_path)
    handle_user_prompt_submit(config)


@hook_app.command("subagent-start")
def subagent_start_cmd() -> None:  # pyright: ignore[reportUnusedFunction]
    """SubagentStart hook: announce subagent spawn."""
    config_path = find_config() or DEFAULT_CONFIG_PATH
    if not config_path.exists():
        return

    config = read_config(config_path)
    handle_subagent_start(config)


@hook_app.command("subagent-stop")
def subagent_stop_cmd() -> None:  # pyright: ignore[reportUnusedFunction]
    """SubagentStop hook: announce subagent completion."""
    config_path = find_config() or DEFAULT_CONFIG_PATH
    if not config_path.exists():
        return

    config = read_config(config_path)
    handle_subagent_stop(config)


@hook_app.command("session-end")
def session_end_cmd() -> None:  # pyright: ignore[reportUnusedFunction]
    """SessionEnd hook: farewell speech."""
    config_path = find_config() or DEFAULT_CONFIG_PATH
    if not config_path.exists():
        return

    config = read_config(config_path)
    handle_session_end(config, config_path)
