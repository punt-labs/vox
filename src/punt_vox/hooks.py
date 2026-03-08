"""Hook dispatchers for Claude Code events.

Thin shell scripts read stdin and delegate to ``vox hook <event>``.
All business logic lives here as testable pure functions.

Events:
- **stop**: task-completion notification (decision-block pattern)
- **post-bash**: signal accumulator for auto-vibe
- **notification**: permission/idle prompt audio alerts
- **pre-compact**: playful 'be right back' before context compaction
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import random
import subprocess
import sys
from pathlib import Path

import typer

from punt_vox.config import (
    VoxConfig,
    read_config,
    resolve_config_path,
    write_field,
    write_fields,
)
from punt_vox.mood import classify_mood

logger = logging.getLogger(__name__)

hook_app = typer.Typer(
    help="Hook dispatchers (called by hook scripts).",
    no_args_is_help=True,
)

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _resolve_assets_dir() -> Path:
    """Resolve the plugin assets directory.

    Uses ``CLAUDE_PLUGIN_ROOT`` (set by Claude Code when running hooks)
    for pip-installed packages.  Falls back to source-tree-relative path
    for local development.
    """
    plugin_root = os.environ.get("CLAUDE_PLUGIN_ROOT")
    if plugin_root:
        return Path(plugin_root) / "assets"
    return Path(__file__).resolve().parent.parent.parent / "assets"


def _read_hook_input() -> dict[str, object]:
    """Read JSON hook payload from stdin."""
    raw = sys.stdin.read()
    if not raw.strip():
        return {}
    try:
        data: object = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    if not isinstance(data, dict):
        return {}
    return dict(data)  # pyright: ignore[reportUnknownArgumentType]


def _emit(output: dict[str, object]) -> None:
    """Write JSON response to stdout."""
    typer.echo(json.dumps(output))


# ---------------------------------------------------------------------------
# Chime resolution
# ---------------------------------------------------------------------------


def resolve_chime(signal: str, vibe: str | None) -> Path:
    """Resolve mood-aware chime path for a signal.

    Fallback chain: mood-specific signal -> neutral signal ->
    mood-specific done -> done.
    """
    assets = _resolve_assets_dir()
    mood = classify_mood(vibe)
    # Asset filenames use underscores (chime_tests_pass.mp3) but
    # signal tokens use hyphens (tests-pass).
    file_signal = signal.replace("-", "_")

    if mood != "neutral":
        mood_file = assets / f"chime_{file_signal}_{mood}.mp3"
        if mood_file.exists():
            return mood_file

    neutral_file = assets / f"chime_{file_signal}.mp3"
    if neutral_file.exists():
        return neutral_file

    if mood != "neutral":
        mood_done = assets / f"chime_done_{mood}.mp3"
        if mood_done.exists():
            return mood_done

    return assets / "chime_done.mp3"


# ---------------------------------------------------------------------------
# Audio helpers
# ---------------------------------------------------------------------------


def _enqueue_audio(path: Path) -> None:
    """Play audio via flock-serialized queue (non-blocking)."""
    logger.info("Hook enqueue: %s (pid=%d)", path.name, os.getpid())
    try:
        subprocess.Popen(
            ["vox", "play", str(path)],
            start_new_session=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except FileNotFoundError:
        logger.warning("vox binary not found, skipping audio")


# ---------------------------------------------------------------------------
# Stop handler — decision-block pattern
# ---------------------------------------------------------------------------

STOP_PHRASES = [
    "\u266a Speaking my thoughts...",
    "\u266a Putting my thoughts into words...",
    "\u266a Summing it up aloud...",
    "\u266a Saying my piece...",
    "\u266a Voicing my closing remarks...",
    "\u266a Letting you hear how it went...",
    "\u266a Telling you what I did...",
]


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

    # Chime mode: play chime, let Claude stop
    if config.speak == "n":
        chime = resolve_chime("done", config.vibe)
        if chime.exists():
            logger.info("Stop hook: chime mode, playing %s", chime.name)
            _enqueue_audio(chime)
        else:
            logger.info("Stop hook: chime mode, missing %s", chime.name)
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
        config_path = resolve_config_path()
        write_fields({"vibe_tags": tags, "vibe_signals": ""}, config_path)
    return {"decision": "block", "reason": phrase}


# ---------------------------------------------------------------------------
# PostToolUse Bash — signal accumulator
# ---------------------------------------------------------------------------

_SIGNAL_PATTERNS: list[tuple[str, list[str]]] = [
    # Lint patterns before tests — "errors" appears in both contexts,
    # so the more specific "Found N error" and "0 errors" must match first.
    ("lint-fail", [r"Found [0-9]+ error"]),
    ("lint-pass", [r"0 errors"]),
    ("tests-pass", [r"passed", r"tests? ok", "\u2713.*passed"]),
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

    write_field("vibe_signals", new_signals, config_path)


# ---------------------------------------------------------------------------
# Notification — permission/idle prompt audio alerts
# ---------------------------------------------------------------------------

PERMISSION_PHRASES = [
    "Needs your approval.",
    "Quick approval needed.",
    "Need a green light here.",
    "Got a question for you.",
    "Your call on this one.",
    "Mind taking a look?",
    "Waiting on your go-ahead.",
]

IDLE_PHRASES = [
    "Waiting for your input.",
    "Ready when you are.",
    "Over to you.",
    "Standing by.",
    "Your turn.",
    "What do you think?",
    "Need your thoughts on this.",
]


def _pick_notification_phrase(notification_type: str, message: str) -> str:
    """Pick a phrase for a notification type."""
    if notification_type == "permission_prompt":
        return random.choice(PERMISSION_PHRASES)
    if notification_type == "idle_prompt":
        return random.choice(IDLE_PHRASES)
    return f"Notification: {message[:80]}"


def handle_notification(data: dict[str, object], config: VoxConfig) -> None:
    """Handle permission/idle prompt notifications.

    In chime mode, plays a chime. In voice mode, synthesizes and plays
    a short spoken phrase.
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
        chime = resolve_chime("prompt", config.vibe)
        if chime.exists():
            logger.info("Notification hook: chime mode, playing %s", chime.name)
            _enqueue_audio(chime)
        else:
            logger.info("Notification hook: chime mode, missing %s", chime.name)
        return

    # Voice mode: synthesize and play via vox unmute (ephemeral mode)
    text = _pick_notification_phrase(notification_type, message)

    voice_args: list[str] = []
    if config.voice:
        voice_args = ["--voice", config.voice]

    with contextlib.suppress(FileNotFoundError, subprocess.TimeoutExpired):
        subprocess.run(
            ["vox", "--json", "unmute", text, *voice_args],
            check=False,
            capture_output=True,
            timeout=30,
        )


