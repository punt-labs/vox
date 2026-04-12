"""Typer CLI for punt-vox."""

from __future__ import annotations

import json
import logging
import os
import platform
import shlex
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Annotated

import click
import typer

from punt_vox import __version__
from punt_vox.client import VoxClientSync, VoxdConnectionError, VoxdProtocolError
from punt_vox.config import (
    DEFAULT_CONFIG_PATH,
    find_config,
    read_config,
    write_field,
    write_fields,
)
from punt_vox.hooks import hook_app
from punt_vox.normalize import normalize_for_speech
from punt_vox.output import default_output_dir
from punt_vox.paths import installed_version, log_dir
from punt_vox.providers import auto_detect_provider
from punt_vox.service import (
    _legacy_user_unit_path,  # pyright: ignore[reportPrivateUsage]
)

logger = logging.getLogger(__name__)

app = typer.Typer(
    name="vox",
    help="Text-to-speech CLI.",
    no_args_is_help=True,
    context_settings={"help_option_names": ["-h", "--help"]},
)
app.add_typer(hook_app, name="hook", hidden=True)

# ---------------------------------------------------------------------------
# cache subcommand group
# ---------------------------------------------------------------------------

cache_app = typer.Typer(
    help="Manage the MP3 quip cache.",
    no_args_is_help=True,
)
app.add_typer(cache_app, name="cache")

# ---------------------------------------------------------------------------
# Display constants
# ---------------------------------------------------------------------------

_PROVIDER_DISPLAY = {
    "elevenlabs": "ElevenLabs",
    "polly": "Polly",
    "openai": "OpenAI",
    "say": "Say",
    "espeak": "eSpeak",
}

# ---------------------------------------------------------------------------
# Global state
# ---------------------------------------------------------------------------

_json_output = False
_quiet_output = False


def _emit(payload: object, text: str) -> None:
    if _json_output:
        typer.echo(json.dumps(payload))
    elif not _quiet_output:
        typer.echo(text)


def _configure_logging(verbose: bool) -> None:
    from punt_vox.logging_config import configure_logging

    configure_logging(stderr_level="DEBUG" if verbose else "WARNING")


def _validate_voice_settings(
    stability: float | None,
    similarity: float | None,
    style: float | None,
) -> None:
    for name, value in [
        ("stability", stability),
        ("similarity", similarity),
        ("style", style),
    ]:
        if value is not None and not 0.0 <= value <= 1.0:
            msg = f"{name} must be between 0.0 and 1.0, got {value}"
            raise typer.BadParameter(msg)


# ---------------------------------------------------------------------------
# API key resolution
#
# The per-call API key feature exists for single-user billing isolation:
# one user holding multiple provider keys and wanting to attribute cost
# to a specific project on a specific call. The secret is forwarded to
# voxd over the local WebSocket and injected into the provider env for
# one synthesize request.
#
# Passing ``--api-key <value>`` literally on the command line exposes
# the value through ``ps`` (and, on Linux, ``/proc/<pid>/cmdline``),
# shell history, and terminal recordings. That's a real credential
# disclosure path even though voxd does not log or persist the key.
# Three safer input paths are supported; ``--api-key`` is retained
# for back-compat and demo use with a stderr warning when the value
# came from argv (not from the ``VOX_API_KEY`` env var).
#
# The four sources are mutually exclusive: specifying more than one
# raises ``typer.BadParameter``. Priority (only one may be set):
#   1. ``--api-key-file <path>``
#   2. ``--api-key-stdin``
#   3. ``VOX_API_KEY`` env var (or ``--api-key <value>`` — typer treats
#      both as populating the same ``api_key`` parameter; we distinguish
#      them via ``ctx.get_parameter_source`` to decide whether to warn)
# ---------------------------------------------------------------------------


_API_KEY_ARGV_WARNING = (
    "warning: --api-key on the command line is visible via 'ps' and "
    "shell history. Prefer VOX_API_KEY env var, --api-key-file <path>, "
    "or --api-key-stdin for real credentials."
)


def _read_api_key_file(path: Path) -> str:
    """Read a per-call API key from a file.

    Rejects missing paths, non-files, and empty files. Strips trailing
    whitespace and newlines. Warns (but does not fail) when the file
    has any group or other permission bits set, matching the advisory
    style used by the install path for ``keys.env`` permission
    handling.

    The check is ``mode & 0o077`` (any group or other bit), not
    ``mode & 0o004`` (the other-read bit only). On shared Unix systems
    a file at 0640 is readable by anyone in the owning group
    (``nobody``, ``www-data``, a shared-dev group, etc.) — the
    narrower check let that exposure slide silently. The only safe
    mode for a credential file is 0600. Copilot on PR #175.
    """
    if not path.is_file():
        msg = f"--api-key-file: {path} is not a file"
        raise typer.BadParameter(msg)
    mode = path.stat().st_mode
    if mode & 0o077:
        typer.echo(
            f"warning: --api-key-file: {path} is accessible to group "
            f"or other users (mode {oct(mode & 0o777)}). Run "
            f"'chmod 600 {path}' to tighten permissions.",
            err=True,
        )
    value = path.read_text(encoding="utf-8").strip()
    if not value:
        msg = f"--api-key-file: {path} is empty"
        raise typer.BadParameter(msg)
    return value


def _read_api_key_stdin() -> str:
    """Read a per-call API key from stdin (one line).

    Refuses to read when stdin is a tty — the user almost certainly
    meant to pipe the key in, and blocking on an interactive prompt
    would be a surprising default. Strips trailing whitespace and
    rejects empty input.
    """
    if sys.stdin.isatty():
        msg = "--api-key-stdin requires piped input (stdin is a tty)"
        raise typer.BadParameter(msg)
    line = sys.stdin.readline().strip()
    if not line:
        msg = "--api-key-stdin: received empty input"
        raise typer.BadParameter(msg)
    return line


def _resolve_api_key(
    ctx: typer.Context,
    api_key: str | None,
    api_key_file: Path | None,
    api_key_stdin: bool,
) -> str | None:
    """Resolve the per-call API key from the first configured source.

    Enforces mutual exclusion between ``--api-key-file``,
    ``--api-key-stdin``, and ``--api-key``/``VOX_API_KEY``. Fires a
    stderr warning when ``--api-key`` was passed literally on the
    command line (source == COMMANDLINE) because argv is visible to
    local process introspection and shell history. The env-var path
    (source == ENVIRONMENT) does not warn: while environment variables
    are not secret, they are generally less exposed to casual local
    observation than argv.

    Returns None when no source is configured — the call is anonymous
    and voxd falls back to the ambient ``keys.env`` value.
    """
    file_set = api_key_file is not None
    stdin_set = api_key_stdin
    argv_or_env_set = api_key is not None
    sources_set = int(file_set) + int(stdin_set) + int(argv_or_env_set)
    if sources_set > 1:
        named: list[str] = []
        if file_set:
            named.append("--api-key-file")
        if stdin_set:
            named.append("--api-key-stdin")
        if argv_or_env_set:
            # Distinguish argv vs env var so the error points at the
            # right input surface. Users piping a key via env var and
            # then also writing --api-key-file by mistake should see
            # "VOX_API_KEY", not "--api-key".
            source = ctx.get_parameter_source("api_key")
            if source is click.core.ParameterSource.ENVIRONMENT:
                named.append("VOX_API_KEY")
            else:
                named.append("--api-key")
        conflict = ", ".join(named)
        msg = (
            f"Specify at most one API key source; got {conflict}. "
            "These are mutually exclusive."
        )
        raise typer.BadParameter(msg)
    if api_key_file is not None:
        return _read_api_key_file(api_key_file)
    if api_key_stdin:
        return _read_api_key_stdin()
    if api_key is not None:
        source = ctx.get_parameter_source("api_key")
        if source is click.core.ParameterSource.COMMANDLINE:
            typer.echo(_API_KEY_ARGV_WARNING, err=True)
        return api_key
    return None


