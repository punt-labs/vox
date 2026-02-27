"""Click CLI for punt-tts."""

from __future__ import annotations

import json
import logging
import os
import platform
import shutil
import sys
from collections.abc import Callable
from pathlib import Path
from typing import cast

import click

from punt_tts.core import TTSClient
from punt_tts.output import default_output_dir
from punt_tts.providers import DEFAULT_VOICES, auto_detect_provider, get_provider
from punt_tts.types import (
    MergeStrategy,
    SynthesisRequest,
    SynthesisResult,
    TTSProvider,
    result_to_dict,
    validate_language,
)

logger = logging.getLogger(__name__)

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

json_output_enabled = False


def _emit(payload: object, text: str) -> None:
    if json_output_enabled:
        click.echo(json.dumps(payload))
    else:
        click.echo(text)


def _configure_logging(verbose: bool) -> None:
    from punt_tts.logging_config import configure_logging

    configure_logging(stderr_level="DEBUG" if verbose else "WARNING")


def _print_result(result: SynthesisResult) -> None:
    payload = result_to_dict(result)
    _emit(payload, f"{result.path}")


def _print_results(results: list[SynthesisResult]) -> None:
    if json_output_enabled:
        payload = [result_to_dict(r) for r in results]
        _emit(payload, "")
        return
    for r in results:
        _print_result(r)


def _get_provider(ctx: click.Context) -> TTSProvider:
    """Retrieve the TTSProvider from the Click context, initializing lazily.

    The group callback stores provider_name and model but does NOT create
    the provider — that way subcommands like ``install`` and ``serve``
    never touch the provider layer.
    """
    obj = cast("dict[str, object]", ctx.ensure_object(dict))  # pyright: ignore[reportUnknownMemberType]
    cached = obj.get("provider")
    if cached is not None:
        return cast("TTSProvider", cached)
    provider_name = cast("str | None", obj.get("provider_name"))
    model = cast("str | None", obj.get("model"))
    provider = get_provider(provider_name, model=model)
    obj["provider"] = provider
    return provider


def _resolve_voice_and_language(
    provider: TTSProvider,
    voice: str | None,
    language: str | None,
) -> tuple[str, str | None]:
    """Resolve voice and language from user input.

    If only language is provided, selects the provider's default voice for it.
    If only voice is provided, infers language from the voice (best-effort).
    If both, validates compatibility.
    """
    if language is not None:
        language = validate_language(language)

    if voice is None and language is not None:
        voice = provider.get_default_voice(language)
    elif voice is None:
        voice = provider.default_voice

    if language is not None:
        provider.resolve_voice(voice, language)
    else:
        provider.resolve_voice(voice)
        language = provider.infer_language_from_voice(voice)

    return voice, language


def _voice_settings_options[F: Callable[..., object]](fn: F) -> F:
    """Shared ElevenLabs voice-settings options for synthesis commands."""
    for decorator in reversed(
        [
            click.option(
                "--stability",
                default=None,
                type=click.FloatRange(0.0, 1.0),
                help="ElevenLabs voice stability (0.0-1.0).",
            ),
            click.option(
                "--similarity",
                default=None,
                type=click.FloatRange(0.0, 1.0),
                help="ElevenLabs voice similarity boost (0.0-1.0).",
            ),
            click.option(
                "--style",
                default=None,
                type=click.FloatRange(0.0, 1.0),
                help="ElevenLabs voice style/expressiveness (0.0-1.0).",
            ),
            click.option(
                "--speaker-boost",
                is_flag=True,
                default=False,
                help="Enable ElevenLabs speaker boost.",
            ),
        ]
    ):
        fn = decorator(fn)  # pyright: ignore[reportAssignmentType]
    return fn


