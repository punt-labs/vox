"""FastMCP server for punt-vox — mic API."""

from __future__ import annotations

import json
import logging
import random
import tempfile
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from punt_vox import __version__
from punt_vox.config import read_field, write_fields
from punt_vox.core import TTSClient, stitch_audio
from punt_vox.logging_config import configure_logging
from punt_vox.playback import enqueue as _enqueue_audio
from punt_vox.providers import get_provider
from punt_vox.resolve import (
    apply_vibe,
    resolve_output_dir,
    resolve_voice_and_language,
)
from punt_vox.types import (
    AudioProviderId,
    MergeStrategy,
    SynthesisRequest,
    SynthesisResult,
    TTSProvider,
    VoiceNotFoundError,
    generate_filename,
    result_to_dict,
    validate_language,
)
from punt_vox.voices import VOICE_BLURBS, voice_not_found_message

logger = logging.getLogger(__name__)

mcp = FastMCP(
    "mic",
    instructions=(
        "Vox is a text-to-speech engine. Use these tools to speak text aloud "
        "and generate audio files.\n\n"
        "When a stop hook blocks with a ♪ phrase (e.g. '♪ Speaking my thoughts...'), "
        "first use the Read tool to read .vox/config.md and check the vibe, "
        "vibe_mode, and vibe_signals fields. If vibe_mode is 'manual', treat "
        "vibe as your primary mood hint. If vibe_mode is 'auto' and "
        "vibe_signals is non-empty, interpret those signals to pick mood. Then "
        "write 1-2 sentences summarizing what you completed and call the unmute "
        'tool with ephemeral=true and vibe_tags="[tag1] [tag2]" to set mood '
        "and speak in one call. No other output."
    ),
)
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


_CONFIG_PATH = Path(".vox/config.md")


# ---------------------------------------------------------------------------
# Segment processing — shared by unmute and record
# ---------------------------------------------------------------------------


def _build_requests(
    segments: list[dict[str, str]],
    default_voice: str | None,
    default_language: str | None,
    provider: TTSProvider,
    *,
    rate: int,
    stability: float | None,
    similarity: float | None,
    style: float | None,
    speaker_boost: bool | None,
) -> list[SynthesisRequest]:
    """Convert segment dicts to SynthesisRequest objects.

    Each segment has ``text`` (required) and optional ``voice`` and
    ``language``.  The *default_voice* and *default_language* are used
    when a segment omits those fields.

    Raises:
        VoiceNotFoundError: If a voice cannot be resolved.
        ValueError: If a language code is invalid or voice/language
            are incompatible.
    """
    requests: list[SynthesisRequest] = []
    for seg in segments:
        seg_voice = seg.get("voice") or default_voice
        seg_language = seg.get("language") or default_language
        seg_text = seg.get("text", "")
        if not seg_text:
            continue

        resolved_voice, language = resolve_voice_and_language(
            provider, seg_voice, seg_language
        )

        seg_text = apply_vibe(
            seg_text, expressive_tags=provider.supports_expressive_tags
        )

        requests.append(
            SynthesisRequest(
                text=seg_text,
                voice=resolved_voice,
                language=language,
                rate=rate,
                stability=stability,
                similarity=similarity,
                style=style,
                speaker_boost=speaker_boost,
            )
        )
    return requests


def _synthesize_segments(
    requests: list[SynthesisRequest],
    provider: TTSProvider,
    output_dir: Path,
    pause_ms: int,
) -> list[SynthesisResult]:
    """Synthesize a list of requests, stitching into one file if multiple."""
    client = TTSClient(provider)

    if len(requests) == 1:
        req = requests[0]
        out_path = output_dir / generate_filename(req.text)
        if out_path.exists():
            return [
                SynthesisResult(
                    path=out_path,
                    text=req.text,
                    provider=AudioProviderId(provider.name),
                    voice=req.voice,
                    language=req.language,
                    metadata=req.metadata,
                )
            ]
        return [client.synthesize(req, out_path)]

    # Multiple segments → stitch into one file
    combined_text = " | ".join(r.text for r in requests)
    out_path = output_dir / generate_filename(combined_text, prefix="batch_")

    voices = {r.voice for r in requests}
    languages = {r.language for r in requests}
    voice = next(iter(voices)) if len(voices) == 1 else "mixed"
    language = next(iter(languages)) if len(languages) == 1 else "mixed"

    if out_path.exists():
        return [
            SynthesisResult(
                path=out_path,
                text=combined_text,
                provider=AudioProviderId(provider.name),
                voice=voice,
                language=language,
            )
        ]
    return client.synthesize_batch(
        requests, output_dir, MergeStrategy.ONE_FILE_PER_BATCH, pause_ms
    )


# ---------------------------------------------------------------------------
# MCP tools
# ---------------------------------------------------------------------------


