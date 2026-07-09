"""Typer CLI for punt-vox."""

from __future__ import annotations

import json
import logging
import platform
import shutil
import subprocess
import urllib.error
import urllib.request
from pathlib import Path
from typing import TYPE_CHECKING, Annotated

import typer

from punt_vox import __version__
from punt_vox.api_key_resolver import ApiKeyResolver
from punt_vox.cli_music import build_music_app
from punt_vox.client_errors import VoxdConnectionError, VoxdProtocolError
from punt_vox.client_sync import VoxClientSync
from punt_vox.config import ConfigStore
from punt_vox.desktop_install import DesktopInstaller
from punt_vox.dirs import DEFAULT_CONFIG_DIR, default_output_dir, find_config_dir
from punt_vox.hooks import hook_app
from punt_vox.output_formatter import OutputFormatter
from punt_vox.types_synthesis import SynthesisSpec

if TYPE_CHECKING:
    # Annotation-only; keeps `client` off __main__'s runtime import graph.
    from punt_vox.client import SynthesizeResult

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

_formatter = OutputFormatter()


def _configure_logging(*, verbose: bool) -> None:
    from punt_vox.logging_config import configure_logging

    configure_logging(stderr_level="DEBUG" if verbose else "WARNING")


def _validated_spec(spec: SynthesisSpec) -> SynthesisSpec:
    """Validate a spec at the CLI boundary, returning it for chaining.

    Translates ``ValueError`` from :meth:`SynthesisSpec.validate` into
    ``typer.BadParameter`` so the CLI displays a user-friendly message. The
    caller builds the :class:`SynthesisSpec` (the bundle already names every
    field), so this stays a one-argument boundary check rather than re-listing
    a dozen parameters.
    """
    try:
        spec.validate()
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    return spec


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
    typer.Option("--output-dir", "-d", help="Output directory. Default: ~/Music/vox."),
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
    json_output: JsonOutput = False,  # noqa: FBT002 -- typer CLI requires bool default
    verbose: Verbose = False,  # noqa: FBT002 -- typer CLI requires bool default
    quiet: Quiet = False,  # noqa: FBT002 -- typer CLI requires bool default
) -> None:
    """Text-to-speech CLI."""
    if verbose and quiet:
        raise typer.BadParameter("--verbose and --quiet are mutually exclusive.")
    _formatter.set_json(value=json_output)
    _formatter.set_quiet(value=quiet)
    _configure_logging(verbose=verbose)


# ---------------------------------------------------------------------------
# unmute — play audio
# ---------------------------------------------------------------------------


def _dedup_fields(result: SynthesizeResult) -> dict[str, object]:
    """Return the dedup annotations for a played result, empty if fresh."""
    if not result.deduped:
        return {}
    fields: dict[str, object] = {"deduped": True}
    if result.original_played_at is not None:
        fields["original_played_at"] = result.original_played_at
    if result.ttl_seconds_remaining is not None:
        fields["ttl_seconds_remaining"] = result.ttl_seconds_remaining
    return fields


