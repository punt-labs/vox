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
from punt_vox.client import VoxdConnectionError, VoxdProtocolError
from punt_vox.client_gateway import ClientProgramGateway
from punt_vox.client_sync import VoxClientSync
from punt_vox.config import write_fields
from punt_vox.logging_config import configure_logging
from punt_vox.music_prompts import PromptSet
from punt_vox.program_control import StartRequest
from punt_vox.program_gateway import ProgramGateway
from punt_vox.types_synthesis import SynthesisSpec
from punt_vox.vibe import VibeChange
from punt_vox.voices import VOICE_BLURBS
from punt_vox.voxd.programs.identifiers import ProgramName

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

    def change_vibe(self, change: VibeChange) -> dict[str, str]:
        """Apply an authoritative vibe change; return the fields to persist.

        The transition rules live on ``VibeChange``; this method mirrors the
        resolved updates into the in-memory session (an empty string clears
        the field back to ``None``).  Raises ``ValueError`` for a bad mode.
        """
        updates = change.resolve()
        if "vibe" in updates:
            self._vibe = updates["vibe"] or None
        if "vibe_tags" in updates:
            self._vibe_tags = updates["vibe_tags"] or None
        if "vibe_signals" in updates:
            self._vibe_signals = updates["vibe_signals"]
        if "vibe_mode" in updates:
            self._vibe_mode = updates["vibe_mode"]
        return updates

    def generating_message(self, style: str | None) -> str:
        """Return the ♪ line for a music-on request.

        Reads the session vibe (``self._vibe``) only to *personalise the display*
        -- it is never fed back as an input to a Program transition (vox-73m5).
        """
        prefix = "♪ Music on — generating"
        vibe = self._vibe
        if style and vibe:
            return f"{prefix} a {style} track for your {vibe} mood..."
        if style:
            return f"{prefix} a {style} track..."
        if vibe:
            return f"{prefix} a track for your {vibe} mood..."
        return f"{prefix} ambient music..."

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