# ---------------------------------------------------------------------------
# Annotated type aliases for shared options
# ---------------------------------------------------------------------------

Verbose = Annotated[
    bool,
    typer.Option("--verbose", "-v", help="Enable debug logging."),
]
Quiet = Annotated[
    bool,
    typer.Option("--quiet", "-q", help="Suppress non-JSON output."),
]
JsonOutput = Annotated[
    bool,
    typer.Option("--json", help="Output JSON."),
]
ProviderOpt = Annotated[
    str | None,
    typer.Option(
        "--provider",
        envvar="TTS_PROVIDER",
        help=(
            "TTS provider (elevenlabs, polly, openai, say, espeak)."
            " Default: auto-detect."
        ),
    ),
]
ModelOpt = Annotated[
    str | None,
    typer.Option(
        "--model",
        envvar="TTS_MODEL",
        help="Model name (e.g. eleven_v3, tts-1). Provider-specific.",
    ),
]
VoiceOpt = Annotated[
    str | None,
    typer.Option("--voice", help="Voice name. Default: provider-specific."),
]
LanguageOpt = Annotated[
    str | None,
    typer.Option("--language", "--lang", help="ISO 639-1 language code (e.g. de, ko)."),
]
RateOpt = Annotated[
    int,
    typer.Option("--rate", help="Speech rate as percentage (e.g. 90 = 90%% speed)."),
]
OutputOpt = Annotated[
    Path | None,
    typer.Option("--output", "-o", help="Output file path."),
]
OutputDirOpt = Annotated[
    Path | None,
    typer.Option("--output-dir", "-d", help="Output directory. Default: ~/vox-output."),
]
StabilityOpt = Annotated[
    float | None,
    typer.Option("--stability", help="ElevenLabs voice stability (0.0-1.0)."),
]
SimilarityOpt = Annotated[
    float | None,
    typer.Option("--similarity", help="ElevenLabs voice similarity boost (0.0-1.0)."),
]
StyleOpt = Annotated[
    float | None,
    typer.Option("--style", help="ElevenLabs voice style/expressiveness (0.0-1.0)."),
]
SpeakerBoostFlag = Annotated[
    bool,
    typer.Option("--speaker-boost", help="Enable ElevenLabs speaker boost."),
]
OnceOpt = Annotated[
    int | None,
    typer.Option(
        "--once",
        help=(
            "Deduplicate identical text within N seconds. When set, voxd "
            "skips the play if the same text was played within the window "
            "(e.g. when multiple Claude Code sessions broadcast the same "
            "biff wall). Omit to play every time. Must be a positive "
            "integer when set."
        ),
    ),
]
ApiKeyOpt = Annotated[
    str | None,
    typer.Option(
        "--api-key",
        envvar="VOX_API_KEY",
        help=(
            "Per-call provider API key. Forwarded to voxd over the local "
            "WebSocket and used for this single synthesis request only. "
            "Lets a single user maintain multiple ElevenLabs/OpenAI keys "
            "for per-project billing attribution without juggling "
            "environment variables. Not persisted, not logged, never "
            "echoed to stdout. vox is single-user — this is cost-tracking, "
            "not multi-tenant isolation. Passing --api-key literally on "
            "the command line exposes the value via 'ps' and shell history; "
            "prefer VOX_API_KEY env var, --api-key-file, or --api-key-stdin "
            "for real credentials."
        ),
    ),
]
ApiKeyFileOpt = Annotated[
    Path | None,
    typer.Option(
        "--api-key-file",
        help=(
            "Read per-call provider API key from a file. Safer than "
            "--api-key on the command line because the value never "
            "appears in argv, shell history, or 'ps'. The file should "
            "be mode 0600; vox warns if any group or other permission "
            "bits are set. Empty files and non-files are rejected. "
            "Trailing whitespace and newlines are stripped."
        ),
    ),
]
ApiKeyStdinFlag = Annotated[
    bool,
    typer.Option(
        "--api-key-stdin",
        help=(
            "Read per-call provider API key from stdin (one line). "
            "Safer than --api-key on the command line because the "
            "value never appears in argv. Intended for piped input "
            "from a password manager, e.g. 'pass show vox/project | "
            "vox unmute ... --api-key-stdin'. Refuses to read from a "
            "tty."
        ),
    ),
]
FromOpt = Annotated[
    Path | None,
    typer.Option("--from", help="JSON file with segments array.", exists=True),
]
TextArg = Annotated[
    str | None, typer.Argument(help="Text to synthesize.", show_default=False)
]


# ---------------------------------------------------------------------------
# callback (global flags)
# ---------------------------------------------------------------------------


@app.callback()
def _callback(  # pyright: ignore[reportUnusedFunction]
    json_output: JsonOutput = False,
    verbose: Verbose = False,
    quiet: Quiet = False,
) -> None:
    """Text-to-speech CLI."""
    if verbose and quiet:
        raise typer.BadParameter("--verbose and --quiet are mutually exclusive.")
    global _json_output, _quiet_output
    _json_output = json_output
    _quiet_output = quiet
    _configure_logging(verbose)


# ---------------------------------------------------------------------------
# unmute — play audio
# ---------------------------------------------------------------------------


