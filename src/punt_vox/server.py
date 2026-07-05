"""FastMCP server for punt-vox -- mic API.

Thin client of the voxd audio daemon. Session state lives in an
in-memory dataclass; audio requests go to voxd over WebSocket
via VoxClient.
"""

from __future__ import annotations

import hashlib
import json
import logging
import random
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from pathlib import Path

from mcp.server.fastmcp import FastMCP
from websockets.exceptions import WebSocketException

from punt_vox import __version__
from punt_vox.client import VoxClientSync, VoxdConnectionError, VoxdProtocolError
from punt_vox.logging_config import configure_logging
from punt_vox.types_synthesis import SynthesisSpec
from punt_vox.voices import VOICE_BLURBS
from punt_vox.voxd.music.generator import MusicTrack

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
        "Do NOT use Read, Write, or Bash tools to access "
        ".punt-labs/vox/vox.md or .punt-labs/vox/vox.local.md. "
        "All config state is available through "
        "MCP tools or hook context."
    ),
)
mcp._mcp_server.version = __version__  # pyright: ignore[reportPrivateUsage]

_VALID_NOTIFY_MODES = frozenset({"y", "n", "c"})
_VALID_SPEAK_MODES = frozenset({"y", "n"})
_VALID_VIBE_MODES = frozenset({"auto", "manual", "off"})


# ---------------------------------------------------------------------------
# Session state
# ---------------------------------------------------------------------------