def _speak_segments(
    segments: list[str],
    spec: SynthesisSpec,
    once: int | None,
) -> None:
    """Synthesize and emit each segment; map voxd errors to a CLI exit."""
    client = VoxClientSync()
    for seg_text in segments:
        try:
            result = client.synthesize(seg_text, spec, once=once)
        except (VoxdConnectionError, VoxdProtocolError) as exc:
            typer.echo(f"Error: {exc}", err=True)
            raise typer.Exit(code=1) from exc
        payload: dict[str, object] = {"id": result.request_id, **_dedup_fields(result)}
        _formatter.emit(payload, seg_text)


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
    speaker_boost: SpeakerBoostFlag = False,  # noqa: FBT002 -- typer CLI requires bool default
    once: OnceOpt = None,
    api_key: ApiKeyOpt = None,
    api_key_file: ApiKeyFileOpt = None,
    api_key_stdin: ApiKeyStdinFlag = False,  # noqa: FBT002 -- typer CLI requires bool default
) -> None:
    """Synthesize and play audio via voxd."""
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
    resolved_api_key = ApiKeyResolver(
        ctx, api_key, api_key_file, api_key_stdin=api_key_stdin
    ).resolve()

    boost = speaker_boost if speaker_boost else None
    spec = _validated_spec(
        SynthesisSpec(
            voice=voice,
            language=language,
            rate=rate,
            provider=provider,
            model=model,
            stability=stability,
            similarity=similarity,
            style=style,
            speaker_boost=boost,
            api_key=resolved_api_key,
        )
    )

    segments = _resolve_text_segments(text, from_file)
    _speak_segments(segments, spec, once)


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
    speaker_boost: SpeakerBoostFlag = False,  # noqa: FBT002 -- typer CLI requires bool default
) -> None:
    """Synthesize and save audio to file via voxd."""
    from punt_vox.types import generate_filename

    boost = speaker_boost if speaker_boost else None
    spec = _validated_spec(
        SynthesisSpec(
            voice=voice,
            language=language,
            rate=rate,
            provider=provider,
            model=model,
            stability=stability,
            similarity=similarity,
            style=style,
            speaker_boost=boost,
        )
    )

    segments = _resolve_text_segments(text, from_file)
    out_dir = output_dir if output_dir is not None else default_output_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    client = VoxClientSync()
    for i, seg_text in enumerate(segments):
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
            mp3_bytes = client.record(seg_text, spec)
        except VoxdConnectionError as exc:
            typer.echo(f"Error: {exc}", err=True)
            raise typer.Exit(code=1) from exc
        except VoxdProtocolError as exc:
            typer.echo(f"Error: {exc}", err=True)
            raise typer.Exit(code=1) from exc

        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(mp3_bytes)
        _formatter.emit({"path": str(out_path)}, str(out_path))


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
    cd = find_config_dir() or DEFAULT_CONFIG_DIR
    store = ConfigStore(cd)
    if mood == "auto":
        store.write_fields({"vibe_tags": "", "vibe": "", "vibe_mode": "auto"})
        _formatter.emit({"vibe_mode": "auto"}, "Vibe mode: auto")
    elif mood == "off":
        store.write_fields({"vibe_tags": "", "vibe": "", "vibe_mode": "off"})
        _formatter.emit({"vibe_mode": "off"}, "Vibe mode: off")
    else:
        store.write_fields({"vibe": mood, "vibe_tags": "", "vibe_mode": "manual"})
        _formatter.emit({"vibe": mood, "vibe_mode": "manual"}, f"Vibe: {mood}")


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

    config_dir = find_config_dir() or DEFAULT_CONFIG_DIR
    store = ConfigStore(config_dir)
    first_init = store.read_field("notify") is None
    updates: dict[str, str] = {"notify": mode}
    if mode == "c" or (first_init and mode == "y"):
        updates["speak"] = "y"
    if voice is not None:
        updates["voice"] = voice
    store.write_fields(updates)

    labels = {
        "y": "Notifications enabled.",
        "n": "Notifications disabled.",
        "c": "Continuous mode on.",
    }
    _formatter.emit(updates, labels[mode])


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

    ConfigStore(find_config_dir() or DEFAULT_CONFIG_DIR).write_field("speak", mode)
    label = "Voice on." if mode == "y" else "Muted — chimes only."
    _formatter.emit({"speak": mode}, label)


# ---------------------------------------------------------------------------
# voice — set session voice
# ---------------------------------------------------------------------------


@app.command("voice")
def voice_cmd(  # pyright: ignore[reportUnusedFunction]
    name: Annotated[str, typer.Argument(help="Voice name (e.g. matilda, roger).")],
) -> None:
    """Set the session voice."""
    ConfigStore(find_config_dir() or DEFAULT_CONFIG_DIR).write_field("voice", name)
    _formatter.emit({"voice": name}, f"{name}'s here.")


# ---------------------------------------------------------------------------
# version
# ---------------------------------------------------------------------------


@app.command("version")
def version_cmd() -> None:  # pyright: ignore[reportUnusedFunction]
    """Print version."""
    _formatter.emit({"version": __version__}, f"vox {__version__}")


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------


@app.command("status")
def status_cmd() -> None:  # pyright: ignore[reportUnusedFunction]
    """Show current state (daemon, voice, vibe, notify)."""
    cfg = ConfigStore(find_config_dir() or DEFAULT_CONFIG_DIR).read()

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
    _formatter.emit(info, "\n".join(text_lines))


# ---------------------------------------------------------------------------
# doctor
# ---------------------------------------------------------------------------


@app.command()
def doctor() -> None:  # pyright: ignore[reportUnusedFunction]
    """Check system health for vox."""
    from punt_vox.doctor import DoctorCheck, format_results

    check = DoctorCheck(client=VoxClientSync())
    results = check.run_all()
    payload, text = format_results(results)
    _formatter.emit(payload, text)

    if payload.get("failed", 0):
        raise typer.Exit(code=1)


# ---------------------------------------------------------------------------
# install / uninstall (Claude Code marketplace)
# ---------------------------------------------------------------------------

_PLUGIN_ID = "vox@punt-labs"