@app.command()
def unmute(  # pyright: ignore[reportUnusedFunction]
    ctx: typer.Context,
    text: TextArg = None,
    from_file: FromOpt = None,
    voice: VoiceOpt = None,
    language: LanguageOpt = None,
    rate: RateOpt = 90,
    provider: ProviderOpt = None,
    model: ModelOpt = None,
    stability: StabilityOpt = None,
    similarity: SimilarityOpt = None,
    style: StyleOpt = None,
    speaker_boost: SpeakerBoostFlag = False,
    once: OnceOpt = None,
    api_key: ApiKeyOpt = None,
    api_key_file: ApiKeyFileOpt = None,
    api_key_stdin: ApiKeyStdinFlag = False,
) -> None:
    """Synthesize and play audio via voxd."""
    _validate_voice_settings(stability, similarity, style)
    # Negative values are a user error. Zero is accepted and treated
    # as unset (no dedup) — matches the server-side semantics so the
    # two surfaces are consistent. Scripts can safely pass
    # ``--once ${ONCE_TTL:-0}`` as a default.
    if once is not None and once < 0:
        raise typer.BadParameter(
            "--once must be a non-negative integer (seconds). "
            "Use 0 or omit the flag to disable dedup."
        )
    if once == 0:
        once = None

    # Empty string from ``VOX_API_KEY=""`` or a literal ``--api-key ""``
    # is normalized to ``None`` so it does not shadow the mutual
    # exclusion rules in ``_resolve_api_key``. Real-world trigger: a CI
    # pipeline that exports ``VOX_API_KEY=""`` globally (because some
    # jobs use vox and others don't) would otherwise be unable to pass
    # ``--api-key-file`` or ``--api-key-stdin`` — typer hands the empty
    # env value to ``api_key``, and without this normalization the
    # mutual-exclusion check counts it as a fourth source. The
    # individual readers (``_read_api_key_file``, ``_read_api_key_stdin``)
    # still reject their own empty content with their own BadParameter
    # messages, so there is no silent fall-through for paths where
    # emptiness is actually a user error. Cursor Bugbot on PR #175.
    if api_key == "":
        api_key = None

    # Resolve the per-call API key from exactly one of the four
    # supported sources (file, stdin, env var, argv) and fire a
    # stderr warning when the argv path was used. See
    # ``_resolve_api_key`` for the full rationale.
    resolved_api_key = _resolve_api_key(ctx, api_key, api_key_file, api_key_stdin)

    segments = _resolve_text_segments(text, from_file)
    boost = speaker_boost if speaker_boost else None
    client = VoxClientSync()

    for seg_text in segments:
        seg_text = normalize_for_speech(seg_text)
        try:
            result = client.synthesize(
                seg_text,
                voice=voice,
                provider=provider,
                model=model,
                rate=rate,
                language=language,
                stability=stability,
                similarity=similarity,
                style=style,
                speaker_boost=boost,
                once=once,
                api_key=resolved_api_key,
            )
            payload: dict[str, object] = {"id": result.request_id}
            if result.deduped:
                payload["deduped"] = True
                if result.original_played_at is not None:
                    payload["original_played_at"] = result.original_played_at
                if result.ttl_seconds_remaining is not None:
                    payload["ttl_seconds_remaining"] = result.ttl_seconds_remaining
            _emit(payload, seg_text)
        except VoxdConnectionError as exc:
            typer.echo(f"Error: {exc}", err=True)
            raise typer.Exit(code=1) from exc
        except VoxdProtocolError as exc:
            typer.echo(f"Error: {exc}", err=True)
            raise typer.Exit(code=1) from exc


# ---------------------------------------------------------------------------
# record — save audio to file
# ---------------------------------------------------------------------------


@app.command()
def record(  # pyright: ignore[reportUnusedFunction]
    text: TextArg = None,
    from_file: FromOpt = None,
    voice: VoiceOpt = None,
    language: LanguageOpt = None,
    rate: RateOpt = 90,
    output: OutputOpt = None,
    output_dir: OutputDirOpt = None,
    provider: ProviderOpt = None,
    model: ModelOpt = None,
    stability: StabilityOpt = None,
    similarity: SimilarityOpt = None,
    style: StyleOpt = None,
    speaker_boost: SpeakerBoostFlag = False,
) -> None:
    """Synthesize and save audio to file via voxd."""
    from punt_vox.types import generate_filename

    _validate_voice_settings(stability, similarity, style)

    segments = _resolve_text_segments(text, from_file)
    boost = speaker_boost if speaker_boost else None
    out_dir = output_dir if output_dir is not None else default_output_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    client = VoxClientSync()

    for i, seg_text in enumerate(segments):
        seg_text = normalize_for_speech(seg_text)
        # Determine output path
        if output is not None and len(segments) == 1:
            out_path = output
        elif output is not None:
            # Multiple segments with explicit --output: append index
            stem = output.stem
            out_path = output.parent / f"{stem}_{i:04d}{output.suffix}"
        else:
            out_path = out_dir / generate_filename(seg_text)

        try:
            mp3_bytes = client.record(
                seg_text,
                voice=voice,
                provider=provider,
                model=model,
                rate=rate,
                language=language,
                stability=stability,
                similarity=similarity,
                style=style,
                speaker_boost=boost,
            )
        except VoxdConnectionError as exc:
            typer.echo(f"Error: {exc}", err=True)
            raise typer.Exit(code=1) from exc
        except VoxdProtocolError as exc:
            typer.echo(f"Error: {exc}", err=True)
            raise typer.Exit(code=1) from exc

        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(mp3_bytes)
        _emit({"path": str(out_path)}, str(out_path))


# ---------------------------------------------------------------------------
# Text segment resolution (shared by unmute and record)
# ---------------------------------------------------------------------------


def _resolve_text_segments(
    text: str | None,
    from_file: Path | None,
) -> list[str]:
    """Resolve text input into a list of segments.

    Accepts either a direct text argument or a JSON file with an array of
    strings or {text} objects. Per-segment voice override is available
    via the MCP ``unmute`` tool, not the CLI.
    """
    if from_file is not None:
        return _segments_from_file(from_file)

    if text is None:
        typer.echo("Error: provide TEXT argument or --from file.", err=True)
        raise typer.Exit(code=1)

    return [text]


