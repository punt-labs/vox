"""FastMCP server for punt-vox -- mic API.

Thin client of the voxd audio daemon. Session state lives in an
in-memory dataclass; audio requests go to voxd over WebSocket
via VoxClient.
"""

from __future__ import annotations

import json
import logging
import random
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

from punt_vox import __version__
from punt_vox.client import VoxClientSync, VoxdConnectionError
from punt_vox.logging_config import configure_logging
from punt_vox.voices import VOICE_BLURBS

logger = logging.getLogger(__name__)

mcp = FastMCP(
    "mic",
    instructions=(
        "Vox is a text-to-speech engine. Use these tools to speak text aloud "
        "and generate audio files.\n\n"
        "When a stop hook blocks with a \u266a phrase, write 1-2 sentences "
        "summarizing what you completed and call the unmute tool with "
        "ephemeral=true. Mood tags are pre-resolved in config \u2014 do not "
        "pass vibe_tags. No other output.\n\n"
        "Do NOT use Read, Write, or Bash tools to access .vox/config.md. "
        "All config state is available through MCP tools or hook context."
    ),
)
mcp._mcp_server.version = __version__  # pyright: ignore[reportPrivateUsage]


# ---------------------------------------------------------------------------
# Session state
# ---------------------------------------------------------------------------


@dataclass
class SessionState:
    """In-memory session state. Seeded from .vox/config.md on startup."""

    session_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    notify: str = "n"
    speak: str = "n"
    voice: str | None = None
    provider: str | None = None
    model: str | None = None
    vibe_mode: str = "off"
    vibe: str | None = None
    vibe_tags: str | None = None
    vibe_signals: str = ""
    music_mode: str = "off"


# Module-level singleton; initialized in run_server().
_state: SessionState = SessionState()


# ---------------------------------------------------------------------------
# Config discovery and seeding
# ---------------------------------------------------------------------------


def _find_config() -> Path | None:
    """Walk up from cwd to find .vox/config.md."""
    from punt_vox.config import find_config

    return find_config()