@dataclass
class SessionConfig:
    """In-memory session config. Seeded from vox.md + vox.local.md."""

    _session_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    _notify: str = "n"
    _speak: str = "n"
    _voice: str | None = None
    _provider: str | None = None
    _model: str | None = None
    _vibe_mode: str = "off"
    _vibe: str | None = None
    _vibe_tags: str | None = None
    _vibe_signals: str = ""
    _music_mode: str = "off"
    _speak_explicit: bool = False

    # -- Properties (read access) ------------------------------------------

    @property
    def session_id(self) -> str:
        """Return the unique session identifier."""
        return self._session_id

    @property
    def notify(self) -> str:
        """Return the notification mode ('y', 'n', or 'c')."""
        return self._notify

    @property
    def speak(self) -> str:
        """Return the speak mode ('y' or 'n')."""
        return self._speak

    @property
    def voice(self) -> str | None:
        """Return the current voice name, or None for provider default."""
        return self._voice

    @voice.setter
    def voice(self, value: str | None) -> None:
        self._voice = value

    @property
    def provider(self) -> str | None:
        """Return the current TTS provider name."""
        return self._provider

    @provider.setter
    def provider(self, value: str | None) -> None:
        self._provider = value

    @property
    def model(self) -> str | None:
        """Return the current TTS model name."""
        return self._model

    @model.setter
    def model(self, value: str | None) -> None:
        self._model = value

    @property
    def vibe_mode(self) -> str:
        """Return the vibe detection mode ('auto', 'manual', or 'off')."""
        return self._vibe_mode

    @property
    def vibe(self) -> str | None:
        """Return the current vibe/mood description."""
        return self._vibe

    @property
    def vibe_tags(self) -> str | None:
        """Return the current ElevenLabs expressive tags."""
        return self._vibe_tags

    @property
    def vibe_signals(self) -> str:
        """Return the accumulated vibe signals string."""
        return self._vibe_signals

    @property
    def music_mode(self) -> str:
        """Return the music mode ('on' or 'off')."""
        return self._music_mode

    @music_mode.setter
    def music_mode(self, value: str) -> None:
        self._music_mode = value

    @property
    def speak_explicit(self) -> bool:
        """Return whether the user has explicitly set speak mode."""
        return self._speak_explicit

    # -- Validated setters -------------------------------------------------

    def set_notify(self, mode: str) -> None:
        """Set notification mode with validation."""
        if mode not in _VALID_NOTIFY_MODES:
            msg = f"invalid notify mode: {mode!r}"
            raise ValueError(msg)
        self._notify = mode

    def set_speak(self, mode: str, *, explicit: bool = True) -> None:
        """Set speak mode with validation.

        When *explicit* is True (the default), marks the choice as
        user-initiated so future notify-enable calls preserve it.
        """
        if mode not in _VALID_SPEAK_MODES:
            msg = f"invalid speak mode: {mode!r}"
            raise ValueError(msg)
        self._speak = mode
        if explicit:
            self._speak_explicit = True

    def set_vibe(self, mood: str | None = None, tags: str | None = None) -> None:
        """Set vibe mood and/or tags together.

        Setting tags clears vibe_signals (tags are the resolved form).
        """
        if mood is not None:
            self._vibe = mood
        if tags is not None:
            self._vibe_tags = tags
            self._vibe_signals = ""

    def set_vibe_mode(self, mode: str) -> None:
        """Set vibe detection mode with validation."""
        if mode not in _VALID_VIBE_MODES:
            msg = f"invalid vibe mode: {mode!r}"
            raise ValueError(msg)
        self._vibe_mode = mode

    @classmethod
    def from_config(cls, config_dir: Path | None) -> SessionConfig:
        """Read per-repo config once and return a SessionConfig."""
        if config_dir is None:
            return cls()

        from punt_vox.config import read_config

        cfg = read_config(config_dir=config_dir)
        return cls(
            _notify=cfg.notify,
            _speak=cfg.speak,
            _voice=cfg.voice,
            _provider=cfg.provider,
            _model=cfg.model,
            _vibe_mode=cfg.vibe_mode,
            _vibe=cfg.vibe,
            _vibe_tags=cfg.vibe_tags,
            _vibe_signals=cfg.vibe_signals or "",
        )

    def refresh_from_config(self) -> None:
        """Re-read config files and update self with current values.

        Config-sourced fields (notify, speak, vibe_mode, vibe, vibe_tags,
        vibe_signals) always take the config value -- the config file is
        the source of truth since CLI and hooks write there directly.

        For voice, provider, and model the MCP tool may have set a value
        that was not persisted to config (e.g. an in-tool override).  Only
        overwrite self when config has a non-None value so those
        overrides survive.
        """
        config_dir = _find_config_dir()
        if config_dir is None:
            return

        from punt_vox.config import read_config, read_field

        cfg = read_config(config_dir=config_dir)

        self._vibe = cfg.vibe
        self._vibe_tags = cfg.vibe_tags
        self._vibe_signals = cfg.vibe_signals or ""
        self._vibe_mode = cfg.vibe_mode
        self._notify = cfg.notify
        self._speak = cfg.speak

        if read_field("speak", config_dir) is not None:
            self._speak_explicit = True

        if cfg.voice is not None:
            self._voice = cfg.voice
        if cfg.provider is not None:
            self._provider = cfg.provider
        if cfg.model is not None:
            self._model = cfg.model


# Module-level singleton; initialized in run_server().
_session: SessionConfig = SessionConfig()


# ---------------------------------------------------------------------------
# Config discovery and seeding
# ---------------------------------------------------------------------------


def _find_config_dir() -> Path | None:
    """Walk up from cwd to find per-repo .punt-labs/vox/ directory."""
    from punt_vox.dirs import find_config_dir

    return find_config_dir()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _default_output_dir() -> Path:
    """Resolve the default output directory for record tool."""
    from punt_vox.dirs import default_output_dir

    return default_output_dir()


def _voxd_client() -> VoxClientSync:
    """Create a VoxClientSync instance."""
    return VoxClientSync()


def _error(message: str) -> str:
    """Return a JSON error string."""
    return json.dumps({"error": message})