@mcp.tool()
def unmute(
    text: str | None = None,
    voice: str | None = None,
    language: str | None = None,
    segments: list[dict[str, str]] | None = None,
    rate: int = 90,
    pause_ms: int = 500,
    ephemeral: bool = True,
    stability: float | None = None,
    similarity: float | None = None,
    style: float | None = None,
    speaker_boost: bool | None = None,
    vibe_tags: str | None = None,
) -> str:
    """Synthesize and play audio sequentially.

    Pass either a simple ``text`` string or a ``segments`` list for
    multi-voice sequential playback. Top-level ``voice`` is the default;
    per-segment ``voice`` overrides it.

    Args:
        text: Simple text to speak. Ignored when segments is provided.
        voice: Default voice for all segments. If omitted, uses the
            session voice or provider default.
        language: Default ISO 639-1 language code (e.g. 'de', 'ko').
            Per-segment "language" overrides this.
        segments: List of segment objects, each with "text" (required)
            and optional "voice" and "language". Example:
            [{"voice": "roger", "text": "Hello."}, {"text": "Hi."}]
        rate: Speech rate as percentage. Defaults to 90.
        pause_ms: Pause between segments in milliseconds. Defaults to 500.
        ephemeral: Write to .vox/ in cwd and clean up previous files.
            Defaults to true (unmute is for playback, not saving).
        stability: ElevenLabs voice stability (0.0-1.0).
        similarity: ElevenLabs voice similarity boost (0.0-1.0).
        style: ElevenLabs voice style/expressiveness (0.0-1.0).
        speaker_boost: ElevenLabs speaker boost toggle.
        vibe_tags: ElevenLabs expressive tags (e.g. "[warm] [satisfied]").
            When provided, writes tags to config and clears vibe_signals.

    Returns:
        JSON string with synthesis results.
    """
    _validate_voice_settings(stability, similarity, style)
    if vibe_tags is not None:
        write_fields({"vibe_tags": vibe_tags, "vibe_signals": ""}, _CONFIG_PATH)

    # Normalize input: text → single segment
    if segments is None:
        if text is None:
            return json.dumps({"error": "Provide text or segments."})
        segments = [{"text": text}]

    provider = get_provider()
    try:
        requests = _build_requests(
            segments,
            voice,
            language,
            provider,
            rate=rate,
            stability=stability,
            similarity=similarity,
            style=style,
            speaker_boost=speaker_boost,
        )
    except VoiceNotFoundError as exc:
        return json.dumps({"error": voice_not_found_message(exc)})
    except ValueError as exc:
        return json.dumps({"error": str(exc)})
    if not requests:
        return json.dumps([])

    dir_path = resolve_output_dir(None, ephemeral=ephemeral)
    results = _synthesize_segments(requests, provider, dir_path, pause_ms)

    for r in results:
        _enqueue_audio(r.path)

    return json.dumps([result_to_dict(r) for r in results])


@mcp.tool()
def record(
    text: str | None = None,
    voice: str | None = None,
    language: str | None = None,
    segments: list[dict[str, str]] | None = None,
    rate: int = 90,
    pause_ms: int = 500,
    output_path: str | None = None,
    output_dir: str | None = None,
    stability: float | None = None,
    similarity: float | None = None,
    style: float | None = None,
    speaker_boost: bool | None = None,
) -> str:
    """Synthesize and save audio to a file.

    Pass either a simple ``text`` string or a ``segments`` list.
    Call multiple times for multiple output files.

    Args:
        text: Simple text to synthesize. Ignored when segments is provided.
        voice: Default voice for all segments. If omitted, uses the
            session voice or provider default.
        language: Default ISO 639-1 language code (e.g. 'de', 'ko').
            Per-segment "language" overrides this.
        segments: List of segment objects, each with "text" (required)
            and optional "voice" and "language".
        rate: Speech rate as percentage. Defaults to 90.
        pause_ms: Pause between segments in milliseconds. Defaults to 500.
        output_path: Full path for the output file. Auto-generated if omitted.
        output_dir: Directory for output. Defaults to VOX_OUTPUT_DIR
            env var or ~/vox-output/.
        stability: ElevenLabs voice stability (0.0-1.0).
        similarity: ElevenLabs voice similarity boost (0.0-1.0).
        style: ElevenLabs voice style/expressiveness (0.0-1.0).
        speaker_boost: ElevenLabs speaker boost toggle.

    Returns:
        JSON string with synthesis results including file path.
    """
    _validate_voice_settings(stability, similarity, style)

    # Normalize input: text → single segment
    if segments is None:
        if text is None:
            return json.dumps({"error": "Provide text or segments."})
        segments = [{"text": text}]

    provider = get_provider()
    try:
        requests = _build_requests(
            segments,
            voice,
            language,
            provider,
            rate=rate,
            stability=stability,
            similarity=similarity,
            style=style,
            speaker_boost=speaker_boost,
        )
    except VoiceNotFoundError as exc:
        return json.dumps({"error": voice_not_found_message(exc)})
    except ValueError as exc:
        return json.dumps({"error": str(exc)})
    if not requests:
        return json.dumps([])

    dir_path = resolve_output_dir(output_dir)

    # If output_path is given, synthesize all segments into that one file
    if output_path:
        client = TTSClient(provider)
        if len(requests) == 1:
            path = Path(output_path)
            result = client.synthesize(requests[0], path)
            return json.dumps([result_to_dict(result)])
        # Multiple segments → stitch into the specified path
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            tmp_paths: list[Path] = []
            for i, req in enumerate(requests):
                seg_path = tmp_dir / f"seg_{i:04d}.mp3"
                client.synthesize(req, seg_path)
                tmp_paths.append(seg_path)
            stitch_audio(tmp_paths, Path(output_path), pause_ms)
        combined_text = " | ".join(r.text for r in requests)
        voices = {r.voice for r in requests}
        languages = {r.language for r in requests}
        result_voice = next(iter(voices)) if len(voices) == 1 else "mixed"
        result_lang = next(iter(languages)) if len(languages) == 1 else "mixed"
        result = SynthesisResult(
            path=Path(output_path),
            text=combined_text,
            provider=AudioProviderId(provider.name),
            voice=result_voice,
            language=result_lang,
        )
        return json.dumps([result_to_dict(result)])

    results = _synthesize_segments(requests, provider, dir_path, pause_ms)
    return json.dumps([result_to_dict(r) for r in results])