def _segments_from_file(from_file: Path) -> list[str]:
    """Parse a JSON segments file into a list of text strings."""
    try:
        raw = json.loads(from_file.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise typer.BadParameter("--from file must contain valid JSON.") from exc

    if not isinstance(raw, list):
        raise typer.BadParameter("--from file must contain a JSON array.")

    segments: list[str] = []
    for i, item in enumerate(raw):  # pyright: ignore[reportUnknownVariableType, reportUnknownArgumentType]
        seg_text: str
        if isinstance(item, str):
            seg_text = item
        elif isinstance(item, dict):
            seg_text = str(item.get("text") or "")  # pyright: ignore[reportUnknownMemberType, reportUnknownArgumentType]
        else:
            raise typer.BadParameter(
                f"Element {i} must be a string or {{voice, text}} object."
            )

        if seg_text:
            segments.append(seg_text)
    return segments


# ---------------------------------------------------------------------------
# vibe — set session mood
# ---------------------------------------------------------------------------


@app.command("vibe")
def vibe_cmd(  # pyright: ignore[reportUnusedFunction]
    mood: Annotated[str, typer.Argument(help="Mood description or 'auto'/'off'.")],
) -> None:
    """Set session mood for TTS voice."""
    cp = find_config() or DEFAULT_CONFIG_PATH
    if mood == "auto":
        write_fields({"vibe_tags": "", "vibe": "", "vibe_mode": "auto"}, config_path=cp)
        _emit({"vibe_mode": "auto"}, "Vibe mode: auto")
    elif mood == "off":
        write_fields({"vibe_tags": "", "vibe": "", "vibe_mode": "off"}, config_path=cp)
        _emit({"vibe_mode": "off"}, "Vibe mode: off")
    else:
        write_fields(
            {"vibe": mood, "vibe_tags": "", "vibe_mode": "manual"}, config_path=cp
        )
        _emit({"vibe": mood, "vibe_mode": "manual"}, f"Vibe: {mood}")


# ---------------------------------------------------------------------------
# notify — notification mode (y/n/c)
# ---------------------------------------------------------------------------


@app.command("notify")
def notify_cmd(  # pyright: ignore[reportUnusedFunction]
    mode: Annotated[
        str,
        typer.Argument(help="Notification mode: y (on), n (off), c (continuous)."),
    ],
    voice: Annotated[
        str | None,
        typer.Option("--voice", help="Set session voice in the same call."),
    ] = None,
) -> None:
    """Set notification mode."""
    if mode not in ("y", "n", "c"):
        typer.echo("Error: mode must be y, n, or c.", err=True)
        raise typer.Exit(code=1)

    config_path = find_config() or DEFAULT_CONFIG_PATH
    first_init = not config_path.exists()
    updates: dict[str, str] = {"notify": mode}
    if mode == "c" or (first_init and mode == "y"):
        updates["speak"] = "y"
    if voice is not None:
        updates["voice"] = voice
    write_fields(updates, config_path=config_path)

    labels = {
        "y": "Notifications enabled.",
        "n": "Notifications disabled.",
        "c": "Continuous mode on.",
    }
    _emit(updates, labels[mode])


# ---------------------------------------------------------------------------
# speak — toggle spoken vs chime notifications (y/n)
# ---------------------------------------------------------------------------


@app.command("speak")
def speak_cmd(  # pyright: ignore[reportUnusedFunction]
    mode: Annotated[
        str,
        typer.Argument(help="Speak mode: y (voice) or n (chimes only)."),
    ],
) -> None:
    """Toggle spoken notifications on or off."""
    if mode not in ("y", "n"):
        typer.echo("Error: mode must be y or n.", err=True)
        raise typer.Exit(code=1)

    write_field("speak", mode, config_path=find_config() or DEFAULT_CONFIG_PATH)
    label = "Voice on." if mode == "y" else "Muted — chimes only."
    _emit({"speak": mode}, label)


# ---------------------------------------------------------------------------
# voice — set session voice
# ---------------------------------------------------------------------------


@app.command("voice")
def voice_cmd(  # pyright: ignore[reportUnusedFunction]
    name: Annotated[str, typer.Argument(help="Voice name (e.g. matilda, roger).")],
) -> None:
    """Set the session voice."""
    write_field("voice", name, config_path=find_config() or DEFAULT_CONFIG_PATH)
    _emit({"voice": name}, f"{name}'s here.")


# ---------------------------------------------------------------------------
# version
# ---------------------------------------------------------------------------


@app.command("version")
def version_cmd() -> None:  # pyright: ignore[reportUnusedFunction]
    """Print version."""
    _emit({"version": __version__}, f"vox {__version__}")


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------


@app.command("status")
def status_cmd() -> None:  # pyright: ignore[reportUnusedFunction]
    """Show current state (daemon, voice, vibe, notify)."""
    cfg = read_config(config_path=find_config() or DEFAULT_CONFIG_PATH)

    # Try to get provider from voxd health
    daemon_provider: str | None = None
    daemon_status = "not running"
    try:
        health = VoxClientSync().health()
        daemon_provider = str(health.get("provider", ""))
        daemon_status = "running"
    except (VoxdConnectionError, VoxdProtocolError):
        pass

    provider_name = daemon_provider or "unknown"
    display_name = _PROVIDER_DISPLAY.get(provider_name, provider_name)

    info: dict[str, str | None] = {
        "daemon": daemon_status,
        "provider": provider_name,
        "voice": cfg.voice or None,
        "notify": cfg.notify,
        "speak": cfg.speak,
        "vibe_mode": cfg.vibe_mode,
        "vibe": cfg.vibe,
        "vibe_tags": cfg.vibe_tags,
        "vibe_signals": cfg.vibe_signals,
    }

    text_lines = [
        f"Daemon:    {daemon_status}",
        f"Provider:  {display_name}",
        f"Voice:     {info['voice'] or '(default)'}",
        f"Notify:    {info['notify']}",
        f"Speak:     {info['speak']}",
        f"Vibe mode: {info['vibe_mode']}",
    ]
    if cfg.vibe:
        text_lines.append(f"Vibe:      {cfg.vibe}")
    if cfg.vibe_tags:
        text_lines.append(f"Tags:      {cfg.vibe_tags}")
    if cfg.vibe_signals:
        text_lines.append(f"Signals:   {cfg.vibe_signals}")
    _emit(info, "\n".join(text_lines))


# ---------------------------------------------------------------------------
# doctor
# ---------------------------------------------------------------------------

_PASS = "\u2713"
_FAIL = "\u2717"
_OPTIONAL = "\u25cb"
_WARN = "\u26a0"  # ⚠ — non-fatal diagnostic, exit code unchanged

# Machine-readable tri-state (+skip) for --json consumers that need to
# distinguish warnings from hard failures.  The existing ``passed`` bool
# is kept for back-compat; ``status_kind`` is the richer replacement.
_STATUS_KIND: dict[str, str] = {
    _PASS: "pass",
    _FAIL: "fail",
    _OPTIONAL: "skip",
    _WARN: "warn",
}


def _claude_desktop_config_path() -> Path:
    return (
        Path.home()
        / "Library"
        / "Application Support"
        / "Claude"
        / "claude_desktop_config.json"
    )


def _valid_vox_subcommands() -> set[str]:
    """Return the set of subcommand tokens the current CLI accepts.

    Includes both leaf commands (``doctor``, ``unmute``, ...) and
    subcommand groups (``daemon``, ``cache``, ``hook``) so a unit
    that references ``vox daemon`` or ``vox cache`` parses as valid.
    The typer app is the single source of truth — hard-coding a
    constant would drift the instant a command is renamed, which is
    exactly the failure mode vox-45r exposed.
    """
    leaf_names: set[str] = set()
    for command in app.registered_commands:
        # Typer derives the CLI token from the callback's __name__ when no
        # explicit name is set, converting underscores to hyphens (e.g.
        # ``def foo_bar`` becomes ``vox foo-bar``). Replicate that conversion
        # so the valid set matches the surface users actually type — without
        # it, a future anonymous command with an underscore would cause the
        # doctor regression check to reject a unit that references the real
        # hyphenated subcommand.
        raw_name = command.callback.__name__ if command.callback else None
        name = command.name or (raw_name.replace("_", "-") if raw_name else None)
        if name:
            leaf_names.add(name)
    group_names = {g.name for g in app.registered_groups if g.name}
    return leaf_names | group_names


def _parse_user_unit_execstart_subcommand(unit_path: Path) -> str | None:
    """Extract the first CLI subcommand token from a systemd unit file.

    Reads the unit file and uses the **first** ``ExecStart=`` line it
    finds. systemd unit grammar permits multiple ``ExecStart=`` entries
    for ``Type=oneshot`` services (the service manager runs all of
    them in order); the legacy ``vox.service`` this parser targets is
    ``Type=simple`` with exactly one ``ExecStart=``, so the first
    entry is sufficient to detect a stale subcommand reference.
    Shell-splits the remainder and looks for the first token that is
    not the binary path itself — that token is the ``vox`` subcommand
    (e.g. ``serve`` in ``/home/j/.local/bin/vox serve --port 8421``).

    Systemd unit grammar allows multi-line directive values via a
    trailing backslash on the continued line. Lines are pre-joined
    before the ``ExecStart=`` search so a unit written as::

        ExecStart=/home/j/.local/bin/vox \\
            serve --port 8421

    parses to the same token as the single-line form. Field probability
    is low for the stale-user-unit case, but the parser contract has to
    cover the full directive syntax or it silently returns "unparseable"
    for legitimate units.

    Returns None when:
    - the file cannot be read (permission, missing, non-UTF-8),
    - no ``ExecStart=`` line exists,
    - ``ExecStart=`` is present but empty or shell-unparseable,
    - the command line has only the binary with no subcommand.

    A None return is the signal to the caller that the unit is
    unparseable; doctor surfaces that separately from "references an
    unknown subcommand".
    """
    try:
        content = unit_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None

    # Fold systemd line continuations: any line whose trimmed form ends
    # in a single backslash is joined with the next line, separated by
    # a space. The backslash itself is stripped. Matches systemd.unit(5)
    # "New lines may be escaped by a backslash at the end of the line".
    joined_lines: list[str] = []
    buffer = ""
    for raw in content.splitlines():
        stripped = raw.rstrip()
        if stripped.endswith("\\"):
            buffer += stripped[:-1] + " "
            continue
        joined_lines.append(buffer + raw)
        buffer = ""
    if buffer:
        joined_lines.append(buffer)

    exec_line: str | None = None
    for raw in joined_lines:
        line = raw.strip()
        if line.startswith("ExecStart="):
            exec_line = line[len("ExecStart=") :].strip()
            break

    if not exec_line:
        return None

    # Systemd ExecStart lines may be prefixed with ``-``, ``@``, ``+``,
    # ``!``, or ``!!`` to adjust execution semantics. Strip them so the
    # remainder is the bare command.
    while exec_line[:1] in {"-", "@", "+", "!"}:
        exec_line = exec_line[1:].lstrip()

    if not exec_line:
        return None

    try:
        tokens = shlex.split(exec_line)
    except ValueError:
        return None

    # tokens[0] is the binary path; tokens[1] (if present) is the first
    # subcommand. Skip tokens that look like flags (start with ``-``)
    # to tolerate unit files that pass a global flag before the
    # subcommand, though the current vox CLI never emits one.
    for token in tokens[1:]:
        if not token.startswith("-"):
            return token
    return None


@app.command()
def doctor() -> None:
    """Check system health for vox."""
    passed = 0
    failed = 0
    warned = 0
    lines: list[str] = []
    checks: list[dict[str, object]] = []

    def _check(symbol: str, message: str, *, required: bool = True) -> None:
        nonlocal passed, failed, warned
        lines.append(f"{symbol} {message}")
        checks.append(
            {
                "status": symbol,
                "status_kind": _STATUS_KIND.get(symbol, "fail"),
                "message": message,
                "required": required,
                "passed": symbol == _PASS,
            }
        )
        if symbol == _PASS:
            passed += 1
        elif symbol == _FAIL and required:
            failed += 1
        elif symbol == _WARN:
            warned += 1

    # Python version
    v = sys.version_info
    if v >= (3, 13):
        _check(_PASS, f"Python {v.major}.{v.minor}.{v.micro}")
    else:
        _check(
            _FAIL,
            f"Python {v.major}.{v.minor}.{v.micro} (requires 3.13+)"
            " \u2014 install from https://www.python.org/downloads/",
        )

    # ffmpeg
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg:
        _check(_PASS, f"ffmpeg: {ffmpeg}")
    else:
        hint = {
            "Darwin": "brew install ffmpeg",
            "Linux": "see https://ffmpeg.org/download.html",
            "Windows": "winget install --id Gyan.FFmpeg",
        }.get(platform.system(), "see https://ffmpeg.org/download.html")
        _check(_FAIL, f"ffmpeg: not found \u2014 {hint}")

    # System TTS fallback (Linux without API keys)
    if platform.system() == "Linux" and not any(
        os.environ.get(k) for k in ("ELEVENLABS_API_KEY", "OPENAI_API_KEY")
    ):
        espeak = shutil.which("espeak-ng") or shutil.which("espeak")
        if espeak:
            espeak_name = Path(espeak).name
            _check(_PASS, f"{espeak_name}: {espeak} (offline fallback)")
        else:
            _check(
                _FAIL,
                "espeak-ng/espeak: not found \u2014 install for offline TTS:"
                " sudo apt-get install espeak-ng",
                required=False,
            )

    # Daemon health (required -- all synthesis goes through voxd now)
    try:
        health = VoxClientSync().health()
        provider_name = str(health.get("provider", "unknown"))
        port = health.get("port", "?")
        running_version = str(health.get("daemon_version", ""))
        wheel_version = installed_version()
        if running_version and running_version != wheel_version:
            # vox-nmb: a stale voxd survives `uv tool upgrade punt-vox`
            # because the wheel on disk was swapped but the long-running
            # daemon process was not cycled. Warn loudly but do not
            # fail — the daemon is still functional, just out of date.
            _check(
                _WARN,
                f"Daemon: running on port {port} (version {running_version}"
                f" \u2014 wheel has {wheel_version},"
                f" run 'vox daemon restart' to refresh)",
            )
        else:
            version_note = f", version {running_version}" if running_version else ""
            _check(
                _PASS,
                f"Daemon: running on port {port}"
                f" (provider: {provider_name}{version_note})",
            )
    except VoxdConnectionError:
        _check(
            _FAIL,
            "Daemon: not running \u2014 start with 'vox daemon install'",
        )
    except VoxdProtocolError as exc:
        _check(
            _FAIL,
            f"Daemon: reachable but unhealthy \u2014 {exc}",
        )

    # Legacy user-level vox.service regression guard (Linux only).
    #
    # vox-45r: an earlier install layout registered a user-level
    # ``~/.config/systemd/user/vox.service`` unit whose ExecStart=
    # pointed at ``vox serve``. The ``serve`` subcommand has since been
    # removed, so any surviving unit crash-loops on systemd's 5-second
    # restart schedule and fills the journal with hundreds of thousands
    # of spurious lines per day. The current daemon is the system-level
    # ``/etc/systemd/system/voxd.service``; the user-level file is pure
    # legacy. This check inspects the file if present and flags it as
    # a hard failure when the referenced subcommand is no longer in
    # the current CLI — the same class of defect ``vox install`` now
    # cleans up automatically.
    legacy_unit = _legacy_user_unit_path()
    if platform.system() == "Linux" and legacy_unit.exists():
        referenced = _parse_user_unit_execstart_subcommand(legacy_unit)
        valid = _valid_vox_subcommands()
        # Shell-quote the path in the remediation hint so users can
        # copy-paste the command even if $HOME contains spaces or
        # other shell metacharacters. The ``systemctl --user disable
        # --now vox.service`` and ``systemctl --user daemon-reload``
        # portions reference a fixed unit name with no interpolation,
        # so only the ``rm`` argument needs quoting.
        #
        # The remediation command is emitted on its own line, unquoted,
        # with a 2-space indent. Earlier revisions wrapped the command
        # in outer single quotes for visual framing, which broke two
        # ways: (1) a literal paste including the outer quotes produced
        # a single quoted word bash refused to execute, and (2) when
        # ``shlex.quote()`` wrapped a path containing spaces in single
        # quotes, the outer-plus-inner nesting collapsed into adjacent
        # quoted fragments and the path's space became a word boundary,
        # splitting the ``rm`` target in two. Emitting the command on
        # its own line avoids both failure modes.
        quoted_legacy_unit = shlex.quote(str(legacy_unit))
        remediation_command = (
            "systemctl --user disable --now vox.service"
            f" && rm {quoted_legacy_unit}"
            " && systemctl --user daemon-reload"
        )
        if referenced is None:
            _check(
                _FAIL,
                f"Legacy user unit: {legacy_unit} exists but ExecStart= is"
                " unparseable \u2014 run 'vox daemon install' to clean it up,"
                " or remove it manually:\n"
                f"  {remediation_command}",
            )
        elif referenced not in valid:
            _check(
                _FAIL,
                f"Legacy user unit: {legacy_unit} references"
                f" 'vox {referenced}', which is not a current subcommand"
                " (this unit will crash-loop on the systemd restart schedule)."
                " Run 'vox daemon install' to clean it up, or remove it"
                " manually:\n"
                f"  {remediation_command}",
            )
        else:
            _check(
                _PASS,
                f"Legacy user unit: {legacy_unit} references current"
                f" 'vox {referenced}' subcommand",
            )

    # uvx (optional)
    uvx = shutil.which("uvx")
    if uvx:
        _check(_PASS, f"uvx: {uvx}", required=False)
    else:
        _check(
            _OPTIONAL,
            "uvx: not found (needed for MCP server)",
            required=False,
        )

    # Claude Desktop config (optional)
    config_path = _claude_desktop_config_path()
    if config_path.exists():
        _check(_PASS, f"Claude Desktop config: {config_path}", required=False)

        try:
            data = json.loads(config_path.read_text(encoding="utf-8"))
            servers = data.get("mcpServers", {})
            if "tts" in servers:
                _check(
                    _PASS,
                    "Claude Desktop MCP: registered",
                    required=False,
                )
            else:
                _check(
                    _OPTIONAL,
                    "Claude Desktop MCP: not registered (run 'vox install-desktop')",
                    required=False,
                )
        except (json.JSONDecodeError, OSError):
            _check(
                _OPTIONAL,
                "Claude Desktop MCP: could not read config",
                required=False,
            )
    else:
        _check(_OPTIONAL, "Claude Desktop config: not found", required=False)
        _check(
            _OPTIONAL,
            "Claude Desktop MCP: not registered (run 'vox install-desktop')",
            required=False,
        )

    # Output directory
    out_dir = default_output_dir()
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
        test_file = out_dir / ".doctor_test"
        test_file.write_text("ok")
        test_file.unlink()
        _check(_PASS, f"Output directory: {out_dir}")
    except OSError as e:
        _check(
            _FAIL,
            f"Output directory: {out_dir} ({e})"
            " \u2014 check permissions or use --output-dir",
        )

    summary = f"{passed} passed, {failed} failed"
    if warned > 0:
        summary += f", {warned} warning" + ("s" if warned > 1 else "")
    text_parts = ["=" * 40, *lines, "=" * 40, summary]
    _emit(
        {
            "passed": passed,
            "failed": failed,
            "warned": warned,
            "checks": checks,
        },
        "\n".join(text_parts),
    )

    if failed > 0:
        raise typer.Exit(code=1)


# ---------------------------------------------------------------------------
# install / uninstall (Claude Code marketplace)
# ---------------------------------------------------------------------------

_PLUGIN_ID = "vox@punt-labs"


@app.command()
def install() -> None:
    """Install the Claude Code plugin and daemon service."""
    # Step 1: Claude Code plugin
    typer.echo("[1/2] Installing Claude Code plugin...")
    claude = shutil.which("claude")
    if not claude:
        typer.echo("Error: claude CLI not found on PATH", err=True)
        raise typer.Exit(code=1)

    result = subprocess.run(
        [claude, "plugin", "install", _PLUGIN_ID, "--scope", "user"],
        check=False,
    )
    if result.returncode != 0:
        typer.echo("Error: plugin install failed", err=True)
        raise typer.Exit(code=1)
    typer.echo("  \u2713 plugin installed")

    # Step 2: daemon service (best-effort — not available in CI/containers)
    # Catch BaseException because service.detect_platform() raises
    # SystemExit on unsupported platforms (not a subclass of Exception).
    typer.echo("[2/2] Registering vox daemon...")
    try:
        from punt_vox.service import install as svc_install

        msg = svc_install()
        typer.echo(f"  \u2713 {msg}")
    except (Exception, SystemExit) as exc:
        typer.echo(f"  \u2022 Skipped: {exc}")
        typer.echo("    Daemon registration is optional — vox works without it.")

    typer.echo()
    _emit(
        {"installed": True},
        "Installed. Restart Claude Code to activate.",
    )


@app.command()
def uninstall() -> None:
    """Uninstall the Claude Code plugin."""
    claude = shutil.which("claude")
    if not claude:
        typer.echo("Error: claude CLI not found on PATH", err=True)
        raise typer.Exit(code=1)

    result = subprocess.run(
        [claude, "plugin", "uninstall", _PLUGIN_ID, "--scope", "user"],
        check=False,
    )
    if result.returncode != 0:
        typer.echo("Error: plugin uninstall failed", err=True)
        raise typer.Exit(code=1)
    _emit({"uninstalled": True}, "Uninstalled.")


# ---------------------------------------------------------------------------
# install-desktop (Claude Desktop MCP server registration)
# ---------------------------------------------------------------------------


def _detect_install_provider(provider_name: str | None) -> str:
    if provider_name:
        return provider_name.lower()
    return auto_detect_provider()


def _build_install_env(prov: str, audio_dir: Path) -> dict[str, str]:
    env: dict[str, str] = {
        "TTS_PROVIDER": prov,
        "VOX_OUTPUT_DIR": str(audio_dir),
    }
    if prov == "elevenlabs":
        key = os.environ.get("ELEVENLABS_API_KEY")
        if not key:
            typer.echo(
                "Error: ELEVENLABS_API_KEY is not set."
                " Export it or use --provider polly/openai/say/espeak.",
                err=True,
            )
            raise typer.Exit(code=1)
        env["ELEVENLABS_API_KEY"] = key
    elif prov == "openai":
        key = os.environ.get("OPENAI_API_KEY")
        if not key:
            typer.echo(
                "Error: OPENAI_API_KEY is not set."
                " Export it or use --provider polly/say/espeak.",
                err=True,
            )
            raise typer.Exit(code=1)
        env["OPENAI_API_KEY"] = key
    return env


@app.command("install-desktop")
def install_desktop(
    output_dir: OutputDirOpt = None,
    uvx_path: Annotated[
        str | None,
        typer.Option("--uvx-path", help="Path to uvx binary. Default: auto-detect."),
    ] = None,
    install_provider: Annotated[
        str | None,
        typer.Option(
            "--provider",
            help="TTS provider. Default: auto-detect.",
        ),
    ] = None,
) -> None:
    """Register the MCP server with Claude Desktop."""
    if platform.system() != "Darwin":
        typer.echo(
            "Warning: Claude Desktop config path is only known for macOS. "
            "You may need to configure manually on this platform.",
            err=True,
        )

    uvx = uvx_path or shutil.which("uvx")
    if not uvx:
        typer.echo(
            "Error: uvx not found. Install uv (https://docs.astral.sh/uv/) first.",
            err=True,
        )
        raise typer.Exit(code=1)

    audio_dir = output_dir or default_output_dir()
    audio_dir.mkdir(parents=True, exist_ok=True)

    detected = _detect_install_provider(install_provider)
    env = _build_install_env(detected, audio_dir)

    config_path = _claude_desktop_config_path()
    config_path.parent.mkdir(parents=True, exist_ok=True)

    if config_path.exists():
        try:
            data = json.loads(config_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            typer.echo(f"Error: Could not read {config_path}: {e}", err=True)
            raise typer.Exit(code=1) from e
    else:
        data = {}

    if "mcpServers" not in data:
        data["mcpServers"] = {}

    overwriting = "tts" in data["mcpServers"]

    data["mcpServers"]["tts"] = {
        "command": uvx,
        "args": ["--from", "punt-vox", "vox", "mcp"],
        "env": env,
    }

    config_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")

    if overwriting:
        typer.echo("Updated existing tts entry.")
    else:
        typer.echo("Registered tts MCP server.")

    typer.echo(f"Provider: {detected}")
    typer.echo(f"Config: {config_path}")
    typer.echo(f"Output: {audio_dir}")
    typer.echo("Restart Claude Desktop to activate.")


# ---------------------------------------------------------------------------
# play
# ---------------------------------------------------------------------------


@app.command()
def play(
    audio_file: Annotated[
        Path,
        typer.Argument(help="Audio file to play.", exists=True),
    ],
) -> None:
    """Play an audio file with serialized flock-based queuing."""
    from punt_vox.playback import play_audio

    play_audio(audio_file)


# ---------------------------------------------------------------------------
# cache commands
# ---------------------------------------------------------------------------


@cache_app.command("status")
def cache_status_cmd() -> None:  # pyright: ignore[reportUnusedFunction]
    """Show cache entry count, size, and path."""
    from punt_vox.cache import cache_status

    try:
        info = cache_status()
    except OSError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1) from exc
    size_kb = info.size_bytes / 1024
    payload = {
        "entries": info.entries,
        "size_bytes": info.size_bytes,
        "path": str(info.path),
    }
    text = f"Entries: {info.entries}\nSize:    {size_kb:.1f} KB\nPath:    {info.path}"
    _emit(payload, text)


@cache_app.command("clear")
def cache_clear_cmd() -> None:  # pyright: ignore[reportUnusedFunction]
    """Delete all cached MP3 files."""
    from punt_vox.cache import cache_clear

    try:
        count = cache_clear()
    except OSError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1) from exc
    _emit({"cleared": count}, f"Cleared {count} cached files.")


