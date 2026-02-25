"""FastMCP server for punt-tts."""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from punt_tts import __version__
from punt_tts.core import TTSClient
from punt_tts.ephemeral import clean_ephemeral, ephemeral_output_dir
from punt_tts.logging_config import configure_logging
from punt_tts.output import default_output_dir
from punt_tts.providers import get_provider
from punt_tts.types import (
    AudioProviderId,
    MergeStrategy,
    SynthesisRequest,
    SynthesisResult,
    TTSProvider,
    generate_filename,
    result_to_dict,
    validate_language,
)

logger = logging.getLogger(__name__)

mcp = FastMCP("tts")
mcp._mcp_server.version = __version__  # pyright: ignore[reportPrivateUsage]


def _validate_voice_settings(
    stability: float | None,
    similarity: float | None,
    style: float | None,
) -> None:
    """Validate ElevenLabs voice settings are in 0.0-1.0 range."""
    for name, value in [
        ("stability", stability),
        ("similarity", similarity),
        ("style", style),
    ]:
        if value is not None and not 0.0 <= value <= 1.0:
            msg = f"{name} must be between 0.0 and 1.0, got {value}"
            raise ValueError(msg)


def _resolve_output_dir(output_dir: str | None, *, ephemeral: bool = False) -> Path:
    """Resolve an output directory, using the default if not specified.

    When *ephemeral* is True, returns the ephemeral `.tts/` directory
    in the current working directory and ignores *output_dir*.
    """
    if ephemeral:
        clean_ephemeral()
        return ephemeral_output_dir()
    if output_dir:
        return Path(output_dir)
    return default_output_dir()


def _resolve_output_path(
    output_path: str | None, output_dir: Path, default_name: str
) -> Path:
    """Resolve an output file path."""
    if output_path:
        return Path(output_path)
    return output_dir / default_name