def _process_segments(
    segments: list[dict[str, str]],
    defaults: SynthesisSpec,
    *,
    handler: Callable[[str, SynthesisSpec], dict[str, object]],
    error_label: str,
) -> str:
    """Synthesize each segment against *defaults* and delegate to *handler*.

    *defaults* bundles the call-level synthesis parameters; each segment may
    override ``voice``, ``language``, and ``vibe_tags``. Returns a JSON string:
    a list of result dicts, or an error dict. The *handler* receives
    (seg_text, seg_spec) and returns one result dict.
    """
    results: list[dict[str, object]] = []
    try:
        for seg in segments:
            seg_text = seg.get("text", "")
            if not seg_text:
                continue
            seg_spec = replace(
                defaults,
                voice=seg.get("voice") or defaults.voice,
                language=seg.get("language") or defaults.language,
                vibe_tags=seg.get("vibe_tags") or defaults.vibe_tags,
            )
            results.append(handler(seg_text, seg_spec))
    except VoxdConnectionError as exc:
        return _error(str(exc))
    except (VoxdProtocolError, WebSocketException, OSError, ValueError) as exc:
        logger.exception("%s failed", error_label)
        return _error(str(exc))

    return json.dumps(results if results else [])


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
    pause_ms: int = 500,  # noqa: ARG001 -- reserved for future multi-segment pause
    ephemeral: bool = True,  # noqa: FBT001, FBT002 -- MCP tool schema requires bool param
    stability: float | None = None,
    similarity: float | None = None,
    style: float | None = None,
    speaker_boost: bool | None = None,  # noqa: FBT001 -- MCP tool schema requires bool param
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
        ephemeral: Write to .punt-labs/vox/ephemeral/ and clean up previous files.
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
    _session.refresh_from_config()

    # Validate voice settings via SynthesisSpec (single validation path).
    SynthesisSpec(stability=stability, similarity=similarity, style=style).validate()

    # ephemeral is accepted for callers but voxd cleans up internally today.
    _ = ephemeral

    # Update in-memory state for persistent fields.
    if provider is not None:
        _session.provider = provider
    if model is not None:
        _session.model = model
    if vibe_tags is not None:
        _session.set_vibe(tags=vibe_tags)

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

    effective_provider = provider or _session.provider
    client = _voxd_client()

    def _synth_handler(seg_text: str, seg_spec: SynthesisSpec) -> dict[str, object]:
        result = client.synthesize(seg_text, **seg_spec.to_client_kwargs())
        entry: dict[str, object] = {
            "id": result.request_id,
            "text": seg_text,
            "voice": seg_spec.voice,
            "provider": effective_provider,
            "cached": result.cached,
        }
        if result.deduped:
            entry["deduped"] = True
            if result.original_played_at is not None:
                entry["original_played_at"] = result.original_played_at
            if result.ttl_seconds_remaining is not None:
                entry["ttl_seconds_remaining"] = result.ttl_seconds_remaining
        return entry

    defaults = SynthesisSpec(
        voice=voice or _session.voice,
        language=language,
        rate=rate,
        provider=effective_provider,
        model=model or _session.model,
        stability=stability,
        similarity=similarity,
        style=style,
        speaker_boost=speaker_boost,
        vibe_tags=vibe_tags or _session.vibe_tags,
    )
    return _process_segments(
        segments, defaults, handler=_synth_handler, error_label="Synthesis"
    )