@app.command()
def install() -> None:
    """Install the Claude Code plugin and daemon service."""
    # Step 1: Claude Code plugin
    typer.echo("[1/3] Installing Claude Code plugin...")
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
    # SystemExit: service.detect_platform() raises on unsupported platforms.
    # OSError/CalledProcessError: subprocess and filesystem failures during install.
    # LaunchctlError: macOS bring-up (bootstrap/kickstart) fails on a GUI-less host.
    # ServiceHealthError: voxd registered but never answered health (silent-down).
    typer.echo("[2/3] Registering vox daemon...")
    from punt_vox.service import install as svc_install
    from punt_vox.service.health_verify import ServiceHealthError
    from punt_vox.service.launchctl import LaunchctlError

    try:
        msg = svc_install()
        typer.echo(f"  \u2713 {msg}")
    except (
        SystemExit,
        OSError,
        subprocess.CalledProcessError,
        LaunchctlError,
        ServiceHealthError,
    ) as exc:
        typer.echo(f"  \u2022 Skipped: {exc}")
        typer.echo("    Daemon registration is optional — vox works without it.")

    # Step 3: write the usage guide and register its @-import so it loads in
    # every Claude Code session. OSError only -- a read-only home should warn,
    # not abort an otherwise-successful plugin install.
    typer.echo("[3/3] Registering vox usage guide...")
    from punt_vox.guidance import VoxGuidance

    try:
        typer.echo(f"  ✓ {VoxGuidance.for_current_user().install()}")
    except OSError as exc:
        typer.echo(f"  • Skipped: {exc}")

    typer.echo()
    _formatter.emit(
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
    plugin_failed = result.returncode != 0
    if plugin_failed:
        typer.echo("Error: plugin uninstall failed", err=True)

    # Prune the usage guide + its @-import regardless of the plugin outcome:
    # uninstall must be idempotent and self-healing, so a plugin step that fails
    # (e.g. the plugin was already gone) must not orphan ~/.punt-labs/vox/CLAUDE.md
    # or its global import line. OSError only -- a filesystem hiccup should not
    # mask the plugin result.
    from punt_vox.guidance import VoxGuidance

    try:
        typer.echo(VoxGuidance.for_current_user().uninstall())
    except OSError as exc:
        typer.echo(f"  • Skipped guide removal: {exc}")

    if plugin_failed:
        raise typer.Exit(code=1)

    _formatter.emit({"uninstalled": True}, "Uninstalled.")


@app.command("register-guidance", hidden=True)
def register_guidance(
    *,
    remove: Annotated[
        bool,
        typer.Option("--remove", "-r", help="Prune the guide instead of writing it."),
    ] = False,
) -> None:
    """Write (or remove) the usage guide and its ``@``-import.

    Hidden plumbing for install scripts (``install.sh``) that register the
    plugin directly via ``claude plugin install`` and so never reach the
    ``vox install`` command. Idempotent: the global ``CLAUDE.md`` is rewritten
    only when the import line actually changes.
    """
    from punt_vox.guidance import VoxGuidance

    guide = VoxGuidance.for_current_user()
    try:
        typer.echo(guide.uninstall() if remove else guide.install())
    except OSError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc


# ---------------------------------------------------------------------------
# install-desktop (Claude Desktop MCP server registration)
# ---------------------------------------------------------------------------


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

    installer = DesktopInstaller.detect(install_provider, audio_dir)

    from punt_vox.doctor import claude_desktop_config_path

    config_path = claude_desktop_config_path()
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

    overwriting = "vox" in data["mcpServers"]

    data["mcpServers"]["vox"] = {
        "command": uvx,
        "args": ["--from", "punt-vox", "vox", "mcp"],
        "env": installer.server_env(),
    }

    config_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")

    if overwriting:
        typer.echo("Updated existing vox entry.")
    else:
        typer.echo("Registered vox MCP server.")

    typer.echo(f"Provider: {installer.provider}")
    typer.echo(f"Config: {config_path}")
    typer.echo(f"Output: {audio_dir}")
    if not installer.daemon_can_authenticate():
        typer.echo(installer.credential_guidance(), err=True)
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
    _formatter.emit(payload, text)


@cache_app.command("clear")
def cache_clear_cmd() -> None:  # pyright: ignore[reportUnusedFunction]
    """Delete all cached MP3 files."""
    from punt_vox.cache import cache_clear

    try:
        count = cache_clear()
    except OSError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1) from exc
    _formatter.emit({"cleared": count}, f"Cleared {count} cached files.")


# ---------------------------------------------------------------------------
# mcp (stdio transport for Claude Code plugin)
# ---------------------------------------------------------------------------


@app.command()
def mcp() -> None:
    """Run the MCP server with stdio transport."""
    from punt_vox.server import run_server

    run_server()


# ---------------------------------------------------------------------------
# music subcommand group (consume-only; implementation in cli_music)
# ---------------------------------------------------------------------------

app.add_typer(build_music_app(_formatter), name="music")


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

    Run as your normal user, NOT under ``sudo``. On macOS no sudo is
    needed — the LaunchAgent installs to ``~/Library/LaunchAgents/``.
    On Linux, vox will prompt once for your sudo password to place
    the systemd unit. Running under sudo yourself would cause per-user
    state to land under ``/root/.punt-labs/vox/`` — wrong on both
    platforms.
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

    Runs as your normal user, NOT under ``sudo``. On macOS, no sudo
    is needed (LaunchAgent). On Linux, vox will prompt once for your
    sudo password when it drives ``systemctl``.
    """
    from punt_vox.daemon_restarter import DaemonRestarter

    restarter = DaemonRestarter(_formatter)
    restarter.run()


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
        if not url.startswith("http://"):  # S310: validate scheme before urlopen
            msg = f"unexpected URL scheme: {url}"
            raise ValueError(msg)
        req = urllib.request.Request(url, method="GET")  # noqa: S310 -- scheme validated above
        with urllib.request.urlopen(req, timeout=3) as resp:  # noqa: S310 -- scheme validated above
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