@click.group()
@click.option("--verbose", "-v", is_flag=True, help="Enable debug logging.")
@click.option("--json", "json_output", is_flag=True, help="Output JSON.")
@click.option(
    "--provider",
    "provider_name",
    default=None,
    envvar="TTS_PROVIDER",
    help="TTS provider (elevenlabs, polly, openai, say, espeak). Default: auto-detect.",
)
@click.option(
    "--model",
    default=None,
    envvar="TTS_MODEL",
    help="Model name (e.g. eleven_v3, tts-1, tts-1-hd). Provider-specific.",
)
@click.pass_context
def main(
    ctx: click.Context,
    verbose: bool,
    json_output: bool,
    provider_name: str | None,
    model: str | None,
) -> None:
    """tts: Text-to-speech CLI."""
    global json_output_enabled
    json_output_enabled = json_output
    _configure_logging(verbose)
    ctx.ensure_object(dict)
    ctx.obj["provider_name"] = provider_name
    ctx.obj["model"] = model


@main.command()
@click.argument("text")
@click.option(
    "--voice",
    default=None,
    help=f"Voice name. Default: {_VOICE_DEFAULTS}.",
)
@click.option(
    "--language",
    "--lang",
    default=None,
    help="ISO 639-1 language code (e.g. de, ko). Auto-selects voice if omitted.",
)
@click.option(
    "--rate",
    default=90,
    show_default=True,
    type=int,
    help="Speech rate as percentage (e.g. 90 = 90%% speed). ElevenLabs ignores this.",
)
@click.option(
    "--output",
    "-o",
    default=None,
    type=click.Path(path_type=Path),
    help="Output file path. Defaults to auto-generated name in ~/tts-output.",
)
@_voice_settings_options
@click.pass_context
def synthesize(
    ctx: click.Context,
    text: str,
    voice: str | None,
    language: str | None,
    rate: int,
    output: Path | None,
    stability: float | None,
    similarity: float | None,
    style: float | None,
    speaker_boost: bool,
) -> None:
    """Synthesize a single text to an MP3 file.

    With ElevenLabs eleven_v3, embed audio tags like [tired], [excited],
    [whisper], [laughs] to control delivery.
    """
    provider = _get_provider(ctx)
    voice, language = _resolve_voice_and_language(provider, voice, language)
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

    client = TTSClient(provider)
    result = client.synthesize(request, output)
    _print_result(result)