# ---------------------------------------------------------------------------
# mcp (stdio transport for Claude Code plugin)
# ---------------------------------------------------------------------------


@app.command()
def mcp() -> None:
    """Run the MCP server with stdio transport."""
    from punt_vox.server import run_server

    run_server()


# ---------------------------------------------------------------------------
# music subcommand group
# ---------------------------------------------------------------------------

music_app = typer.Typer(
    help="Control background music generation.",
    no_args_is_help=True,
)
app.add_typer(music_app, name="music")


@music_app.command("on")
def music_on_cmd(  # pyright: ignore[reportUnusedFunction]
    style: Annotated[
        list[str] | None,
        typer.Option(
            "--style",
            help="Style modifier for music generation (e.g. techno, jazz).",
        ),
    ] = None,
    name: Annotated[
        str | None,
        typer.Option(
            "--name",
            help="Track name. Replays if exists, otherwise saves.",
        ),
    ] = None,
) -> None:
    """Start background music generation via voxd."""
    style_str = " ".join(style) if style else None
    client = VoxClientSync()
    try:
        result = client.music("on", style=style_str, name=name)
        status = result.get("status", "unknown")
        payload: dict[str, object] = {"music": "on", "status": status}
        if style_str:
            payload["style"] = style_str
        if name:
            payload["name"] = name
        text = f"Music on ({status})"
        if name and status == "playing":
            text = f"Playing saved track: {name}"
        elif style_str:
            text += f" — style: {style_str}"
        _emit(payload, text)
    except VoxdConnectionError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    except VoxdProtocolError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc


