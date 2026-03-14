"""Typer CLI for punt-vox."""

from __future__ import annotations

import json
import logging
import os
import platform
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Annotated

import typer

from punt_vox import __version__
from punt_vox.config import read_config, resolve_config_path, write_field, write_fields
from punt_vox.core import TTSClient, stitch_audio
from punt_vox.hooks import hook_app
from punt_vox.normalize import normalize_for_speech
from punt_vox.output import default_output_dir
from punt_vox.providers import DEFAULT_VOICES, auto_detect_provider, get_provider
from punt_vox.resolve import resolve_voice_and_language
from punt_vox.types import (
    SynthesisRequest,
    SynthesisResult,
    TTSProvider,
    result_to_dict,
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
_VOICE_DEFAULTS = ", ".join(
    f"{DEFAULT_VOICES[k]} ({_PROVIDER_DISPLAY[k]})"
    for k in ("elevenlabs", "polly", "openai", "say", "espeak")
)

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


def _print_result(result: SynthesisResult) -> None:
    payload = result_to_dict(result)
    _emit(payload, f"{result.path}")


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
    typer.Option("--voice", help=f"Voice name. Default: {_VOICE_DEFAULTS}."),
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
PauseOpt = Annotated[
    int,
    typer.Option("--pause", help="Pause between segments in ms."),
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
    text: TextArg = None,
    from_file: FromOpt = None,
    voice: VoiceOpt = None,
    language: LanguageOpt = None,
    rate: RateOpt = 90,
    pause: PauseOpt = 500,
    provider: ProviderOpt = None,
    model: ModelOpt = None,
    stability: StabilityOpt = None,
    similarity: SimilarityOpt = None,
    style: StyleOpt = None,
    speaker_boost: SpeakerBoostFlag = False,
) -> None:
    """Synthesize and play audio."""
    from punt_vox.ephemeral import clean_ephemeral, ephemeral_output_dir
    from punt_vox.playback import enqueue
    from punt_vox.types import generate_filename

    _validate_voice_settings(stability, similarity, style)
    prov = get_provider(provider, model=model)
    boost = speaker_boost if speaker_boost else None

    requests = _build_cli_requests(
        text,
        from_file,
        voice,
        language,
        prov,
        rate,
        stability,
        similarity,
        style,
        boost,
    )

    clean_ephemeral()
    out_dir = ephemeral_output_dir()
    client = TTSClient(prov)

    if len(requests) > 1:
        combined_text = " | ".join(r.text for r in requests)
        out_path = out_dir / generate_filename(combined_text, prefix="seg_")
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            tmp_paths: list[Path] = []
            for i, req in enumerate(requests):
                seg_path = tmp_dir / f"seg_{i:04d}.mp3"
                client.synthesize(req, seg_path)
                tmp_paths.append(seg_path)
            stitch_audio(tmp_paths, out_path, pause)
        enqueue(out_path)
        _emit({"path": str(out_path)}, str(out_path))
    else:
        for req in requests:
            out_path = out_dir / generate_filename(req.text)
            result = client.synthesize(req, out_path)
            enqueue(result.path)
            _print_result(result)


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
    pause: PauseOpt = 500,
    output: OutputOpt = None,
    output_dir: OutputDirOpt = None,
    provider: ProviderOpt = None,
    model: ModelOpt = None,
    stability: StabilityOpt = None,
    similarity: SimilarityOpt = None,
    style: StyleOpt = None,
    speaker_boost: SpeakerBoostFlag = False,
) -> None:
    """Synthesize and save audio to file."""
    _validate_voice_settings(stability, similarity, style)
    prov = get_provider(provider, model=model)
    boost = speaker_boost if speaker_boost else None

    requests = _build_cli_requests(
        text,
        from_file,
        voice,
        language,
        prov,
        rate,
        stability,
        similarity,
        style,
        boost,
    )

    out_dir = output_dir if output_dir is not None else default_output_dir()
    client = TTSClient(prov)

    if output is not None and len(requests) == 1:
        result = client.synthesize(requests[0], output)
        _print_result(result)
        return

    if output is not None and len(requests) > 1:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            tmp_paths: list[Path] = []
            for i, req in enumerate(requests):
                seg_path = tmp_dir / f"seg_{i:04d}.mp3"
                client.synthesize(req, seg_path)
                tmp_paths.append(seg_path)
            stitch_audio(tmp_paths, output, pause)
        _emit({"path": str(output)}, str(output))
        return

    from punt_vox.types import generate_filename

    for req in requests:
        out_path = out_dir / generate_filename(req.text)
        result = client.synthesize(req, out_path)
        _print_result(result)