def _seed_state_from_config(config_path: Path | None) -> SessionState:
    """Read .vox/config.md once and return a SessionState."""
    if config_path is None or not config_path.exists():
        return SessionState()

    from punt_vox.config import read_config

    cfg = read_config(config_path=config_path)
    return SessionState(
        notify=cfg.notify,
        speak=cfg.speak,
        voice=cfg.voice,
        provider=cfg.provider,
        model=cfg.model,
        vibe_mode=cfg.vibe_mode,
        vibe=cfg.vibe,
        vibe_tags=cfg.vibe_tags,
        vibe_signals=cfg.vibe_signals or "",
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


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


def _default_output_dir() -> Path:
    """Resolve the default output directory for record tool."""
    import os

    env_dir = os.environ.get("VOX_OUTPUT_DIR")
    if env_dir:
        return Path(env_dir)
    return Path.home() / "vox-output"


def _voxd_client() -> VoxClientSync:
    """Create a VoxClientSync instance."""
    return VoxClientSync()


def _error(message: str) -> str:
    """Return a JSON error string."""
    return json.dumps({"error": message})


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
    provider: str | None = None,
    model: str | None = None,
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
            and optional "voice", "language", and "vibe_tags".
            Per-segment "vibe_tags" override the top-level default.
            Example:
            [{"voice": "roger", "text": "Hello.", "vibe_tags": "[excited]"},
             {"text": "Hi."}]
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
        provider: TTS provider override (elevenlabs, openai, polly, say,
            espeak). When provided, persists to session config for
            subsequent calls.
        model: TTS model override (e.g. eleven_v3, eleven_flash_v2_5,
            tts-1). When provided, persists to session config for
            subsequent calls.

    Returns:
        JSON string with synthesis results.
    """
    _validate_voice_settings(stability, similarity, style)

    # Update in-memory state for persistent fields.
    if provider is not None:
        _state.provider = provider
    if model is not None:
        _state.model = model
    if vibe_tags is not None:
        _state.vibe_tags = vibe_tags
        _state.vibe_signals = ""

    # Normalize input: text -> single segment.
    if segments is None:
        if text is None:
            if provider is not None or model is not None or vibe_tags is not None:
                updates: dict[str, str] = {}
                if provider is not None:
                    updates["provider"] = provider
                if model is not None:
                    updates["model"] = model
                if vibe_tags is not None:
                    updates["vibe_tags"] = vibe_tags
                return json.dumps({"status": "config updated", **updates})
            return _error("Provide text or segments.")
        segments = [{"text": text}]

    # Resolve effective voice: explicit param > session state.
    effective_voice = voice or _state.voice
    effective_provider = provider or _state.provider
    effective_model = model or _state.model

    # Resolve vibe_tags: explicit param > session state.
    effective_vibe_tags = vibe_tags or _state.vibe_tags

    client = _voxd_client()
    results: list[dict[str, Any]] = []

    try:
        for seg in segments:
            seg_text = seg.get("text", "")
            if not seg_text:
                continue
            seg_voice = seg.get("voice") or effective_voice
            seg_language = seg.get("language") or language
            seg_vibe_tags = seg.get("vibe_tags") or effective_vibe_tags

            kwargs: dict[str, Any] = {
                "rate": rate,
            }
            if seg_voice is not None:
                kwargs["voice"] = seg_voice
            if effective_provider is not None:
                kwargs["provider"] = effective_provider
            if effective_model is not None:
                kwargs["model"] = effective_model
            if seg_language is not None:
                kwargs["language"] = seg_language
            if seg_vibe_tags is not None:
                kwargs["vibe_tags"] = str(seg_vibe_tags)
            if stability is not None:
                kwargs["stability"] = stability
            if similarity is not None:
                kwargs["similarity"] = similarity
            if style is not None:
                kwargs["style"] = style
            if speaker_boost is not None:
                kwargs["speaker_boost"] = speaker_boost

            result = client.synthesize(seg_text, **kwargs)
            entry: dict[str, object] = {
                "id": result.request_id,
                "text": seg_text,
                "voice": seg_voice,
                "provider": effective_provider,
            }
            if result.deduped:
                entry["deduped"] = True
                if result.original_played_at is not None:
                    entry["original_played_at"] = result.original_played_at
                if result.ttl_seconds_remaining is not None:
                    entry["ttl_seconds_remaining"] = result.ttl_seconds_remaining
            results.append(entry)
    except VoxdConnectionError as exc:
        return _error(str(exc))
    except Exception as exc:
        logger.exception("Synthesis failed")
        return _error(str(exc))

    if not results:
        return json.dumps([])
    return json.dumps(results)


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
            and optional "voice", "language", and "vibe_tags".
            Per-segment "vibe_tags" override the session config.
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

    # Normalize input: text -> single segment.
    if segments is None:
        if text is None:
            return _error("Provide text or segments.")
        segments = [{"text": text}]

    effective_voice = voice or _state.voice
    effective_provider = _state.provider
    effective_model = _state.model
    effective_vibe_tags = _state.vibe_tags

    if output_path and len(segments) > 1:
        return _error("output_path only supported for single-segment calls")

    # Resolve output directory.
    dir_path = Path(output_dir) if output_dir else _default_output_dir()
    dir_path.mkdir(parents=True, exist_ok=True)

    client = _voxd_client()
    results: list[dict[str, Any]] = []

    try:
        for seg in segments:
            seg_text = seg.get("text", "")
            if not seg_text:
                continue
            seg_voice = seg.get("voice") or effective_voice
            seg_language = seg.get("language") or language
            seg_vibe_tags = seg.get("vibe_tags") or effective_vibe_tags

            kwargs: dict[str, Any] = {
                "rate": rate,
            }
            if seg_voice is not None:
                kwargs["voice"] = seg_voice
            if effective_provider is not None:
                kwargs["provider"] = effective_provider
            if effective_model is not None:
                kwargs["model"] = effective_model
            if seg_language is not None:
                kwargs["language"] = seg_language
            if seg_vibe_tags is not None:
                kwargs["vibe_tags"] = str(seg_vibe_tags)
            if stability is not None:
                kwargs["stability"] = stability
            if similarity is not None:
                kwargs["similarity"] = similarity
            if style is not None:
                kwargs["style"] = style
            if speaker_boost is not None:
                kwargs["speaker_boost"] = speaker_boost

            mp3_bytes = client.record(seg_text, **kwargs)

            # Determine output file path.
            if output_path and len(segments) == 1:
                file_path = Path(output_path)
            else:
                import hashlib

                text_hash = hashlib.md5(seg_text.encode()).hexdigest()[:10]
                filename = f"{text_hash}.mp3"
                file_path = dir_path / filename

            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_bytes(mp3_bytes)

            results.append(
                {
                    "path": str(file_path),
                    "text": seg_text,
                    "voice": seg_voice,
                    "provider": effective_provider,
                    "bytes": len(mp3_bytes),
                }
            )
    except VoxdConnectionError as exc:
        return _error(str(exc))
    except Exception as exc:
        logger.exception("Record failed")
        return _error(str(exc))

    if not results:
        return json.dumps([])
    return json.dumps(results)


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
        _state.vibe = mood
    if tags is not None:
        updates["vibe_tags"] = tags
        updates["vibe_signals"] = ""
        _state.vibe_tags = tags
        _state.vibe_signals = ""
    if mode is not None:
        if mode not in ("auto", "manual", "off"):
            return _error(f"Invalid mode '{mode}'. Use auto/manual/off.")
        updates["vibe_mode"] = mode
        _state.vibe_mode = mode

    if not updates:
        return _error("Provide at least one of: mood, tags, mode.")

    # Persist to disk so hooks (which read config independently) see the change
    from punt_vox.config import write_fields

    write_fields(updates, _find_config())

    # Propagate vibe change to music loop if this session owns music.
    if _state.music_mode == "on":
        client = _voxd_client()
        try:
            client.music_vibe(
                vibe=_state.vibe or "",
                vibe_tags=_state.vibe_tags or "",
                owner_id=_state.session_id,
            )
        except Exception:
            logger.warning(
                "voxd error during vibe propagation; music off",
                exc_info=True,
            )
            _state.music_mode = "off"

    return json.dumps({"vibe": updates})


def _music_on_message(style: str | None, vibe: str | None) -> str:
    """Build the human-readable message for music-on."""
    prefix = "\u266a Music on \u2014 generating"
    if style and vibe:
        return f"{prefix} a {style} track for your {vibe} mood..."
    if style:
        return f"{prefix} a {style} track..."
    if vibe:
        return f"{prefix} a track for your {vibe} mood..."
    return f"{prefix} ambient music..."


@mcp.tool()
def music(
    mode: str,
    style: str | None = None,
    name: str | None = None,
) -> str:
    """Control background music generation.

    When on, voxd generates instrumental tracks derived from the current
    session vibe and loops them. Vibe changes automatically trigger new
    track generation for the owning session.

    Args:
        mode: "on" to start music, "off" to stop.
        style: Optional style modifier (e.g. "techno", "jazz").
            Persists across calls -- subsequent ``on`` reuses the
            last-set style.
        name: Optional track name. When a saved track with this name
            exists, it is replayed without generation (zero credits).
            When no saved track exists, the generated track is saved
            under this name.

    Returns:
        JSON string with a human-readable ``message`` field and
        the raw voxd response fields.
    """
    if mode not in ("on", "off"):
        return _error(f"Invalid mode '{mode}'. Use on/off.")

    client = _voxd_client()
    try:
        resp = client.music(
            mode=mode,
            style=style or "",
            vibe=_state.vibe or "",
            vibe_tags=_state.vibe_tags or "",
            owner_id=_state.session_id,
            name=name,
        )
    except VoxdConnectionError:
        logger.warning("voxd unreachable in music tool; music off", exc_info=True)
        _state.music_mode = "off"
        return json.dumps(
            {
                "message": "\u266a Daemon unreachable \u2014 music off.",
                "error": "daemon unreachable",
            }
        )
    except Exception as exc:
        logger.warning("voxd error in music tool; music off", exc_info=True)
        _state.music_mode = "off"
        return json.dumps(
            {
                "message": f"\u266a Music error: {exc}",
                "error": str(exc),
            }
        )

    _state.music_mode = mode

    # Replay of existing track — status is "playing", not "generating".
    if resp.get("status") == "playing" and name:
        message = f"\u266a Playing saved track: {name}"
    elif mode == "on":
        message = _music_on_message(style, _state.vibe)
    else:
        message = "\u266a Music off."
    return json.dumps({"message": message, **resp})


@mcp.tool()
def music_play(name: str) -> str:
    """Replay a saved music track by name.

    Finds the track in the music library and starts looping it.
    No generation, no credits used.

    Args:
        name: Track name (as shown by music_list).

    Returns:
        JSON string with a human-readable ``message`` field and
        the raw voxd response fields.
    """
    client = _voxd_client()
    try:
        resp = client.music_play(name, owner_id=_state.session_id)
    except VoxdConnectionError:
        logger.warning("voxd unreachable in music_play", exc_info=True)
        return json.dumps(
            {
                "message": "\u266a Daemon unreachable.",
                "error": "daemon unreachable",
            }
        )
    except Exception as exc:
        logger.warning("voxd error in music_play", exc_info=True)
        return json.dumps(
            {
                "message": f"\u266a {exc}",
                "error": str(exc),
            }
        )

    _state.music_mode = "on"
    track_name = resp.get("name", name)
    message = f"\u266a Now playing: {track_name}"
    return json.dumps({"message": message, **resp})


@mcp.tool()
def music_list() -> str:
    """Show saved music tracks with name, size, and date.

    Returns:
        JSON string with a human-readable ``message`` field and
        the track list from voxd.
    """
    client = _voxd_client()
    try:
        resp = client.music_list()
    except VoxdConnectionError:
        logger.warning("voxd unreachable in music_list", exc_info=True)
        return json.dumps(
            {
                "message": "\u266a Daemon unreachable.",
                "error": "daemon unreachable",
            }
        )
    except Exception as exc:
        logger.warning("voxd error in music_list", exc_info=True)
        return json.dumps(
            {
                "message": f"\u266a {exc}",
                "error": str(exc),
            }
        )

    tracks: list[dict[str, object]] = resp.get("tracks", [])
    if not tracks:
        message = "\u266a No saved tracks."
    else:
        lines = [f"\u266a {len(tracks)} saved track(s):"]
        for t in tracks:
            raw_size = t.get("size_bytes", 0)
            size_kb = int(str(raw_size)) // 1024
            lines.append(f"  \u266a {t['name']} ({size_kb} KB)")
        message = "\n".join(lines)
    return json.dumps({"message": message, **resp})


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
    client = _voxd_client()

    try:
        all_voices = client.voices(provider=_state.provider)
    except VoxdConnectionError as exc:
        return _error(str(exc))
    except Exception as exc:
        logger.exception("Voice listing failed")
        return _error(str(exc))

    # Determine effective provider name for blurb lookup.
    provider_name = _state.provider or "elevenlabs"

    featured = [
        {"name": name, "blurb": blurb}
        for (prov, name), blurb in VOICE_BLURBS.items()
        if prov == provider_name and name in all_voices
    ]
    random.shuffle(featured)
    featured = featured[:6]

    return json.dumps(
        {
            "provider": provider_name,
            "current": _state.voice,
            "featured": featured,
            "all": all_voices,
        }
    )


@mcp.tool()
def notify(
    mode: str,
    voice: str | None = None,
) -> str:
    """Set notification mode and optionally the session voice.

    Controls whether vox sends notification events. Whether notifications
    are heard as chimes or TTS speech is controlled by the separate
    ``speak`` field (see the ``speak`` tool).

    When enabling notifications (mode "y" or "c"), initializes speak to
    "y" (voice) if the user has not yet made an explicit speak choice.
    Once the user has set speak via ``/mute`` or ``/unmute``, enabling
    notifications preserves that choice.

    Args:
        mode: Notification mode -- "y" (notifications on), "n" (off),
            or "c" (continuous with real-time signal announcements).
        voice: Optional session voice to set (e.g. "matilda", "roger").

    Returns:
        JSON string with the updated config fields.
    """
    if mode not in ("y", "n", "c"):
        return _error(f"Invalid mode '{mode}'. Use y/n/c.")

    updates: dict[str, str] = {"notify": mode}
    _state.notify = mode

    # Initialize speak to "y" if not explicitly set yet.
    # "n" is the default sentinel; if it's still at default and we're
    # enabling notifications, default to voice mode.
    if mode in ("y", "c") and _state.speak == "n" and not _speak_was_explicitly_set():
        updates["speak"] = "y"
        _state.speak = "y"

    if voice is not None:
        updates["voice"] = voice
        _state.voice = voice

    # Persist to disk so hooks (which read config independently) see the change
    from punt_vox.config import write_fields

    write_fields(updates, _find_config())

    return json.dumps({"notify": updates})


# Track whether speak was explicitly set by the user.
_speak_explicit: bool = False


def _speak_was_explicitly_set() -> bool:
    """Check if speak was explicitly set (via config file or tool call)."""
    return _speak_explicit


@mcp.tool()
def speak(
    mode: str,
    voice: str | None = None,
) -> str:
    """Toggle spoken notifications on or off.

    Args:
        mode: "y" for voice (TTS speech) or "n" for chimes only.
        voice: Optional session voice to set (e.g. "matilda", "roger").

    Returns:
        JSON string with the updated fields.
    """
    global _speak_explicit

    if mode not in ("y", "n"):
        return _error(f"Invalid mode '{mode}'. Use y/n.")

    updates: dict[str, str] = {"speak": mode}
    _state.speak = mode
    _speak_explicit = True

    if voice is not None:
        updates["voice"] = voice
        _state.voice = voice

    # Persist to disk so hooks (which read config independently) see the change
    from punt_vox.config import write_fields

    write_fields(updates, _find_config())

    return json.dumps(updates)


@mcp.tool()
def status() -> str:
    """Show current vox state (provider, voice, notify, vibe).

    Returns:
        JSON string with provider, voice, notify mode, speak mode,
        vibe mode, and current vibe.
    """
    return json.dumps(
        {
            "provider": _state.provider,
            "voice": _state.voice,
            "notify": _state.notify,
            "speak": _state.speak,
            "vibe_mode": _state.vibe_mode,
            "vibe": _state.vibe,
            "vibe_tags": _state.vibe_tags,
            "vibe_signals": _state.vibe_signals,
            "music_mode": _state.music_mode,
        }
    )


@mcp.tool()
def show_vox() -> str:
    """Display the Vox status widget in the Lux window.

    Shows notification mode, voice/mute state, voice picker, vibe,
    and engine in a compact display panel. Call again to refresh
    after changing settings.

    Returns:
        JSON string with status ("ok" or "error" with message).
    """
    from punt_vox.applet import show_applet
    from punt_vox.config import VoxConfig

    cfg = VoxConfig(
        notify=_state.notify,
        speak=_state.speak,
        vibe_mode=_state.vibe_mode,
        voice=_state.voice,
        provider=_state.provider,
        model=_state.model,
        vibe=_state.vibe,
        vibe_tags=_state.vibe_tags,
        vibe_signals=_state.vibe_signals,
    )

    # Get voice roster from voxd.
    try:
        client = _voxd_client()
        voice_roster = client.voices(provider=_state.provider)
    except Exception:
        voice_roster = []

    provider_name = _state.provider or "elevenlabs"
    return json.dumps(show_applet(cfg, provider_name, voice_roster))


# ---------------------------------------------------------------------------
# Server lifecycle
# ---------------------------------------------------------------------------


def run_server() -> None:
    """Run the MCP server with stdio transport."""
    global _state, _speak_explicit

    configure_logging(stderr_level="INFO")
    logger.info("Starting vox MCP server (mic)")

    # Seed session state from .vox/config.md if it exists.
    config_path = _find_config()
    _state = _seed_state_from_config(config_path)

    # Mark speak as explicitly set if the config file had it.
    if config_path is not None and config_path.exists():
        from punt_vox.config import read_field

        if read_field("speak", config_path) is not None:
            _speak_explicit = True

    logger.info(
        "Session state: notify=%s speak=%s voice=%s provider=%s vibe_mode=%s",
        _state.notify,
        _state.speak,
        _state.voice,
        _state.provider,
        _state.vibe_mode,
    )

    mcp.run(transport="stdio")


if __name__ == "__main__":
    run_server()