@music_app.command("off")
def music_off_cmd() -> None:  # pyright: ignore[reportUnusedFunction]
    """Stop background music generation."""
    client = VoxClientSync()
    try:
        result = client.music("off")
        status = result.get("status", "stopped")
        _emit({"music": "off", "status": status}, f"Music off ({status})")
    except VoxdConnectionError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    except VoxdProtocolError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc


@music_app.command("play")
def music_play_cmd(  # pyright: ignore[reportUnusedFunction]
    name: Annotated[
        str,
        typer.Argument(help="Name of saved track to play."),
    ],
) -> None:
    """Replay a saved music track by name."""
    client = VoxClientSync()
    try:
        result = client.music_play(name)
        track_name = result.get("name", name)
        _emit(
            {"music": "play", "name": track_name, "status": "playing"},
            f"Playing: {track_name}",
        )
    except VoxdConnectionError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    except VoxdProtocolError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc


@music_app.command("list")
def music_list_cmd() -> None:  # pyright: ignore[reportUnusedFunction]
    """List saved music tracks."""
    client = VoxClientSync()
    try:
        result = client.music_list()
        tracks: list[dict[str, object]] = result.get("tracks", [])
        if not tracks:
            _emit({"tracks": []}, "No saved tracks.")
            return
        lines: list[str] = []
        for t in tracks:
            raw_size = t.get("size_bytes", 0)
            size_kb = int(str(raw_size)) // 1024
            lines.append(f"  {t['name']} ({size_kb} KB)")
        _emit(
            {"tracks": tracks},
            f"{len(tracks)} saved track(s):\n" + "\n".join(lines),
        )
    except VoxdConnectionError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    except VoxdProtocolError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc


# ---------------------------------------------------------------------------
# daemon subcommand group
# ---------------------------------------------------------------------------

daemon_app = typer.Typer(
    help="Manage the vox daemon service.",
    no_args_is_help=True,
)
app.add_typer(daemon_app, name="daemon")


@daemon_app.command("install")
def daemon_install_cmd() -> None:  # pyright: ignore[reportUnusedFunction]
    """Register vox as a system service (launchd/systemd).

    Run as your normal user, NOT under ``sudo``. vox will prompt once
    for your sudo password when it needs to place the system service
    unit into its system directory. Running under sudo yourself would
    cause per-user state to land under ``/root/.punt-labs/vox/`` and
    the generated unit to run as ``User=root`` — both wrong.
    """
    from punt_vox.service import install as svc_install

    result = svc_install()
    typer.echo(result)


@daemon_app.command("uninstall")
def daemon_uninstall_cmd() -> None:  # pyright: ignore[reportUnusedFunction]
    """Remove the vox system service."""
    from punt_vox.service import uninstall as svc_uninstall

    result = svc_uninstall()
    typer.echo(result)


@daemon_app.command("restart")
def daemon_restart_cmd() -> None:  # pyright: ignore[reportUnusedFunction]
    """Restart the voxd system service and verify it is back up.

    Use this after ``uv tool upgrade punt-vox`` so the running daemon
    picks up the new wheel. A plain ``uv tool upgrade`` replaces the
    on-disk binary but does not cycle the long-running voxd process —
    so changes to the WebSocket protocol or playback behavior do not
    take effect until the service is restarted. ``vox daemon restart``
    is the supported way to do that.

    Runs as your normal user, NOT under ``sudo``. vox will prompt once
    for your sudo password when it drives ``systemctl``/``launchctl``
    itself. Running under sudo yourself would corrupt the sudo state
    the service manager uses.
    """
    from punt_vox.service import (
        _ensure_port_free,  # pyright: ignore[reportPrivateUsage]
        _launchd_stop,  # pyright: ignore[reportPrivateUsage]
        _systemd_stop,  # pyright: ignore[reportPrivateUsage]
        detect_platform,
    )

    # Refuse Windows before touching ``os.geteuid``. ``geteuid`` is
    # POSIX-only and raises ``AttributeError`` on Windows, which would
    # surface as a confusing crash for anyone experimenting with vox on
    # an unsupported platform (or a Windows-based test harness). vox
    # daemon restart has the same OS matrix as ``vox daemon install``
    # (macOS + Linux), so match the CLI's other platform-refusal
    # messages: explain the scope and stop cleanly. Cursor Bugbot on
    # PR #175.
    if sys.platform == "win32":
        raise typer.BadParameter(
            "vox daemon restart is only supported on macOS and Linux; "
            "Windows does not have a comparable system service manager."
        )
    if os.geteuid() == 0:
        raise typer.BadParameter(
            "vox daemon restart must be run as your normal user, not root "
            "or sudo. vox will prompt for your sudo password when it drives "
            "systemctl/launchctl. Re-run without sudo:\n\n"
            "    vox daemon restart\n"
        )

    plat = detect_platform()

    logger.info("Stopping voxd via service manager...")
    if plat == "macos":
        _launchd_stop()
    else:
        _systemd_stop()

    logger.info("Waiting for port to free...")
    # ``_ensure_port_free`` raises ``SystemExit(msg)`` on port contention
    # that survives the stop + kill attempt. Typer's runner swallows
    # ``SystemExit`` without printing the message argument, so the user
    # would otherwise see a silent exit-1 with no indication of why the
    # restart aborted. Translate the raised message into a typer error
    # with the same code path and log hint as the other failure modes.
    try:
        _ensure_port_free()
    except SystemExit as exc:
        reason = str(exc) if exc.code not in (0, None) else ""
        detail = f": {reason}" if reason else ""
        typer.echo(
            f"Error: port still occupied after service manager stop{detail}\n"
            f"Check the logs at {log_dir() / 'voxd.log'}",
            err=True,
        )
        raise typer.Exit(code=1) from exc

    logger.info("Starting voxd via service manager...")
    try:
        if plat == "macos":
            subprocess.run(
                [
                    "sudo",
                    "launchctl",
                    "load",
                    "-w",
                    "/Library/LaunchDaemons/com.punt-labs.voxd.plist",
                ],
                check=True,
            )
            subprocess.run(
                ["sudo", "launchctl", "kickstart", "-k", "system/com.punt-labs.voxd"],
                check=True,
            )
        else:
            subprocess.run(
                ["sudo", "systemctl", "start", "voxd"],
                check=True,
            )
    except subprocess.CalledProcessError as exc:
        log_path = log_dir() / "voxd.log"
        typer.echo(
            f"Error: service manager failed to start voxd: {exc}\n"
            f"Check the logs at {log_path}",
            err=True,
        )
        raise typer.Exit(code=1) from exc

    logger.info("Waiting for voxd to come back up...")
    deadline = time.monotonic() + 5.0
    last_exc: Exception | None = None
    while time.monotonic() < deadline:
        try:
            health = VoxClientSync().health()
        except (VoxdConnectionError, VoxdProtocolError) as exc:
            last_exc = exc
            time.sleep(0.2)
            continue
        pid = health.get("pid", "?")
        port = health.get("port", "?")

        # Load-bearing verification for vox-nmb: a silent stop failure
        # (systemctl lost the unit, dbus quirk, _is_vox_daemon_process
        # returning False, etc.) can leave the OLD daemon alive, and
        # ``systemctl start voxd`` exits 0 as a no-op when the unit is
        # already active. Without this version check, the restart
        # command would print success while the stale daemon continues
        # to answer — which is exactly the bug vox-nmb exists to prevent.
        running_version = str(health.get("daemon_version", ""))
        wheel_version = installed_version()
        log_path = log_dir() / "voxd.log"
        if not running_version:
            # Pre-cef3e8a daemons do not self-report a version. Fail
            # closed: we cannot prove the restart picked up new code,
            # and the symptom we're trying to detect (stale daemon)
            # would look exactly like this.
            typer.echo(
                "Error: restarted daemon did not report a version. Expected "
                f"{wheel_version}. Check {log_path} — the daemon may be "
                "running pre-feat/install-verify-hardening code that cannot "
                "self-report its version.",
                err=True,
            )
            raise typer.Exit(code=1)
        if running_version != wheel_version:
            typer.echo(
                f"Error: daemon reports version {running_version} but wheel is "
                f"{wheel_version}. The restart did not pick up the new code. "
                f"Check {log_path}.",
                err=True,
            )
            raise typer.Exit(code=1)

        _emit(
            {
                "restarted": True,
                "pid": pid,
                "port": port,
                "daemon_version": running_version,
            },
            f"voxd restarted (pid={pid}, listening on port {port}, "
            f"version {running_version})",
        )
        return

    log_path = log_dir() / "voxd.log"
    reason = f": {last_exc}" if last_exc is not None else ""
    typer.echo(
        f"Error: voxd did not come back up within 5s{reason}\n"
        f"Check the logs at {log_path}",
        err=True,
    )
    raise typer.Exit(code=1)