@main.command("synthesize-batch")
@click.option(
    "--voice",
    default=None,
    help=(f"Voice name for all texts. Default: {_VOICE_DEFAULTS}."),
)
@click.option(
    "--language",
    "--lang",
    default=None,
    help="ISO 639-1 language code (e.g. de, ko). Auto-selects voice if omitted.",
)
@click.option(
    "--rate",
    default=90,
    show_default=True,
    type=int,
    help="Speech rate as percentage. ElevenLabs ignores this.",
)
@click.option(
    "--output-dir",
    "-d",
    default=None,
    type=click.Path(path_type=Path),
    help="Output directory. Defaults to ~/tts-output.",
)
@click.option(
    "--merge",
    is_flag=True,
    default=False,
    help="Merge all outputs into a single file.",
)
@click.option(
    "--pause",
    default=500,
    show_default=True,
    type=int,
    help="Pause between segments in ms (used with --merge).",
)
@_voice_settings_options
@click.argument("input_file", type=click.Path(exists=True, path_type=Path))
@click.pass_context
def synthesize_batch(
    ctx: click.Context,
    voice: str | None,
    language: str | None,
    rate: int,
    output_dir: Path | None,
    merge: bool,
    pause: int,
    stability: float | None,
    similarity: float | None,
    style: float | None,
    speaker_boost: bool,
    input_file: Path,
) -> None:
    """Synthesize a batch of texts from a JSON file.

    INPUT_FILE should contain a JSON array of strings, e.g.:
    ["hello", "world", "good morning"]
    """
    provider = _get_provider(ctx)
    try:
        raw = json.loads(input_file.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise click.BadParameter(
            "INPUT_FILE must contain valid JSON (array of strings)."
        ) from exc

    if not isinstance(raw, list):
        raise click.BadParameter("INPUT_FILE must contain a JSON array of strings.")

    for i, item in enumerate(raw):  # pyright: ignore[reportUnknownVariableType, reportUnknownArgumentType]
        if not isinstance(item, str):
            raise click.BadParameter(
                f"Element {i} must be a string, got {type(item).__name__}."  # pyright: ignore[reportUnknownArgumentType]
            )

    voice, language = _resolve_voice_and_language(provider, voice, language)
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

    client = TTSClient(provider)
    results = client.synthesize_batch(requests, out_dir, strategy, pause)
    _print_results(results)


@main.command("synthesize-pair")
@click.argument("text1")
@click.argument("text2")
@click.option(
    "--voice1",
    default=None,
    help="Voice for first text (typically English). Default: provider's default voice.",
)
@click.option(
    "--voice2",
    default=None,
    help="Voice for the second text (typically L2). Default: provider's default voice.",
)
@click.option(
    "--lang1",
    default=None,
    help="ISO 639-1 language for first text (e.g. en).",
)
@click.option(
    "--lang2",
    default=None,
    help="ISO 639-1 language for second text (e.g. de).",
)
@click.option(
    "--rate",
    default=90,
    show_default=True,
    type=int,
    help="Speech rate as percentage. ElevenLabs ignores this.",
)
@click.option(
    "--pause",
    default=500,
    show_default=True,
    type=int,
    help="Pause between the two texts in ms.",
)
@click.option(
    "--output",
    "-o",
    default=None,
    type=click.Path(path_type=Path),
    help="Output file path.",
)
@_voice_settings_options
@click.pass_context
def synthesize_pair(
    ctx: click.Context,
    text1: str,
    text2: str,
    voice1: str | None,
    voice2: str | None,
    lang1: str | None,
    lang2: str | None,
    rate: int,
    pause: int,
    output: Path | None,
    stability: float | None,
    similarity: float | None,
    style: float | None,
    speaker_boost: bool,
) -> None:
    """Synthesize a pair of texts and stitch them with a pause.

    Creates [TEXT1 audio] [pause] [TEXT2 audio] in a single MP3.
    """
    provider = _get_provider(ctx)
    voice1, lang1 = _resolve_voice_and_language(provider, voice1, lang1)
    voice2, lang2 = _resolve_voice_and_language(provider, voice2, lang2)
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

    client = TTSClient(provider)
    result = client.synthesize_pair(text1, req1, text2, req2, output, pause)
    _print_result(result)


@main.command("synthesize-pair-batch")
@click.option(
    "--voice1",
    default=None,
    help=(
        "Voice for first texts (typically English). Default: provider's default voice."
    ),
)
@click.option(
    "--voice2",
    default=None,
    help="Voice for second texts (typically L2). Default: provider's default voice.",
)
@click.option(
    "--lang1",
    default=None,
    help="ISO 639-1 language for first texts (e.g. en).",
)
@click.option(
    "--lang2",
    default=None,
    help="ISO 639-1 language for second texts (e.g. de).",
)
@click.option(
    "--rate",
    default=90,
    show_default=True,
    type=int,
    help="Speech rate as percentage. ElevenLabs ignores this.",
)
@click.option(
    "--pause",
    default=500,
    show_default=True,
    type=int,
    help="Pause between pair segments in ms.",
)
@click.option(
    "--output-dir",
    "-d",
    default=None,
    type=click.Path(path_type=Path),
    help="Output directory. Defaults to ~/tts-output.",
)
@click.option(
    "--merge",
    is_flag=True,
    default=False,
    help="Merge all pair outputs into a single file.",
)
@_voice_settings_options
@click.argument("input_file", type=click.Path(exists=True, path_type=Path))
@click.pass_context
def synthesize_pair_batch(
    ctx: click.Context,
    voice1: str | None,
    voice2: str | None,
    lang1: str | None,
    lang2: str | None,
    rate: int,
    pause: int,
    output_dir: Path | None,
    merge: bool,
    stability: float | None,
    similarity: float | None,
    style: float | None,
    speaker_boost: bool,
    input_file: Path,
) -> None:
    """Synthesize a batch of text pairs from a JSON file.

    INPUT_FILE should contain a JSON array of [text1, text2] pairs:
    [["strong", "stark"], ["house", "Haus"]]
    """
    provider = _get_provider(ctx)
    try:
        raw = json.loads(input_file.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise click.BadParameter(
            "INPUT_FILE must contain valid JSON (array of [text1, text2] pairs)."
        ) from exc
    if not isinstance(raw, list):
        raise click.BadParameter(
            "INPUT_FILE must contain a JSON array of [text1, text2] pairs."
        )

    for i, item in enumerate(raw):  # pyright: ignore[reportUnknownVariableType, reportUnknownArgumentType]
        if not isinstance(item, list) or len(item) != 2:  # pyright: ignore[reportUnknownArgumentType]
            raise click.BadParameter(
                f"Element {i} must be a [text1, text2] pair, got {item!r}."
            )
        if not isinstance(item[0], str) or not isinstance(item[1], str):
            raise click.BadParameter(f"Element {i} must contain strings, got {item!r}.")

    voice1, lang1 = _resolve_voice_and_language(provider, voice1, lang1)
    voice2, lang2 = _resolve_voice_and_language(provider, voice2, lang2)
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

    client = TTSClient(provider)
    results = client.synthesize_pair_batch(pairs, out_dir, strategy, pause)
    _print_results(results)


# ---------------------------------------------------------------------------
# doctor
# ---------------------------------------------------------------------------

_PASS = "✓"
_FAIL = "✗"
_OPTIONAL = "○"


def _claude_desktop_config_path() -> Path:
    """Return the Claude Desktop config file path (macOS only)."""
    return (
        Path.home()
        / "Library"
        / "Application Support"
        / "Claude"
        / "claude_desktop_config.json"
    )


@main.command()
@click.pass_context
def doctor(ctx: click.Context) -> None:
    """Check system health for tts."""
    provider = _get_provider(ctx)
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
            " — install from https://www.python.org/downloads/",
        )

    # Active provider
    _check(_PASS, f"Provider: {provider.name}")

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
        _check(_FAIL, f"ffmpeg: not found — {hint}")

    # Provider-specific health checks
    for check in provider.check_health():
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
                "espeak-ng/espeak: not found — install for offline TTS:"
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

        # MCP server registered (optional)
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
                    "Claude Desktop MCP: not registered (run 'tts install-desktop')",
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
            "Claude Desktop MCP: not registered (run 'tts install-desktop')",
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
            " — check permissions or use --output-dir",
        )

    if json_output_enabled:
        _emit(
            {"passed": passed, "failed": failed, "checks": checks},
            "",
        )
    else:
        # Print report
        click.echo("=" * 40)
        for line in lines:
            click.echo(line)
        click.echo("=" * 40)
        click.echo(f"{passed} passed, {failed} failed")

    if failed > 0:
        raise SystemExit(1)


