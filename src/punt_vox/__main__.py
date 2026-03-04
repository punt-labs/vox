"""Typer CLI for punt-vox."""

from __future__ import annotations

import json
import logging
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Annotated, cast

import typer

from punt_vox.core import TTSClient
from punt_vox.output import default_output_dir
from punt_vox.providers import DEFAULT_VOICES, auto_detect_provider, get_provider
from punt_vox.resolve import resolve_voice_and_language
from punt_vox.types import (
    MergeStrategy,
    SynthesisRequest,
    SynthesisResult,
    result_to_dict,
)

logger = logging.getLogger(__name__)

app = typer.Typer(name="vox", help="Text-to-speech CLI.", no_args_is_help=True)

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


def _emit(payload: object, text: str) -> None:
    if _json_output:
        typer.echo(json.dumps(payload))
    else:
        typer.echo(text)


def _configure_logging(verbose: bool) -> None:
    from punt_vox.logging_config import configure_logging

    configure_logging(stderr_level="DEBUG" if verbose else "WARNING")


def _print_result(result: SynthesisResult) -> None:
    payload = result_to_dict(result)
    _emit(payload, f"{result.path}")


def _print_results(results: list[SynthesisResult]) -> None:
    if _json_output:
        payload = [result_to_dict(r) for r in results]
        _emit(payload, "")
        return
    for r in results:
        _print_result(r)


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
MergeFlag = Annotated[
    bool,
    typer.Option("--merge", help="Merge all outputs into a single file."),
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

# Pair-specific options (not shared widely enough for top-level aliases)
Voice1Opt = Annotated[
    str | None,
    typer.Option("--voice1", help="Voice for first text(s)."),
]
Voice2Opt = Annotated[
    str | None,
    typer.Option("--voice2", help="Voice for second text(s)."),
]
Lang1Opt = Annotated[
    str | None,
    typer.Option("--lang1", help="ISO 639-1 language for first text(s)."),
]
Lang2Opt = Annotated[
    str | None,
    typer.Option("--lang2", help="ISO 639-1 language for second text(s)."),
]
TextArg = Annotated[str, typer.Argument(help="Text to convert to speech.")]
Text1Arg = Annotated[str, typer.Argument(help="First text (typically English).")]
Text2Arg = Annotated[str, typer.Argument(help="Second text (target language).")]
InputFileArg = Annotated[Path, typer.Argument(help="JSON input file.", exists=True)]


# ---------------------------------------------------------------------------
# callback (global flags)
# ---------------------------------------------------------------------------


@app.callback()
def _callback(  # pyright: ignore[reportUnusedFunction]
    verbose: Verbose = False,
    json_output: JsonOutput = False,
) -> None:
    """Text-to-speech CLI."""
    global _json_output
    _json_output = json_output
    _configure_logging(verbose)


# ---------------------------------------------------------------------------
# synthesize
# ---------------------------------------------------------------------------


@app.command()
def synthesize(
    text: TextArg,
    voice: VoiceOpt = None,
    language: LanguageOpt = None,
    rate: RateOpt = 90,
    output: OutputOpt = None,
    provider: ProviderOpt = None,
    model: ModelOpt = None,
    stability: StabilityOpt = None,
    similarity: SimilarityOpt = None,
    style: StyleOpt = None,
    speaker_boost: SpeakerBoostFlag = False,
) -> None:
    """Synthesize a single text to an MP3 file."""
    _validate_voice_settings(stability, similarity, style)
    prov = get_provider(provider, model=model)
    voice, language = resolve_voice_and_language(prov, voice, language)
    request = SynthesisRequest(
        text=text,
        voice=voice,
        language=language,
        rate=rate,
        stability=stability,
        similarity=similarity,
        style=style,
        speaker_boost=speaker_boost if speaker_boost else None,
    )

    if output is None:
        output = default_output_dir() / f"{voice}_{text[:20].replace(' ', '_')}.mp3"

    client = TTSClient(prov)
    result = client.synthesize(request, output)
    _print_result(result)


# ---------------------------------------------------------------------------
# synthesize-batch
# ---------------------------------------------------------------------------


@app.command("synthesize-batch")
def synthesize_batch(
    input_file: InputFileArg,
    voice: VoiceOpt = None,
    language: LanguageOpt = None,
    rate: RateOpt = 90,
    output_dir: OutputDirOpt = None,
    merge: MergeFlag = False,
    pause: PauseOpt = 500,
    provider: ProviderOpt = None,
    model: ModelOpt = None,
    stability: StabilityOpt = None,
    similarity: SimilarityOpt = None,
    style: StyleOpt = None,
    speaker_boost: SpeakerBoostFlag = False,
) -> None:
    """Synthesize a batch of texts from a JSON file."""
    _validate_voice_settings(stability, similarity, style)
    prov = get_provider(provider, model=model)
    try:
        raw = json.loads(input_file.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise typer.BadParameter(
            "INPUT_FILE must contain valid JSON (array of strings)."
        ) from exc

    if not isinstance(raw, list):
        raise typer.BadParameter("INPUT_FILE must contain a JSON array of strings.")

    for i, item in enumerate(raw):  # pyright: ignore[reportUnknownVariableType, reportUnknownArgumentType]
        if not isinstance(item, str):
            raise typer.BadParameter(
                f"Element {i} must be a string, got {type(item).__name__}."  # pyright: ignore[reportUnknownArgumentType]
            )

    voice, language = resolve_voice_and_language(prov, voice, language)
    texts = cast("list[str]", raw)
    boost = speaker_boost if speaker_boost else None
    requests = [
        SynthesisRequest(
            text=t,
            voice=voice,
            language=language,
            rate=rate,
            stability=stability,
            similarity=similarity,
            style=style,
            speaker_boost=boost,
        )
        for t in texts
    ]
    strategy = (
        MergeStrategy.ONE_FILE_PER_BATCH if merge else MergeStrategy.ONE_FILE_PER_INPUT
    )
    out_dir = output_dir if output_dir is not None else default_output_dir()

    client = TTSClient(prov)
    results = client.synthesize_batch(requests, out_dir, strategy, pause)
    _print_results(results)


# ---------------------------------------------------------------------------
# synthesize-pair
# ---------------------------------------------------------------------------


@app.command("synthesize-pair")
def synthesize_pair(
    text1: Text1Arg,
    text2: Text2Arg,
    voice1: Voice1Opt = None,
    voice2: Voice2Opt = None,
    lang1: Lang1Opt = None,
    lang2: Lang2Opt = None,
    rate: RateOpt = 90,
    pause: PauseOpt = 500,
    output: OutputOpt = None,
    provider: ProviderOpt = None,
    model: ModelOpt = None,
    stability: StabilityOpt = None,
    similarity: SimilarityOpt = None,
    style: StyleOpt = None,
    speaker_boost: SpeakerBoostFlag = False,
) -> None:
    """Synthesize a pair of texts and stitch them with a pause."""
    _validate_voice_settings(stability, similarity, style)
    prov = get_provider(provider, model=model)
    voice1, lang1 = resolve_voice_and_language(prov, voice1, lang1)
    voice2, lang2 = resolve_voice_and_language(prov, voice2, lang2)
    boost = speaker_boost if speaker_boost else None
    req1 = SynthesisRequest(
        text=text1,
        voice=voice1,
        language=lang1,
        rate=rate,
        stability=stability,
        similarity=similarity,
        style=style,
        speaker_boost=boost,
    )
    req2 = SynthesisRequest(
        text=text2,
        voice=voice2,
        language=lang2,
        rate=rate,
        stability=stability,
        similarity=similarity,
        style=style,
        speaker_boost=boost,
    )

    if output is None:
        output = default_output_dir() / f"pair_{text1[:10]}_{text2[:10]}.mp3"

    client = TTSClient(prov)
    result = client.synthesize_pair(text1, req1, text2, req2, output, pause)
    _print_result(result)


# ---------------------------------------------------------------------------
# synthesize-pair-batch
# ---------------------------------------------------------------------------


@app.command("synthesize-pair-batch")
def synthesize_pair_batch(
    input_file: InputFileArg,
    voice1: Voice1Opt = None,
    voice2: Voice2Opt = None,
    lang1: Lang1Opt = None,
    lang2: Lang2Opt = None,
    rate: RateOpt = 90,
    pause: PauseOpt = 500,
    output_dir: OutputDirOpt = None,
    merge: MergeFlag = False,
    provider: ProviderOpt = None,
    model: ModelOpt = None,
    stability: StabilityOpt = None,
    similarity: SimilarityOpt = None,
    style: StyleOpt = None,
    speaker_boost: SpeakerBoostFlag = False,
) -> None:
    """Synthesize a batch of text pairs from a JSON file."""
    _validate_voice_settings(stability, similarity, style)
    prov = get_provider(provider, model=model)
    try:
        raw = json.loads(input_file.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise typer.BadParameter(
            "INPUT_FILE must contain valid JSON (array of [text1, text2] pairs)."
        ) from exc
    if not isinstance(raw, list):
        raise typer.BadParameter(
            "INPUT_FILE must contain a JSON array of [text1, text2] pairs."
        )

    for i, item in enumerate(raw):  # pyright: ignore[reportUnknownVariableType, reportUnknownArgumentType]
        if not isinstance(item, list) or len(item) != 2:  # pyright: ignore[reportUnknownArgumentType]
            raise typer.BadParameter(
                f"Element {i} must be a [text1, text2] pair, got {item!r}."
            )
        if not isinstance(item[0], str) or not isinstance(item[1], str):
            raise typer.BadParameter(f"Element {i} must contain strings, got {item!r}.")

    voice1, lang1 = resolve_voice_and_language(prov, voice1, lang1)
    voice2, lang2 = resolve_voice_and_language(prov, voice2, lang2)
    raw_pairs = cast("list[list[str]]", raw)
    boost = speaker_boost if speaker_boost else None
    pairs: list[tuple[SynthesisRequest, SynthesisRequest]] = [
        (
            SynthesisRequest(
                text=p[0],
                voice=voice1,
                language=lang1,
                rate=rate,
                stability=stability,
                similarity=similarity,
                style=style,
                speaker_boost=boost,
            ),
            SynthesisRequest(
                text=p[1],
                voice=voice2,
                language=lang2,
                rate=rate,
                stability=stability,
                similarity=similarity,
                style=style,
                speaker_boost=boost,
            ),
        )
        for p in raw_pairs
    ]

    strategy = (
        MergeStrategy.ONE_FILE_PER_BATCH if merge else MergeStrategy.ONE_FILE_PER_INPUT
    )
    out_dir = output_dir if output_dir is not None else default_output_dir()

    client = TTSClient(prov)
    results = client.synthesize_pair_batch(pairs, out_dir, strategy, pause)
    _print_results(results)


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

    if _json_output:
        _emit(
            {"passed": passed, "failed": failed, "checks": checks},
            "",
        )
    else:
        typer.echo("=" * 40)
        for line in lines:
            typer.echo(line)
        typer.echo("=" * 40)
        typer.echo(f"{passed} passed, {failed} failed")

    if failed > 0:
        raise typer.Exit(code=1)


# ---------------------------------------------------------------------------
# install / uninstall (Claude Code marketplace)
# ---------------------------------------------------------------------------

_PLUGIN_ID = "tts@punt-labs"


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
# mcp (replaces old "serve" command — "serve" is for HTTP, "mcp" for stdio)
# ---------------------------------------------------------------------------


@app.command()
def mcp() -> None:
    """Run the MCP server with stdio transport."""
    from punt_vox.server import run_server

    run_server()