@daemon_app.command("status")
def daemon_status_cmd() -> None:  # pyright: ignore[reportUnusedFunction]
    """Check if the vox daemon is reachable."""
    from punt_vox.client import read_port_file

    port = read_port_file()
    if port is None:
        typer.echo("Daemon: not running (no port file)")
        raise typer.Exit(code=1)

    try:
        url = f"http://127.0.0.1:{port}/health"
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=3) as resp:
            data = json.loads(resp.read())
        uptime = data.get("uptime_seconds", "?")
        sessions = data.get("active_sessions", "?")
        typer.echo(f"Daemon: running on port {port}")
        typer.echo(f"  Uptime:   {uptime}s")
        typer.echo(f"  Sessions: {sessions}")
    except urllib.error.URLError as exc:
        reason = exc.reason
        if isinstance(reason, ConnectionRefusedError):
            typer.echo(f"Daemon: not running (port {port} refused)")
        elif isinstance(reason, TimeoutError):
            typer.echo(f"Daemon: not responding on port {port} (timeout)")
        else:
            typer.echo(f"Daemon: cannot reach port {port}: {reason}")
        raise typer.Exit(code=1) from exc
    except json.JSONDecodeError as exc:
        typer.echo(f"Daemon: port {port} responded but not valid JSON (wrong process?)")
        raise typer.Exit(code=1) from exc
    except OSError as exc:
        typer.echo(f"Daemon: cannot reach port {port}: {exc}")
        raise typer.Exit(code=1) from exc


if __name__ == "__main__":
    app()