# ---------------------------------------------------------------------------
# PreCompact — playful 'be right back' before context compaction
# ---------------------------------------------------------------------------

PRE_COMPACT_PHRASES = [
    "Grabbing a snack, be right back.",
    "Quick bathroom break, one sec.",
    "Stretching my legs for a moment.",
    "Hold that thought — reorganizing my notes.",
    "Tidying up my desk, back in a flash.",
    "Refilling my coffee, don't go anywhere.",
    "Let me gather my thoughts real quick.",
]


def handle_pre_compact(config: VoxConfig) -> None:
    """Play a playful message before context compaction.

    Only fires in continuous mode (notify=c). In on-demand (y) or
    off (n), compaction happens silently.
    """
    if config.notify != "c":
        logger.info("PreCompact hook: skip (notify=%s, not continuous)", config.notify)
        return

    # Chime mode: play chime, no speech
    if config.speak == "n":
        chime = resolve_chime("compact", config.vibe)
        if chime.exists():
            logger.info("PreCompact hook: chime mode, playing %s", chime.name)
            _enqueue_audio(chime)
        else:
            logger.info("PreCompact hook: chime mode, missing %s", chime.name)
        return

    # Voice mode: speak a playful phrase
    text = random.choice(PRE_COMPACT_PHRASES)
    logger.info("PreCompact hook: speaking '%s'", text)

    voice_args: list[str] = []
    if config.voice:
        voice_args = ["--voice", config.voice]

    with contextlib.suppress(FileNotFoundError, subprocess.TimeoutExpired):
        subprocess.run(
            ["vox", "--json", "unmute", text, *voice_args],
            check=False,
            capture_output=True,
            timeout=30,
        )


# ---------------------------------------------------------------------------
# CLI commands
# ---------------------------------------------------------------------------


@hook_app.command("stop")
def stop_cmd() -> None:  # pyright: ignore[reportUnusedFunction]
    """Stop hook: task-completion notification."""
    config_path = resolve_config_path()
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
    config_path = resolve_config_path()
    if not config_path.exists():
        return

    data = _read_hook_input()
    handle_post_bash(data, config_path)


@hook_app.command("notification")
def notification_cmd() -> None:  # pyright: ignore[reportUnusedFunction]
    """Notification hook: permission/idle prompt audio alerts."""
    config_path = resolve_config_path()
    if not config_path.exists():
        return

    config = read_config(config_path)
    data = _read_hook_input()
    handle_notification(data, config)


@hook_app.command("pre-compact")
def pre_compact_cmd() -> None:  # pyright: ignore[reportUnusedFunction]
    """PreCompact hook: playful 'be right back' message."""
    config_path = resolve_config_path()
    if not config_path.exists():
        return

    config = read_config(config_path)
    _read_hook_input()  # drain stdin to avoid pipe backpressure
    handle_pre_compact(config)
