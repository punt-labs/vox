"""Hook dispatchers for Claude Code events.

Thin shell scripts read stdin and delegate to ``vox hook <event>``.
All business logic lives here as testable pure functions.

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

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _resolve_assets_dir() -> Path:
    """Resolve the plugin assets directory.

    Uses ``CLAUDE_PLUGIN_ROOT`` (set by Claude Code when running hooks)
    first.  Falls back to the ``assets/`` subpackage next to this file,
    which works for both editable and installed packages.
    """
    plugin_root = os.environ.get("CLAUDE_PLUGIN_ROOT")
    if plugin_root:
        candidate = Path(plugin_root) / "assets"
        if candidate.is_dir():
            return candidate
    return Path(__file__).resolve().parent / "assets"


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

    # Voice mode: synthesize and play
    text = _pick_notification_phrase(notification_type, message)
    if notification_type in ("permission_prompt", "idle_prompt"):
        # Known quip pool — safe to cache
        _speak_with_cache(text, config)
    else:
        # Dynamic text from notification message — bypass cache
        _speak_uncached(text, config)


# ---------------------------------------------------------------------------
# Cached speech helper
# ---------------------------------------------------------------------------


def _speak_with_cache(text: str, config: VoxConfig) -> None:
    """Synthesize and play a phrase, using the MP3 cache when possible.

    On cache hit: plays the cached file directly via ``_enqueue_audio`` —
    no subprocess, no API call.

    On cache miss: runs ``vox --json unmute`` which handles both synthesis
    and playback internally (its ``enqueue()`` call spawns a detached
    player).  The result is then copied to cache for future hits.
    ``_enqueue_audio`` is intentionally NOT called on miss — the subprocess
    already enqueued playback.

    Cache I/O failures (``OSError``) are caught so a broken cache never
    prevents speech.
    """
    from punt_vox.cache import cache_get, cache_put
    from punt_vox.providers import auto_detect_provider

    voice = config.voice
    # Always resolve to an actual provider name so cache keys are
    # stable regardless of whether config.provider is set or auto-detected.
    provider = config.provider or auto_detect_provider()

    # Cache hit — play directly, skip subprocess entirely
    try:
        cached = cache_get(text, voice, provider)
        if cached is not None:
            logger.debug("Cache hit for %r, playing %s", text, cached.name)
            _enqueue_audio(cached)
            return
    except OSError:
        logger.debug("Cache lookup failed, falling through to synthesis", exc_info=True)

    # Cache miss — synthesize via subprocess (subprocess owns playback).
    # Pass --provider so the subprocess uses the same provider as the cache key.
    extra_args: list[str] = ["--provider", provider]
    if voice:
        extra_args.extend(["--voice", voice])

    try:
        result = subprocess.run(
            ["vox", "--json", "unmute", text, *extra_args],
            check=False,
            capture_output=True,
            timeout=30,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return

    # Populate cache from subprocess output.
    try:
        if result.returncode == 0 and result.stdout:
            data = json.loads(result.stdout)
            if isinstance(data, dict) and "path" in data:
                source = Path(str(data["path"]))  # pyright: ignore[reportUnknownArgumentType]
                cache_put(text, voice, provider, source)
    except (OSError, ValueError):
        logger.debug("Cache put failed", exc_info=True)


def _speak_uncached(text: str, config: VoxConfig) -> None:
    """Synthesize and play a phrase without caching.

    Used for dynamic text (e.g. unknown notification types) that should
    not pollute the cache.
    """
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
# Shared speech helper — used by continuous mode hooks
# ---------------------------------------------------------------------------


def _speak_phrase(
    phrases: tuple[str, ...] | list[str],
    config: VoxConfig,
    *,
    chime_signal: str = "done",
) -> None:
    """Pick a random phrase and speak or chime it.

    Common pattern for async continuous-mode hooks: check speak mode,
    play a chime or synthesize speech via ``vox unmute``.
    """
    if config.speak == "n":
        chime = resolve_chime(chime_signal, config.vibe)
        if chime.exists():
            _enqueue_audio(chime)
        return

    text = random.choice(phrases)
    _speak_with_cache(text, config)


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
    handle_pre_compact(config)


@hook_app.command("user-prompt-submit")
def user_prompt_submit_cmd() -> None:  # pyright: ignore[reportUnusedFunction]
    """UserPromptSubmit hook: acknowledgment in continuous mode."""
    config_path = resolve_config_path()
    if not config_path.exists():
        return

    config = read_config(config_path)
    handle_user_prompt_submit(config)


@hook_app.command("subagent-start")
def subagent_start_cmd() -> None:  # pyright: ignore[reportUnusedFunction]
    """SubagentStart hook: announce subagent spawn."""
    config_path = resolve_config_path()
    if not config_path.exists():
        return

    config = read_config(config_path)
    handle_subagent_start(config)


@hook_app.command("subagent-stop")
def subagent_stop_cmd() -> None:  # pyright: ignore[reportUnusedFunction]
    """SubagentStop hook: announce subagent completion."""
    config_path = resolve_config_path()
    if not config_path.exists():
        return

    config = read_config(config_path)
    handle_subagent_stop(config)


@hook_app.command("session-end")
def session_end_cmd() -> None:  # pyright: ignore[reportUnusedFunction]
    """SessionEnd hook: farewell speech."""
    config_path = resolve_config_path()
    if not config_path.exists():
        return

    config = read_config(config_path)
    handle_session_end(config, config_path)