def _resolve_voice_and_language(
    provider: TTSProvider,
    voice: str | None,
    language: str | None,
) -> tuple[str, str | None]:
    """Resolve voice and language from MCP tool input.

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


def _cached_result(
    provider: TTSProvider, request: SynthesisRequest, output_path: Path
) -> SynthesisResult:
    """Build a cached AudioResult without calling the provider."""
    provider_id = AudioProviderId(provider.name)
    return SynthesisResult(
        path=output_path,
        text=request.text,
        provider=provider_id,
        voice=request.voice,
        language=request.language,
        metadata=request.metadata,
    )


def _play_audio(path: Path) -> None:
    """Play an audio file using macOS afplay (non-blocking).

    Logs a warning and returns silently if afplay is not available
    (e.g. on Linux or Windows).
    """
    try:
        subprocess.Popen(
            ["afplay", str(path)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except FileNotFoundError:
        logger.warning("afplay not found — auto-play requires macOS")


@mcp.tool()
def synthesize(
    text: str,
    voice: str | None = None,
    language: str | None = None,
    rate: int = 90,
    auto_play: bool = True,
    output_path: str | None = None,
    output_dir: str | None = None,
    ephemeral: bool = False,
    stability: float | None = None,
    similarity: float | None = None,
    style: float | None = None,
    speaker_boost: bool | None = None,
) -> str:
    """Synthesize text to an MP3 audio file.

    Args:
        text: The text to convert to speech. With ElevenLabs eleven_v3,
            you can embed audio tags in square brackets anywhere in the
            text to control delivery — e.g. [tired], [excited], [whisper],
            [sad], [sigh], [laughs], [dramatic tone]. Tags are free-form;
            the model interprets them as performance cues. Combine with
            punctuation (ellipsis for pauses, ! for emphasis) for best
            results. Tags only work with ElevenLabs eleven_v3 model.
        voice: Voice name. Default: provider's default voice (currently
            matilda for ElevenLabs, joanna for Polly, nova for OpenAI).
            If language is provided without voice, a suitable default
            voice for that language is selected automatically.
        language: ISO 639-1 language code (e.g. 'de', 'ko', 'fr').
            Enables language-aware voice selection and validation.
            With Polly, validates voice-language compatibility.
            With ElevenLabs/OpenAI, passed through (voices are
            multilingual).
        rate: Speech rate as percentage (90 = 90% speed, good for
            language learners). Defaults to 90. ElevenLabs ignores rate;
            use audio tags like [rushed] or [drawn out] instead.
        auto_play: Open the file in the default audio player after
            synthesis. Defaults to true.
        output_path: Full path for the output file. If not provided,
            a file is auto-generated in output_dir.
        output_dir: Directory for output. Defaults to TTS_OUTPUT_DIR
            env var or ~/tts-output/.
        ephemeral: If true, write to `.tts/` in cwd and clean up
            previous ephemeral files. Ignores output_dir/output_path.
        stability: ElevenLabs voice stability (0.0-1.0). Ignored by
            other providers. Defaults to provider default.
        similarity: ElevenLabs voice similarity boost (0.0-1.0). Ignored
            by other providers. Defaults to provider default.
        style: ElevenLabs voice style/expressiveness (0.0-1.0). Ignored
            by other providers. Defaults to provider default.
        speaker_boost: ElevenLabs speaker boost toggle. Ignored by other
            providers. Defaults to provider default.

    Returns:
        JSON string with path, text, voice, and language fields.
    """
    _validate_voice_settings(stability, similarity, style)
    provider = get_provider()
    voice, language = _resolve_voice_and_language(provider, voice, language)
    request = SynthesisRequest(
        text=text,
        voice=voice,
        language=language,
        rate=rate,
        stability=stability,
        similarity=similarity,
        style=style,
        speaker_boost=speaker_boost,
    )

    dir_path = _resolve_output_dir(output_dir, ephemeral=ephemeral)
    path = _resolve_output_path(
        output_path,
        dir_path,
        f"{voice}_{text[:20].replace(' ', '_')}.mp3",
    )

    client = TTSClient(provider)
    if path.exists():
        result = _cached_result(provider, request, path)
    else:
        result = client.synthesize(request, path)
    if auto_play:
        _play_audio(result.path)
    return str(result_to_dict(result))


@mcp.tool()
def synthesize_batch(
    texts: list[str],
    voice: str | None = None,
    language: str | None = None,
    rate: int = 90,
    merge: bool = False,
    pause_ms: int = 500,
    auto_play: bool = True,
    output_dir: str | None = None,
    ephemeral: bool = False,
    stability: float | None = None,
    similarity: float | None = None,
    style: float | None = None,
    speaker_boost: bool | None = None,
) -> str:
    """Synthesize multiple texts to MP3 files.

    Args:
        texts: List of texts to synthesize. With ElevenLabs eleven_v3,
            embed audio tags like [tired], [excited], [whisper] in text.
        voice: Voice name for all texts. Default: provider's default voice
            (currently matilda for ElevenLabs, joanna for Polly, nova for
            OpenAI). If language is provided without voice, auto-selects.
        language: ISO 639-1 language code (e.g. 'de', 'ko').
        rate: Speech rate as percentage. Defaults to 90.
        merge: If true, produce one merged file instead of separate
            files per text. Defaults to false.
        pause_ms: Pause between segments in milliseconds when merging.
            Defaults to 500.
        auto_play: Open the file(s) in the default audio player after
            synthesis. Defaults to true.
        output_dir: Directory for output files. Defaults to
            TTS_OUTPUT_DIR env var or ~/tts-output/.
        ephemeral: If true, write to `.tts/` in cwd and clean up
            previous ephemeral files. Ignores output_dir.
        stability: ElevenLabs voice stability (0.0-1.0).
        similarity: ElevenLabs voice similarity boost (0.0-1.0).
        style: ElevenLabs voice style/expressiveness (0.0-1.0).
        speaker_boost: ElevenLabs speaker boost toggle.

    Returns:
        JSON string with list of results, each containing path,
        text, voice, and language fields.
    """
    _validate_voice_settings(stability, similarity, style)
    provider = get_provider()
    voice, language = _resolve_voice_and_language(provider, voice, language)
    requests = [
        SynthesisRequest(
            text=t,
            voice=voice,
            language=language,
            rate=rate,
            stability=stability,
            similarity=similarity,
            style=style,
            speaker_boost=speaker_boost,
        )
        for t in texts
    ]
    if not requests:
        return str([])
    dir_path = _resolve_output_dir(output_dir, ephemeral=ephemeral)

    client = TTSClient(provider)
    results: list[SynthesisResult]
    if merge:
        combined_text = " | ".join(r.text for r in requests)
        out_path = dir_path / generate_filename(combined_text, prefix="batch_")
        if out_path.exists():
            cached = SynthesisResult(
                path=out_path,
                text=combined_text,
                provider=AudioProviderId(provider.name),
                voice=voice,
                language=language,
                metadata=requests[0].metadata,
            )
            results = [cached]
        else:
            results = client.synthesize_batch(
                requests, dir_path, MergeStrategy.ONE_FILE_PER_BATCH, pause_ms
            )
    else:
        results = []
        for req in requests:
            out_path = dir_path / generate_filename(req.text)
            if out_path.exists():
                results.append(_cached_result(provider, req, out_path))
            else:
                results.append(client.synthesize(req, out_path))
    if auto_play:
        for r in results:
            _play_audio(r.path)
    return str([result_to_dict(r) for r in results])


@mcp.tool()
def synthesize_pair(
    text1: str,
    text2: str,
    voice1: str | None = None,
    voice2: str | None = None,
    lang1: str | None = None,
    lang2: str | None = None,
    rate: int = 90,
    pause_ms: int = 500,
    auto_play: bool = True,
    output_path: str | None = None,
    output_dir: str | None = None,
    ephemeral: bool = False,
    stability: float | None = None,
    similarity: float | None = None,
    style: float | None = None,
    speaker_boost: bool | None = None,
) -> str:
    """Synthesize a pair of texts and stitch them into one MP3.

    Creates [text1 audio] [pause] [text2 audio]. Use for language
    learning pairs like "strong" (English) + "stark" (German).

    Args:
        text1: First text (typically English). With ElevenLabs eleven_v3,
            embed audio tags like [tired], [excited], [whisper] in text.
        text2: Second text (typically target language). Same audio tag
            support as text1.
        voice1: Voice for text1. Defaults to provider's default voice.
            If lang1 is provided without voice1, auto-selects.
        voice2: Voice for text2. Defaults to provider's default voice.
            If lang2 is provided without voice2, auto-selects.
        lang1: ISO 639-1 language code for text1 (e.g. 'en').
        lang2: ISO 639-1 language code for text2 (e.g. 'de').
        rate: Speech rate as percentage. Defaults to 90.
        pause_ms: Pause between the two texts in milliseconds.
            Defaults to 500.
        auto_play: Play the audio after synthesis. Defaults to true.
        output_path: Full path for the output file.
        output_dir: Directory for output. Defaults to
            TTS_OUTPUT_DIR env var or ~/tts-output/.
        ephemeral: If true, write to `.tts/` in cwd and clean up
            previous ephemeral files. Ignores output_dir/output_path.
        stability: ElevenLabs voice stability (0.0-1.0).
        similarity: ElevenLabs voice similarity boost (0.0-1.0).
        style: ElevenLabs voice style/expressiveness (0.0-1.0).
        speaker_boost: ElevenLabs speaker boost toggle.

    Returns:
        JSON string with path, text, voice, and language fields.
    """
    _validate_voice_settings(stability, similarity, style)
    provider = get_provider()
    voice1, lang1 = _resolve_voice_and_language(provider, voice1, lang1)
    voice2, lang2 = _resolve_voice_and_language(provider, voice2, lang2)
    req1 = SynthesisRequest(
        text=text1,
        voice=voice1,
        language=lang1,
        rate=rate,
        stability=stability,
        similarity=similarity,
        style=style,
        speaker_boost=speaker_boost,
    )
    req2 = SynthesisRequest(
        text=text2,
        voice=voice2,
        language=lang2,
        rate=rate,
        stability=stability,
        similarity=similarity,
        style=style,
        speaker_boost=speaker_boost,
    )

    dir_path = _resolve_output_dir(output_dir, ephemeral=ephemeral)
    path = _resolve_output_path(
        output_path,
        dir_path,
        f"pair_{text1[:10]}_{text2[:10]}.mp3",
    )

    client = TTSClient(provider)
    if path.exists():
        voice_parts = [v for v in (voice1, voice2) if v]
        combined_voice = "+".join(voice_parts) if voice_parts else None
        result = SynthesisResult(
            path=path,
            text=f"{text1} | {text2}",
            provider=AudioProviderId(provider.name),
            voice=combined_voice,
            language=lang1,
            metadata=req1.metadata,
        )
    else:
        result = client.synthesize_pair(text1, req1, text2, req2, path, pause_ms)
    if auto_play:
        _play_audio(result.path)
    return str(result_to_dict(result))


@mcp.tool()
def synthesize_pair_batch(
    pairs: list[list[str]],
    voice1: str | None = None,
    voice2: str | None = None,
    lang1: str | None = None,
    lang2: str | None = None,
    rate: int = 90,
    pause_ms: int = 500,
    merge: bool = False,
    auto_play: bool = True,
    output_dir: str | None = None,
    ephemeral: bool = False,
    stability: float | None = None,
    similarity: float | None = None,
    style: float | None = None,
    speaker_boost: bool | None = None,
) -> str:
    """Synthesize multiple text pairs and stitch each into MP3 files.

    Each pair becomes [text1 audio] [pause] [text2 audio]. Use for
    vocabulary lists like [["strong","stark"], ["house","Haus"]].

    Args:
        pairs: List of [text1, text2] pairs. With ElevenLabs eleven_v3,
            texts can include audio tags like [tired], [excited].
        voice1: Voice for all first texts. Defaults to provider's default.
            If lang1 is provided without voice1, auto-selects.
        voice2: Voice for all second texts. Defaults to provider's default.
            If lang2 is provided without voice2, auto-selects.
        lang1: ISO 639-1 language code for first texts (e.g. 'en').
        lang2: ISO 639-1 language code for second texts (e.g. 'de').
        rate: Speech rate as percentage. Defaults to 90.
        pause_ms: Pause between pair segments in milliseconds.
            Defaults to 500.
        merge: If true, produce one merged file instead of separate
            files per pair. Defaults to false.
        auto_play: Play the audio after synthesis. Defaults to true.
        output_dir: Directory for output files. Defaults to
            TTS_OUTPUT_DIR env var or ~/tts-output/.
        ephemeral: If true, write to `.tts/` in cwd and clean up
            previous ephemeral files. Ignores output_dir.
        stability: ElevenLabs voice stability (0.0-1.0).
        similarity: ElevenLabs voice similarity boost (0.0-1.0).
        style: ElevenLabs voice style/expressiveness (0.0-1.0).
        speaker_boost: ElevenLabs speaker boost toggle.

    Returns:
        JSON string with list of results.
    """
    _validate_voice_settings(stability, similarity, style)
    provider = get_provider()
    voice1, lang1 = _resolve_voice_and_language(provider, voice1, lang1)
    voice2, lang2 = _resolve_voice_and_language(provider, voice2, lang2)

    pair_requests: list[tuple[SynthesisRequest, SynthesisRequest]] = [
        (
            SynthesisRequest(
                text=p[0],
                voice=voice1,
                language=lang1,
                rate=rate,
                stability=stability,
                similarity=similarity,
                style=style,
                speaker_boost=speaker_boost,
            ),
            SynthesisRequest(
                text=p[1],
                voice=voice2,
                language=lang2,
                rate=rate,
                stability=stability,
                similarity=similarity,
                style=style,
                speaker_boost=speaker_boost,
            ),
        )
        for p in pairs
    ]
    if not pair_requests:
        return str([])

    dir_path = _resolve_output_dir(output_dir, ephemeral=ephemeral)

    client = TTSClient(provider)
    results: list[SynthesisResult]
    if merge:
        all_texts = " | ".join(f"{r1.text}-{r2.text}" for r1, r2 in pair_requests)
        out_path = dir_path / generate_filename(all_texts, prefix="pairs_")
        if out_path.exists():
            results = [
                SynthesisResult(
                    path=out_path,
                    text=all_texts,
                    provider=AudioProviderId(provider.name),
                    voice="mixed",
                    metadata=pair_requests[0][0].metadata,
                )
            ]
        else:
            results = client.synthesize_pair_batch(
                pair_requests, dir_path, MergeStrategy.ONE_FILE_PER_BATCH, pause_ms
            )
    else:
        results = []
        for req_1, req_2 in pair_requests:
            combined = f"{req_1.text}_{req_2.text}"
            out_path = dir_path / generate_filename(combined, prefix="pair_")
            if out_path.exists():
                voice_parts = [v for v in (req_1.voice, req_2.voice) if v]
                combined_voice = "+".join(voice_parts) if voice_parts else None
                results.append(
                    SynthesisResult(
                        path=out_path,
                        text=f"{req_1.text} | {req_2.text}",
                        provider=AudioProviderId(provider.name),
                        voice=combined_voice,
                        language=req_1.language,
                        metadata=req_1.metadata,
                    )
                )
            else:
                results.append(
                    client.synthesize_pair(
                        req_1.text,
                        req_1,
                        req_2.text,
                        req_2,
                        out_path,
                        pause_ms,
                    )
                )
    if auto_play:
        for r in results:
            _play_audio(r.path)
    return str([result_to_dict(r) for r in results])


def run_server() -> None:
    """Run the MCP server with stdio transport."""
    # MCP stdio servers must not write to stdout; stderr handler is safe.
    configure_logging(stderr_level="INFO")
    logger.info("Starting tts MCP server")
    mcp.run(transport="stdio")


if __name__ == "__main__":
    run_server()