# ---------------------------------------------------------------------------
# install / uninstall (Claude Code marketplace)
# ---------------------------------------------------------------------------

_PLUGIN_ID = "tts@punt-labs"


@main.command()
def install() -> None:
    """Install the Claude Code plugin via the punt-labs marketplace."""
    import shutil
    import subprocess

    claude = shutil.which("claude")
    if not claude:
        raise click.ClickException("claude CLI not found on PATH")

    result = subprocess.run(
        [claude, "plugin", "install", _PLUGIN_ID, "--scope", "user"],
        check=False,
    )
    if result.returncode != 0:
        raise click.ClickException("plugin install failed")
    _emit({"installed": True}, "Installed. Restart Claude Code to activate.")


@main.command()
def uninstall() -> None:
    """Uninstall the Claude Code plugin."""
    import shutil
    import subprocess

    claude = shutil.which("claude")
    if not claude:
        raise click.ClickException("claude CLI not found on PATH")

    result = subprocess.run(
        [claude, "plugin", "uninstall", _PLUGIN_ID, "--scope", "user"],
        check=False,
    )
    if result.returncode != 0:
        raise click.ClickException("plugin uninstall failed")
    _emit({"uninstalled": True}, "Uninstalled.")


# ---------------------------------------------------------------------------
# install-desktop (Claude Desktop MCP server registration)
# ---------------------------------------------------------------------------


def _detect_install_provider(provider_name: str | None) -> str:
    """Detect the provider to configure for install.

    If explicit, use it. Otherwise delegates to auto_detect_provider().
    """
    if provider_name:
        return provider_name.lower()
    return auto_detect_provider()