@mcp.tool()
def record(
    text: str | None = None,
    voice: str | None = None,
    language: str | None = None,
    segments: list[dict[str, str]] | None = None,
    rate: int = 90,
    pause_ms: int = 500,  # noqa: ARG001 -- reserved for future multi-segment pause
    output_path: str | None = None,
    output_dir: str | None = None,
    stability: float | None = None,
    similarity: float | None = None,
    style: float | None = None,
    speaker_boost: bool | None = None,  # noqa: FBT001 -- MCP tool schema requires bool param
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
            env var or ~/Music/vox/.
        stability: ElevenLabs voice stability (0.0-1.0).
        similarity: ElevenLabs voice similarity boost (0.0-1.0).
        style: ElevenLabs voice style/expressiveness (0.0-1.0).
        speaker_boost: ElevenLabs speaker boost toggle.

    Returns:
        JSON string with synthesis results including file path.
    """
    _session.refresh_from_config()

    # Validate voice settings via SynthesisSpec (single validation path).
    SynthesisSpec(stability=stability, similarity=similarity, style=style).validate()

    # Normalize input: text -> single segment.
    if segments is None:
        if text is None:
            return _error("Provide text or segments.")
        segments = [{"text": text}]

    if output_path and len(segments) > 1:
        return _error("output_path only supported for single-segment calls")

    # Resolve output directory.
    dir_path = Path(output_dir) if output_dir else _default_output_dir()
    dir_path.mkdir(parents=True, exist_ok=True)

    effective_provider = _session.provider
    client = _voxd_client()

    def _record_handler(seg_text: str, seg_spec: SynthesisSpec) -> dict[str, object]:
        mp3_bytes = client.record(seg_text, **seg_spec.to_client_kwargs())

        # Determine output file path.
        if output_path and len(segments) == 1:
            file_path = Path(output_path)
        else:
            text_hash = hashlib.md5(
                seg_text.encode(),
                usedforsecurity=False,
            ).hexdigest()[:10]
            file_path = dir_path / f"{text_hash}.mp3"

        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_bytes(mp3_bytes)

        return {
            "path": str(file_path),
            "text": seg_text,
            "voice": seg_spec.voice,
            "provider": effective_provider,
            "bytes": len(mp3_bytes),
        }

    defaults = SynthesisSpec(
        voice=voice or _session.voice,
        language=language,
        rate=rate,
        provider=effective_provider,
        model=_session.model,
        stability=stability,
        similarity=similarity,
        style=style,
        speaker_boost=speaker_boost,
        vibe_tags=_session.vibe_tags,
    )
    return _process_segments(
        segments, defaults, handler=_record_handler, error_label="Record"
    )


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
    _session.refresh_from_config()
    updates: dict[str, str] = {}
    if mood is not None:
        updates["vibe"] = mood
    if tags is not None:
        updates["vibe_tags"] = tags
        updates["vibe_signals"] = ""
    _session.set_vibe(mood=mood, tags=tags)
    if mode is not None:
        if mode not in _VALID_VIBE_MODES:
            return _error(f"Invalid mode '{mode}'. Use auto/manual/off.")
        updates["vibe_mode"] = mode
        _session.set_vibe_mode(mode)

    if not updates:
        return _error("Provide at least one of: mood, tags, mode.")

    # Persist to disk so hooks (which read config independently) see the change
    from punt_vox.config import write_fields

    write_fields(updates, _find_config_dir())

    # Propagate vibe change to music loop if this session owns music.
    if _session.music_mode == "on":
        client = _voxd_client()
        try:
            client.music_vibe(
                vibe=_session.vibe or "",
                vibe_tags=_session.vibe_tags or "",
                owner_id=_session.session_id,
            )
        except (VoxdConnectionError, VoxdProtocolError, WebSocketException, OSError):
            logger.warning(
                "voxd error during vibe propagation; music off",
                exc_info=True,
            )
            _session.music_mode = "off"

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
    base_prompt: str | None = None,
    variations: list[str] | None = None,
) -> str:
    """Control background music generation.

    vox never interprets a genre -- YOU, the calling agent, author the prompts.
    On ``on`` (and on any style/vibe change) supply ``base_prompt`` plus exactly
    12 literal, genre-accurate ``variations`` (one per pool slot); voxd generates
    track ``i`` from ``base_prompt`` + ``variations[i]``. Omit both to fall back
    to ``"<style> music, <mood>. instrumental, loopable."``. Never add generic
    "background music for deep work / smooth ambient texture / driving beat"
    boilerplate -- it homogenizes every genre. See ``/music`` for a worked
    example.

    Args:
        mode: "on" to start music, "off" to stop.
        style: Optional style modifier (e.g. "techno", "klezmer"); persists.
        name: Optional track name -- replays a saved track by that name, or
            saves the generated track under it.
        base_prompt: Genre-forward stem shared by every track. Requires
            ``variations``.
        variations: Exactly 12 literal per-track descriptions, varied within
            the genre. Requires ``base_prompt``.

    Returns:
        JSON string with a ``message`` field and the raw voxd response.
    """
    _session.refresh_from_config()
    if mode not in ("on", "off"):
        return _error(f"Invalid mode '{mode}'. Use on/off.")

    client = _voxd_client()
    try:
        resp = client.music(
            mode=mode,
            style=style or "",
            vibe=_session.vibe or "",
            vibe_tags=_session.vibe_tags or "",
            owner_id=_session.session_id,
            name=name,
            base_prompt=base_prompt,
            variations=variations,
        )
    except VoxdConnectionError:
        logger.warning("voxd unreachable in music tool; music off", exc_info=True)
        _session.music_mode = "off"
        return json.dumps(
            {
                "message": "\u266a Daemon unreachable \u2014 music off.",
                "error": "daemon unreachable",
            }
        )
    except (VoxdProtocolError, WebSocketException, OSError, ValueError) as exc:
        logger.warning("voxd error in music tool; music off", exc_info=True)
        _session.music_mode = "off"
        return json.dumps(
            {
                "message": f"\u266a Music error: {exc}",
                "error": str(exc),
            }
        )

    _session.music_mode = mode

    # Replay of existing track — status is "playing", not "generating".
    if resp.get("status") == "playing" and name:
        message = f"\u266a Playing saved track: {name}"
    elif mode == "on":
        message = _music_on_message(style, _session.vibe)
    else:
        message = "\u266a Music off."
    return json.dumps({"message": message, **resp})


@mcp.tool()
def music_play(name: str) -> str:
    """Replay a saved music track by name.

    Finds the track in the music library and starts looping it.
    No generation, no credits used.

    .. note:: Calls ``_session.refresh_from_config()`` for consistency.

    Args:
        name: Track name (as shown by music_list).

    Returns:
        JSON string with a human-readable ``message`` field and
        the raw voxd response fields.
    """
    _session.refresh_from_config()
    client = _voxd_client()
    try:
        resp = client.music_play(name, owner_id=_session.session_id)
    except VoxdConnectionError:
        logger.warning("voxd unreachable in music_play", exc_info=True)
        return json.dumps(
            {
                "message": "\u266a Daemon unreachable.",
                "error": "daemon unreachable",
            }
        )
    except (VoxdProtocolError, WebSocketException, OSError, ValueError) as exc:
        logger.warning("voxd error in music_play", exc_info=True)
        return json.dumps(
            {
                "message": f"\u266a {exc}",
                "error": str(exc),
            }
        )

    _session.music_mode = "on"
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
    _session.refresh_from_config()
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
    except (VoxdProtocolError, WebSocketException, OSError, ValueError) as exc:
        logger.warning("voxd error in music_list", exc_info=True)
        return json.dumps(
            {
                "message": f"\u266a {exc}",
                "error": str(exc),
            }
        )

    raw_tracks: list[dict[str, object]] = resp.get("tracks", [])
    tracks = [MusicTrack.from_dict(t) for t in raw_tracks]
    if not tracks:
        message = "\u266a No saved tracks."
    else:
        lines = [f"\u266a {len(tracks)} saved track(s):"]
        lines.extend(f"  \u266a {track.display_line()}" for track in tracks)
        message = "\n".join(lines)
    return json.dumps({"message": message, **resp})


@mcp.tool()
def music_next() -> str:
    """Skip to a new generated track.

    Signals voxd to regenerate without changing vibe or style. The
    current track keeps playing until the new one is ready (gapless).

    Returns:
        JSON string with a human-readable ``message`` field and
        the raw voxd response fields.
    """
    _session.refresh_from_config()
    client = _voxd_client()
    try:
        resp = client.music_next(owner_id=_session.session_id)
    except VoxdConnectionError:
        logger.warning("voxd unreachable in music_next", exc_info=True)
        return json.dumps(
            {
                "message": "♪ Daemon unreachable.",
                "error": "daemon unreachable",
            }
        )
    except (VoxdProtocolError, WebSocketException, OSError, ValueError) as exc:
        logger.warning("voxd error in music_next", exc_info=True)
        return json.dumps(
            {
                "message": f"♪ {exc}",
                "error": str(exc),
            }
        )

    return json.dumps({"message": "♪ Skipping — generating next track...", **resp})


@mcp.tool()
def who(language: str | None = None) -> str:  # noqa: ARG001 -- reserved for future language filtering
    """List available voices for the current provider.

    Returns the voice roster with personality blurbs, the full list
    of available voices, and the current session voice.

    Args:
        language: ISO 639-1 language code to filter by (e.g. 'de', 'ko').

    Returns:
        JSON string with provider, current voice, featured voices,
        and full voice list.
    """
    _session.refresh_from_config()
    client = _voxd_client()

    try:
        all_voices = client.voices(provider=_session.provider)
    except VoxdConnectionError as exc:
        return _error(str(exc))
    except (VoxdProtocolError, WebSocketException, OSError, ValueError) as exc:
        logger.exception("Voice listing failed")
        return _error(str(exc))

    # Determine effective provider name for blurb lookup.
    provider_name = _session.provider or "elevenlabs"

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
            "current": _session.voice,
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
    _session.refresh_from_config()
    if mode not in _VALID_NOTIFY_MODES:
        return _error(f"Invalid mode '{mode}'. Use y/n/c.")

    updates: dict[str, str] = {"notify": mode}
    _session.set_notify(mode)

    # Initialize speak to "y" if not explicitly set yet.
    # "n" is the default sentinel; if it's still at default and we're
    # enabling notifications, default to voice mode.
    if mode in ("y", "c") and _session.speak == "n" and not _session.speak_explicit:
        updates["speak"] = "y"
        _session.set_speak("y", explicit=False)

    if voice is not None:
        updates["voice"] = voice
        _session.voice = voice

    # Persist to disk so hooks (which read config independently) see the change
    from punt_vox.config import write_fields

    write_fields(updates, _find_config_dir())

    return json.dumps({"notify": updates})


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
    _session.refresh_from_config()

    if mode not in _VALID_SPEAK_MODES:
        return _error(f"Invalid mode '{mode}'. Use y/n.")

    updates: dict[str, str] = {"speak": mode}
    _session.set_speak(mode)

    if voice is not None:
        updates["voice"] = voice
        _session.voice = voice

    # Persist to disk so hooks (which read config independently) see the change
    from punt_vox.config import write_fields

    write_fields(updates, _find_config_dir())

    return json.dumps(updates)


@mcp.tool()
def status() -> str:
    """Show current vox state (provider, voice, notify, vibe).

    Returns:
        JSON string with provider, voice, notify mode, speak mode,
        vibe mode, and current vibe.
    """
    _session.refresh_from_config()
    return json.dumps(
        {
            "provider": _session.provider,
            "voice": _session.voice,
            "notify": _session.notify,
            "speak": _session.speak,
            "vibe_mode": _session.vibe_mode,
            "vibe": _session.vibe,
            "vibe_tags": _session.vibe_tags,
            "vibe_signals": _session.vibe_signals,
            "music_mode": _session.music_mode,
        }
    )


# ---------------------------------------------------------------------------
# Server lifecycle
# ---------------------------------------------------------------------------


def run_server() -> None:
    """Run the MCP server with stdio transport."""
    global _session

    configure_logging(stderr_level="INFO")
    logger.info("Starting vox MCP server (mic)")

    # Seed session config from per-repo config if it exists.
    config_dir = _find_config_dir()
    _session = SessionConfig.from_config(config_dir)

    # Mark speak as explicitly set if the config file had it.
    if config_dir is not None:
        from punt_vox.config import read_field

        if read_field("speak", config_dir) is not None:
            _session.set_speak(_session.speak)

    logger.info(
        "Session config: notify=%s speak=%s voice=%s provider=%s vibe_mode=%s",
        _session.notify,
        _session.speak,
        _session.voice,
        _session.provider,
        _session.vibe_mode,
    )

    mcp.run(transport="stdio")


if __name__ == "__main__":
    run_server()
