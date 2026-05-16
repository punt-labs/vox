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

import json
import logging
import os
import random
import re
import select
import sys
from pathlib import Path
from typing import cast

import typer

from punt_vox.client import VoxClientSync, VoxdConnectionError, VoxdProtocolError
from punt_vox.config import (
    VoxConfig,
    read_config,
    write_field,
    write_fields,
)
from punt_vox.dirs import find_config_dir
from punt_vox.hook_payload import (
    BashPayload,
    NotificationPayload,
    StopPayload,
    parse_hook_payload,
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
from punt_vox.signal import Signal, SignalLog

logger = logging.getLogger(__name__)

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
    except (json.JSONDecodeError, OSError, UnicodeDecodeError):
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
    except (VoxdConnectionError, VoxdProtocolError):
        logger.warning("voxd not running, skipping speech")


def _chime_via_voxd(signal: str, *, wait: bool = True) -> None:
    """Play a chime via voxd.

    When *wait* is False, spawns a detached subprocess to avoid blocking
    the hook.  Used by sync hooks (Stop) that must return quickly.

    Catches ``VoxdConnectionError`` and ``VoxdProtocolError`` so a
    missing or misbehaving daemon never crashes a hook.
    """
    if not wait:
        # Fire-and-forget: spawn a detached process so the hook returns
        # immediately.  Per hooks.md §4: side-effect hooks must not block.
        import subprocess as _sp

        try:
            _sp.Popen(
                [sys.executable, "-m", "punt_vox", "hook", "_chime", signal],
                stdout=_sp.DEVNULL,
                stderr=_sp.DEVNULL,
                start_new_session=True,
            )
        except OSError:
            logger.warning("Could not spawn chime subprocess")
        return
    try:
        client = _make_client()
        client.chime(signal)
    except (VoxdConnectionError, VoxdProtocolError):
        logger.warning("voxd not running, skipping chime")


# ---------------------------------------------------------------------------
# Stop handler — decision-block pattern
# ---------------------------------------------------------------------------


def handle_stop(payload: StopPayload, config: VoxConfig) -> dict[str, object] | None:
    """Decide whether to block Claude from stopping.

    Returns a decision-block dict if Claude should speak a summary,
    or None to let it stop normally.
    """
    # Not enabled
    if config.notify == "n":
        logger.info("Stop hook: skip (notify=n)")
        return None

    # Already continuing from a previous Stop hook — prevent infinite loop
    if payload.stop_hook_active is True:
        logger.info("Stop hook: skip (stop_hook_active=True, preventing loop)")
        return None

    # No signals = no meaningful work to summarize
    if not config.vibe_signals:
        logger.info("Stop hook: skip (no vibe_signals)")
        return None

    # Chime mode: fire-and-forget chime, let Claude stop immediately.
    # Must not block — the Stop hook is sync and Claude waits.
    if config.speak == "n":
        logger.info("Stop hook: chime mode, requesting done chime from voxd")
        _chime_via_voxd("done", wait=False)
        return None

    # Voice mode: block the stop, ask Claude to summarize and speak.
    # Resolve tags and write to config so apply_vibe picks them up
    # automatically — no vibe tags or signals in the reason string.
    phrase = random.choice(STOP_PHRASES)
    if config.repo_name:
        phrase = f"{config.repo_name}. {phrase}"
    logger.info(
        "Stop hook: blocking for voice summary (signals=%s)",
        config.vibe_signals,
    )
    if config.vibe_mode == "off":
        pass  # User disabled vibe — don't write tags
    elif config.vibe_mode == "manual" and config.vibe_tags:
        pass  # Manual mode with existing tags — already set
    else:
        log = SignalLog.deserialize(config.vibe_signals or "")
        tags = log.resolve_tags()
        config_dir = find_config_dir()
        if config_dir is None:
            logger.warning(
                "Stop hook: config dir not found; vibe_tags not persisted. "
                "Run vox from inside a repo with .punt-labs/vox/ configured."
            )
        else:
            write_fields({"vibe_tags": tags, "vibe_signals": ""}, config_dir)
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


def handle_post_bash(payload: BashPayload, config_dir: Path) -> None:
    """Accumulate vibe signals from Bash tool execution.

    Appends a signal token to ``vibe_signals`` in ``vox.local.md``.

    .. todo:: Signal accumulation should move to the MCP server so hooks
       don't write to config. Kept here as the one exception to preserve
       auto-vibe detection until a proper communication channel exists.
    """
    signal = classify_signal(payload.exit_code, payload.stdout)
    if signal is None:
        return

    log = SignalLog.deserialize(read_config(config_dir).vibe_signals or "")
    log.append(Signal.now(signal))
    write_field("vibe_signals", log.serialize(), config_dir)


# ---------------------------------------------------------------------------
# Notification — permission/idle prompt audio alerts
# ---------------------------------------------------------------------------


def _pick_notification_phrase(
    notification_type: str,
    message: str,
    *,
    repo_name: str | None = None,
) -> str:
    """Pick a phrase for a notification type.

    When *repo_name* is set, prepends it with a period separator so
    the TTS engine inserts a natural pause.
    """
    if notification_type == "permission_prompt":
        text = random.choice(PERMISSION_PHRASES)
    elif notification_type == "idle_prompt":
        text = random.choice(IDLE_PHRASES)
    else:
        text = f"Notification: {message[:80]}"
    if repo_name:
        text = f"{repo_name}. {text}"
    return text


def handle_notification(payload: NotificationPayload, config: VoxConfig) -> None:
    """Handle permission/idle prompt notifications.

    In chime mode, plays a chime via voxd. In voice mode, synthesizes
    and plays a short spoken phrase via voxd.
    """
    # Not enabled
    if config.notify == "n":
        logger.info("Notification hook: skip (notify=n)")
        return

    logger.info("Notification hook: type=%s", payload.notification_type)

    # Chime mode
    if config.speak == "n":
        logger.info("Notification hook: chime mode, requesting prompt chime from voxd")
        _chime_via_voxd("prompt")
        return

    # Voice mode: synthesize via voxd
    text = _pick_notification_phrase(
        payload.notification_type, payload.message, repo_name=config.repo_name
    )
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
    play a chime or synthesize speech.  When ``config.repo_name`` is
    set, prepends it with a period separator so the TTS engine inserts
    a natural pause between the repo name and the phrase.
    """
    if config.speak == "n":
        _chime_via_voxd(chime_signal)
        return

    text = random.choice(phrases)
    if config.repo_name:
        text = f"{config.repo_name}. {text}"
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


def handle_session_end(config: VoxConfig, config_dir: Path) -> None:
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
        write_field("vibe_signals", "", config_dir)


# ---------------------------------------------------------------------------
# CLI commands
# ---------------------------------------------------------------------------


@hook_app.command("stop")
def stop_cmd() -> None:  # pyright: ignore[reportUnusedFunction]
    """Stop hook: task-completion notification."""
    config_dir = find_config_dir()
    if config_dir is None:
        return
    config = read_config(config_dir)
    data = _read_hook_input()
    stop_payload = cast("StopPayload", parse_hook_payload(data, "stop"))
    result = handle_stop(stop_payload, config)
    if result is not None:
        _emit(result)


@hook_app.command("post-bash")
def post_bash_cmd() -> None:  # pyright: ignore[reportUnusedFunction]
    """PostToolUse hook: accumulate vibe signals from Bash."""
    config_dir = find_config_dir()
    if config_dir is None:
        return
    data = _read_hook_input()
    bash_payload = cast("BashPayload", parse_hook_payload(data, "post_bash"))
    handle_post_bash(bash_payload, config_dir)


@hook_app.command("notification")
def notification_cmd() -> None:  # pyright: ignore[reportUnusedFunction]
    """Notification hook: permission/idle prompt audio alerts."""
    config_dir = find_config_dir()
    if config_dir is None:
        return
    config = read_config(config_dir)
    data = _read_hook_input()
    notif_payload = cast(
        "NotificationPayload", parse_hook_payload(data, "notification")
    )
    handle_notification(notif_payload, config)


@hook_app.command("pre-compact")
def pre_compact_cmd() -> None:  # pyright: ignore[reportUnusedFunction]
    """PreCompact hook: playful 'be right back' message."""
    config_dir = find_config_dir()
    if config_dir is None:
        return
    config = read_config(config_dir)
    handle_pre_compact(config)


@hook_app.command("user-prompt-submit")
def user_prompt_submit_cmd() -> None:  # pyright: ignore[reportUnusedFunction]
    """UserPromptSubmit hook: acknowledgment in continuous mode."""
    config_dir = find_config_dir()
    if config_dir is None:
        return
    config = read_config(config_dir)
    handle_user_prompt_submit(config)


@hook_app.command("subagent-start")
def subagent_start_cmd() -> None:  # pyright: ignore[reportUnusedFunction]
    """SubagentStart hook: announce subagent spawn."""
    config_dir = find_config_dir()
    if config_dir is None:
        return
    config = read_config(config_dir)
    handle_subagent_start(config)


@hook_app.command("subagent-stop")
def subagent_stop_cmd() -> None:  # pyright: ignore[reportUnusedFunction]
    """SubagentStop hook: announce subagent completion."""
    config_dir = find_config_dir()
    if config_dir is None:
        return
    config = read_config(config_dir)
    handle_subagent_stop(config)


@hook_app.command("session-end")
def session_end_cmd() -> None:  # pyright: ignore[reportUnusedFunction]
    """SessionEnd hook: farewell speech."""
    config_dir = find_config_dir()
    if config_dir is None:
        return
    config = read_config(config_dir)
    handle_session_end(config, config_dir)


@hook_app.command("_chime", hidden=True)
def chime_cmd(signal: str) -> None:  # pyright: ignore[reportUnusedFunction]
    """Internal: play a chime (used by fire-and-forget detached process)."""
    _chime_via_voxd(signal)