def _build_install_env(provider: str, audio_dir: Path) -> dict[str, str]:
    """Build the env dict for the MCP server config entry.

    Claude Desktop does not support env var interpolation (``${VAR}``),
    so literal values are written. The API key is required because the
    MCP server subprocess does not inherit the user's shell environment.
    """
    env: dict[str, str] = {
        "TTS_PROVIDER": provider,
        "TTS_OUTPUT_DIR": str(audio_dir),
    }
    if provider == "elevenlabs":
        key = os.environ.get("ELEVENLABS_API_KEY")
        if not key:
            raise click.ClickException(
                "ELEVENLABS_API_KEY is not set."
                " Export it or use --provider polly/openai/say/espeak."
            )
        env["ELEVENLABS_API_KEY"] = key
    elif provider == "openai":
        key = os.environ.get("OPENAI_API_KEY")
        if not key:
            raise click.ClickException(
                "OPENAI_API_KEY is not set."
                " Export it or use --provider polly/say/espeak."
            )
        env["OPENAI_API_KEY"] = key
    return env


@main.command("install-desktop")
@click.option(
    "--output-dir",
    default=None,
    type=click.Path(path_type=Path),
    help="Output directory for synthesized audio. Default: ~/tts-output",
)
@click.option(
    "--uvx-path",
    default=None,
    help="Path to uvx binary. Default: auto-detect via shutil.which.",
)
@click.option(
    "--provider",
    "install_provider",
    default=None,
    help="TTS provider (elevenlabs, polly, openai, say, espeak). Default: auto-detect.",
)
def install_desktop(
    output_dir: Path | None, uvx_path: str | None, install_provider: str | None
) -> None:
    """Register the MCP server with Claude Desktop.

    Writes a tts entry to the Claude Desktop config file at
    ~/Library/Application Support/Claude/claude_desktop_config.json (macOS).
    The entry includes the uvx command, provider name, output directory, and
    API key (for ElevenLabs/OpenAI) as environment variables. Requires a
    Claude Desktop restart to take effect.
    """
    if platform.system() != "Darwin":
        click.echo(
            "Warning: Claude Desktop config path is only known for macOS. "
            "You may need to configure manually on this platform.",
            err=True,
        )

    # Resolve uvx
    uvx = uvx_path or shutil.which("uvx")
    if not uvx:
        raise click.ClickException(
            "uvx not found. Install uv (https://docs.astral.sh/uv/) first."
        )

    # Resolve output directory
    audio_dir = output_dir or default_output_dir()
    audio_dir.mkdir(parents=True, exist_ok=True)

    # Detect provider and build env
    detected = _detect_install_provider(install_provider)
    env = _build_install_env(detected, audio_dir)

    # Read or create config
    config_path = _claude_desktop_config_path()
    config_path.parent.mkdir(parents=True, exist_ok=True)

    if config_path.exists():
        try:
            data = json.loads(config_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            raise click.ClickException(f"Could not read {config_path}: {e}") from e
    else:
        data = {}

    if "mcpServers" not in data:
        data["mcpServers"] = {}

    overwriting = "tts" in data["mcpServers"]

    data["mcpServers"]["tts"] = {
        "command": uvx,
        "args": ["--from", "punt-tts", "tts-server"],
        "env": env,
    }

    config_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")

    if overwriting:
        click.echo("Updated existing tts entry.")
    else:
        click.echo("Registered tts MCP server.")

    click.echo(f"Provider: {detected}")
    click.echo(f"Config: {config_path}")
    click.echo(f"Output: {audio_dir}")
    click.echo("Restart Claude Desktop to activate.")


# ---------------------------------------------------------------------------
# play
# ---------------------------------------------------------------------------


@main.command()
@click.argument("audio_file", type=click.Path(exists=True, path_type=Path))
def play(audio_file: Path) -> None:
    """Play an audio file with serialized flock-based queuing."""
    from punt_tts.playback import play_audio

    play_audio(audio_file)


# ---------------------------------------------------------------------------
# serve
# ---------------------------------------------------------------------------


@main.command()
def serve() -> None:
    """Run the MCP server with stdio transport."""
    from punt_tts.server import run_server

    run_server()