@mcp.tool()
def vibe(
    mood: str | None = None,
    tags: str | None = None,
    mode: str | None = None,
) -> str:
    """Set session mood and expressive tags.

    Controls how TTS voices sound during the session. Mood is a
    human-readable description; tags are ElevenLabs performance cues.

    Args:
        mood: Human-readable mood (e.g. "3am debugging", "excited").
            Stored as the ``vibe`` config field.
        tags: ElevenLabs expressive tags (e.g. "[tired] [slow]").
            Stored as ``vibe_tags``. Clears ``vibe_signals``.
        mode: Vibe detection mode: "auto", "manual", or "off".
            Auto mode reads signals from tool use; manual uses
            the mood/tags you set here.

    Returns:
        JSON string with the updated vibe state.
    """
    updates: dict[str, str] = {}
    if mood is not None:
        updates["vibe"] = mood
    if tags is not None:
        updates["vibe_tags"] = tags
        updates["vibe_signals"] = ""
    if mode is not None:
        if mode not in ("auto", "manual", "off"):
            return json.dumps({"error": f"Invalid mode '{mode}'. Use auto/manual/off."})
        updates["vibe_mode"] = mode

    if not updates:
        return json.dumps({"error": "Provide at least one of: mood, tags, mode."})

    write_fields(updates, _CONFIG_PATH)
    return json.dumps({"vibe": updates})


@mcp.tool()
def who(language: str | None = None) -> str:
    """List available voices for the current provider.

    Returns the voice roster with personality blurbs, the full list
    of available voices, and the current session voice.

    Args:
        language: ISO 639-1 language code to filter by (e.g. 'de', 'ko').

    Returns:
        JSON string with provider, current voice, featured voices,
        and full voice list.
    """
    if language is not None:
        language = validate_language(language)
    provider = get_provider()
    all_voices = provider.list_voices(language)
    current = read_field("voice", _CONFIG_PATH)

    featured = [
        {"name": name, "blurb": blurb}
        for (prov, name), blurb in VOICE_BLURBS.items()
        if prov == provider.name and name in all_voices
    ]
    random.shuffle(featured)
    featured = featured[:6]

    return json.dumps(
        {
            "provider": provider.name,
            "current": current,
            "featured": featured,
            "all": all_voices,
        }
    )


# ---------------------------------------------------------------------------
# Server lifecycle
# ---------------------------------------------------------------------------


def _start_watcher() -> None:
    """Start the session event watcher if a session directory exists."""
    from punt_vox.watcher import (
        SessionWatcher,
        derive_session_dir,
        make_notification_consumer,
    )

    session_dir = derive_session_dir()
    if not session_dir.is_dir():
        logger.info("Session dir %s not found, watcher not started", session_dir)
        return

    consumer = make_notification_consumer()
    watcher = SessionWatcher(session_dir=session_dir, consumers=[consumer])
    watcher.start()


def run_server() -> None:
    """Run the MCP server with stdio transport."""
    configure_logging(stderr_level="INFO")
    logger.info("Starting vox MCP server (mic)")
    _start_watcher()
    mcp.run(transport="stdio")


if __name__ == "__main__":
    run_server()
