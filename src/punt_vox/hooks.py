"""Hook dispatchers for Claude Code events.

Thin shell scripts read stdin and delegate to ``vox hook <event>``.
All business logic lives here as testable pure functions.

Events:
- **stop**: task-completion notification (decision-block pattern)
- **post-bash**: signal accumulator for auto-vibe
- **notification**: permission/idle prompt audio alerts
"""

from __future__ import annotations

import contextlib
import json
import logging
import random
import subprocess
import sys
from pathlib import Path

import typer

from punt_vox.config import VoxConfig, read_config, resolve_config_path, write_field
from punt_vox.mood import classify_mood

logger = logging.getLogger(__name__)

hook_app = typer.Typer(
    help="Hook dispatchers (called by hook scripts).",
    no_args_is_help=True,
)

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

_ASSETS_DIR = Path(__file__).resolve().parent.parent.parent / "assets"


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
    mood = classify_mood(vibe)

    if mood != "neutral":
        mood_file = _ASSETS_DIR / f"chime_{signal}_{mood}.mp3"
        if mood_file.exists():
            return mood_file

    neutral_file = _ASSETS_DIR / f"chime_{signal}.mp3"
    if neutral_file.exists():
        return neutral_file

    if mood != "neutral":
        mood_done = _ASSETS_DIR / f"chime_done_{mood}.mp3"
        if mood_done.exists():
            return mood_done

    return _ASSETS_DIR / "chime_done.mp3"


# ---------------------------------------------------------------------------
# Audio helpers
# ---------------------------------------------------------------------------


def _enqueue_audio(path: Path) -> None:
    """Play audio via flock-serialized queue (non-blocking)."""
    try:
        subprocess.Popen(
            ["vox", "play", str(path)],
            start_new_session=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except FileNotFoundError:
        logger.debug("vox binary not found, skipping audio")


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


def handle_stop(data: dict[str, object], config: VoxConfig) -> dict[str, object] | None:
    """Decide whether to block Claude from stopping.

    Returns a decision-block dict if Claude should speak a summary,
    or None to let it stop normally.
    """
    # Not enabled
    if config.notify == "n":
        return None

    # Already continuing from a previous Stop hook — prevent infinite loop
    stop_active = data.get("stop_hook_active", False)
    if stop_active is True:
        return None

    # No signals = no meaningful work to summarize
    if not config.vibe_signals:
        return None

    # Chime mode: play chime, let Claude stop
    if config.speak == "n":
        chime = resolve_chime("done", config.vibe)
        if chime.exists():
            _enqueue_audio(chime)
        return None

    # Voice mode: block the stop, ask Claude to summarize and speak
    reason = random.choice(STOP_PHRASES)
    return {"decision": "block", "reason": reason}


# ---------------------------------------------------------------------------
# PostToolUse Bash — signal accumulator
# ---------------------------------------------------------------------------

_SIGNAL_PATTERNS: list[tuple[str, list[str]]] = [
    # Lint patterns before tests — "errors" appears in both contexts,
    # so the more specific "Found N error" and "0 errors" must match first.
    ("lint-fail", [r"Found [0-9]+ error"]),
    ("lint-pass", [r"0 errors"]),
    ("tests-pass", [r"passed", r"tests? ok", r"\u2713.*passed"]),
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
            if re.search(pattern, text, re.IGNORECASE):
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
    elif isinstance(exit_code_raw, str) and exit_code_raw.isdigit():
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
        return

    notification_type = data.get("notification_type", "unknown")
    if not isinstance(notification_type, str):
        notification_type = "unknown"

    message = data.get("message", "Needs your attention")
    if not isinstance(message, str):
        message = "Needs your attention"

    # Chime mode
    if config.speak == "n":
        chime = resolve_chime("prompt", config.vibe)
        if chime.exists():
            _enqueue_audio(chime)
        return

    # Voice mode: synthesize and play via vox unmute (ephemeral mode)
    text = _pick_notification_phrase(notification_type, message)

    voice_args: list[str] = []
    if config.voice:
        voice_args = ["--voice", config.voice]

    with contextlib.suppress(FileNotFoundError, subprocess.TimeoutExpired):
        subprocess.run(
            ["vox", "unmute", text, *voice_args, "--json"],
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