# The daemon-facing seam the music/status tools drive. A module-level value (not
# a factory) so it adds no procedural surface; tests replace it with an in-memory
# FakeProgramGateway. It holds a VoxClientSync that opens a fresh connection per
# call, so no session or owner travels with a command (design section 4).
_program_tools: ProgramGateway = ClientProgramGateway(VoxClientSync())


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
            updates = {
                key: value
                for key, value in (
                    ("provider", provider),
                    ("model", model),
                    ("vibe_tags", vibe_tags),
                )
                if value is not None
            }
            if updates:
                return json.dumps({"status": "config updated", **updates})
            return _error("Provide text or segments.")
        segments = [{"text": text}]

    effective_provider = provider or _session.provider
    client = _voxd_client()

    def _synth_handler(seg_text: str, seg_spec: SynthesisSpec) -> dict[str, object]:
        result = client.synthesize(seg_text, seg_spec)
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
        mp3_bytes = client.record(seg_text, seg_spec)

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
    try:
        updates = _session.change_vibe(VibeChange(mood=mood, tags=tags, mode=mode))
    except ValueError:
        return _error(f"Invalid mode '{mode}'. Use auto/manual/off.")

    if not updates:
        return _error("Provide at least one of: mood, tags, mode.")

    # Persist to disk so hooks (which read config independently) see the change.
    # The session vibe is NOT pushed to the Program here: vox-73m5 shipped broken
    # because a stale session vibe was replayed as an authoritative music
    # transition. The vibe is display/record state; a Program retune is a
    # deliberate music command, never a side effect of setting the session mood.
    write_fields(updates, _find_config_dir())

    return json.dumps({"vibe": updates})


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

    ``mode`` is "on"/"off"; ``style`` persists across calls; ``name`` replays or
    saves a track; ``base_prompt`` + the 12 ``variations`` require each other.
    Returns a JSON string with a ``message`` line and the ``applied`` result.
    """
    _session.refresh_from_config()
    if mode not in ("on", "off"):
        return _error(f"Invalid mode '{mode}'. Use on/off.")
    try:
        if mode == "on":
            prompts = PromptSet.from_tool_args(base_prompt, variations)
            outcome = _program_tools.start(
                StartRequest(style=style, name=name, prompts=prompts)
            )
            _session.music_mode = "on"
            message = _session.generating_message(style)
        else:
            outcome = _program_tools.stop()
            _session.music_mode = "off"
            message = "\u266a Music off."
    except ValueError as exc:  # malformed prompt shape, surfaced at the boundary
        return _error(str(exc))
    except (VoxdConnectionError, VoxdProtocolError, WebSocketException, OSError) as exc:
        _session.music_mode = "off"
        return _error(str(exc))
    return json.dumps({"message": message, "applied": outcome.applied})


@mcp.tool()
def music_play(name: str) -> str:
    """Replay a saved Program by name -- from disk, no generation, no credits.

    Args:
        name: Program name (as shown by ``music_list``).

    Returns:
        JSON string with a ``message`` line and the ``applied`` result.
    """
    _session.refresh_from_config()
    try:
        outcome = _program_tools.play(ProgramName(name), None)
    except ValueError as exc:  # empty or path-bearing name
        return _error(str(exc))
    except (VoxdConnectionError, VoxdProtocolError, WebSocketException, OSError) as exc:
        return _error(str(exc))
    _session.music_mode = "on"
    return json.dumps(
        {"message": f"\u266a {outcome.message}", "applied": outcome.applied}
    )


@mcp.tool()
def music_list() -> str:
    """Show saved Programs, grouped by name with their ready/total part counts.

    Returns:
        JSON string with a ``message`` line and a ``programs`` list.
    """
    _session.refresh_from_config()
    try:
        summaries = _program_tools.catalog()
    except (VoxdConnectionError, VoxdProtocolError, WebSocketException, OSError) as exc:
        return _error(str(exc))
    if not summaries:
        message = "\u266a No saved programs."
    else:
        lines = [f"\u266a {len(summaries)} saved program(s):"]
        lines.extend(f"  \u266a {summary.display_line()}" for summary in summaries)
        message = "\n".join(lines)
    programs = [
        {"name": s.name, "format": s.format, "ready": s.ready, "total": s.total}
        for s in summaries
    ]
    return json.dumps({"message": message, "programs": programs})


@mcp.tool()
def music_next() -> str:
    """Advance to another Part -- the one ungated skip/next transition.

    Returns:
        JSON string with a ``message`` line and the ``applied`` result.
    """
    _session.refresh_from_config()
    try:
        outcome = _program_tools.advance()
    except (VoxdConnectionError, VoxdProtocolError, WebSocketException, OSError) as exc:
        return _error(str(exc))
    message = "♪ Skipping — generating next track..."
    return json.dumps({"message": message, "applied": outcome.applied})


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
    write_fields(updates, _find_config_dir())

    return json.dumps(updates)


@mcp.tool()
def status() -> str:
    """Show current vox state (provider, voice, notify, vibe) and the Program.

    The ``program`` block is the daemon's *authoritative* Program status, read
    fresh from ``voxd`` on every call -- never a server-side cache. vox-73m5
    shipped broken because the server served a stale music shadow; here a client
    asking "what is playing?" always gets what the daemon actually holds. When
    ``voxd`` is unreachable the block carries an ``error`` instead of stale data.

    Returns:
        JSON string with the session display fields plus the authoritative
        ``program`` status.
    """
    _session.refresh_from_config()
    payload: dict[str, object] = {
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
    try:
        payload["program"] = _program_tools.status().to_dict()
    except (VoxdConnectionError, VoxdProtocolError, WebSocketException, OSError) as exc:
        payload["program"] = {"error": str(exc)}
    return json.dumps(payload)


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