def _build_cli_requests(
    text: str | None,
    from_file: Path | None,
    voice: str | None,
    language: str | None,
    prov: TTSProvider,
    rate: int,
    stability: float | None,
    similarity: float | None,
    style: float | None,
    speaker_boost: bool | None,
) -> list[SynthesisRequest]:
    """Build SynthesisRequest list from CLI args."""
    if from_file is not None:
        return _requests_from_file(
            from_file,
            voice,
            language,
            prov,
            rate,
            stability,
            similarity,
            style,
            speaker_boost,
        )

    if text is None:
        typer.echo("Error: provide TEXT argument or --from file.", err=True)
        raise typer.Exit(code=1)

    resolved_voice, resolved_lang = resolve_voice_and_language(prov, voice, language)
    text = normalize_for_speech(text)
    return [
        SynthesisRequest(
            text=text,
            voice=resolved_voice,
            language=resolved_lang,
            rate=rate,
            stability=stability,
            similarity=similarity,
            style=style,
            speaker_boost=speaker_boost,
        )
    ]


def _requests_from_file(
    from_file: Path,
    voice: str | None,
    language: str | None,
    prov: TTSProvider,
    rate: int,
    stability: float | None,
    similarity: float | None,
    style: float | None,
    speaker_boost: bool | None,
) -> list[SynthesisRequest]:
    """Parse a JSON segments file into SynthesisRequest list."""
    try:
        raw = json.loads(from_file.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise typer.BadParameter("--from file must contain valid JSON.") from exc

    if not isinstance(raw, list):
        raise typer.BadParameter("--from file must contain a JSON array.")

    requests: list[SynthesisRequest] = []
    for i, item in enumerate(raw):  # pyright: ignore[reportUnknownVariableType, reportUnknownArgumentType]
        seg_voice: str | None
        seg_text: str
        if isinstance(item, str):
            seg_voice = voice
            seg_text = item
        elif isinstance(item, dict):
            seg_voice = str(item.get("voice") or voice or "")  # pyright: ignore[reportUnknownMemberType, reportUnknownArgumentType]
            seg_text = str(item.get("text") or "")  # pyright: ignore[reportUnknownMemberType, reportUnknownArgumentType]
            seg_voice = seg_voice or None
        else:
            raise typer.BadParameter(
                f"Element {i} must be a string or {{voice, text}} object."
            )

        if not seg_text:
            continue

        seg_text = normalize_for_speech(seg_text)
        resolved_voice, resolved_lang = resolve_voice_and_language(
            prov, seg_voice, language
        )
        requests.append(
            SynthesisRequest(
                text=seg_text,
                voice=resolved_voice,
                language=resolved_lang,
                rate=rate,
                stability=stability,
                similarity=similarity,
                style=style,
                speaker_boost=speaker_boost,
            )
        )
    return requests


# ---------------------------------------------------------------------------
# vibe — set session mood
# ---------------------------------------------------------------------------


@app.command("vibe")
def vibe_cmd(  # pyright: ignore[reportUnusedFunction]
    mood: Annotated[str, typer.Argument(help="Mood description or 'auto'/'off'.")],
) -> None:
    """Set session mood for TTS voice."""
    cp = resolve_config_path()
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

    config_path = resolve_config_path()
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

    write_field("speak", mode, config_path=resolve_config_path())
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
    write_field("voice", name, config_path=resolve_config_path())
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
def status_cmd(  # pyright: ignore[reportUnusedFunction]
    provider: ProviderOpt = None,
    model: ModelOpt = None,
) -> None:
    """Show current state (provider, voice, vibe, notify)."""
    prov = get_provider(provider, model=model)
    cfg = read_config(config_path=resolve_config_path())

    info = {
        "provider": prov.name,
        "voice": cfg.voice or prov.default_voice,
        "notify": cfg.notify,
        "speak": cfg.speak,
        "vibe_mode": cfg.vibe_mode,
        "vibe": cfg.vibe,
        "vibe_tags": cfg.vibe_tags,
        "vibe_signals": cfg.vibe_signals,
    }

    display_name = _PROVIDER_DISPLAY.get(prov.name, prov.name)
    text_lines = [
        f"Provider:  {display_name}",
        f"Voice:     {info['voice']}",
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


def _claude_desktop_config_path() -> Path:
    return (
        Path.home()
        / "Library"
        / "Application Support"
        / "Claude"
        / "claude_desktop_config.json"
    )


@app.command()
def doctor(
    provider: ProviderOpt = None,
    model: ModelOpt = None,
) -> None:
    """Check system health for vox."""
    prov = get_provider(provider, model=model)
    passed = 0
    failed = 0
    lines: list[str] = []
    checks: list[dict[str, object]] = []

    def _check(symbol: str, message: str, *, required: bool = True) -> None:
        nonlocal passed, failed
        lines.append(f"{symbol} {message}")
        checks.append(
            {
                "status": symbol,
                "message": message,
                "required": required,
                "passed": symbol == _PASS,
            }
        )
        if symbol == _PASS:
            passed += 1
        elif symbol == _FAIL and required:
            failed += 1

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

    # Active provider
    _check(_PASS, f"Provider: {prov.name}")

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

    # Provider-specific health checks
    for check in prov.check_health():
        symbol = _PASS if check.passed else _FAIL
        _check(symbol, check.message, required=check.required)

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

    # uvx (optional)
    uvx = shutil.which("uvx")
    if uvx:
        _check(_PASS, f"uvx: {uvx}", required=False)
    else:
        _check(_OPTIONAL, "uvx: not found (needed for MCP server)", required=False)

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

    text_parts = ["=" * 40, *lines, "=" * 40, f"{passed} passed, {failed} failed"]
    _emit(
        {"passed": passed, "failed": failed, "checks": checks},
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
    """Install the Claude Code plugin via the punt-labs marketplace."""
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
    _emit({"installed": True}, "Installed. Restart Claude Code to activate.")


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
# serve (daemon mode)
# ---------------------------------------------------------------------------


@app.command()
def serve(
    port: Annotated[
        int,
        typer.Option("--port", help="Port to bind."),
    ] = 8421,
    host: Annotated[
        str,
        typer.Option("--host", help="Host to bind."),
    ] = "127.0.0.1",
) -> None:
    """Start the vox daemon (HTTP + WebSocket server)."""
    from punt_vox.daemon import serve as daemon_serve

    daemon_serve(port=port, host=host)


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
    """Register vox as a system service (launchd/systemd)."""
    from punt_vox.service import install as svc_install

    result = svc_install()
    typer.echo(result)


@daemon_app.command("uninstall")
def daemon_uninstall_cmd() -> None:  # pyright: ignore[reportUnusedFunction]
    """Remove the vox system service."""
    from punt_vox.service import uninstall as svc_uninstall

    result = svc_uninstall()
    typer.echo(result)


@daemon_app.command("status")
def daemon_status_cmd() -> None:  # pyright: ignore[reportUnusedFunction]
    """Check if the vox daemon is reachable."""
    import urllib.request

    from punt_vox.daemon import read_port_file

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
    except ConnectionRefusedError as exc:
        typer.echo(f"Daemon: not running (port {port} refused)")
        raise typer.Exit(code=1) from exc
    except TimeoutError as exc:
        typer.echo(f"Daemon: not responding on port {port} (timeout)")
        raise typer.Exit(code=1) from exc
    except json.JSONDecodeError as exc:
        typer.echo(f"Daemon: port {port} responded but not valid JSON (wrong process?)")
        raise typer.Exit(code=1) from exc
    except OSError as exc:
        typer.echo(f"Daemon: cannot reach port {port}: {exc}")
        raise typer.Exit(code=1) from exc
