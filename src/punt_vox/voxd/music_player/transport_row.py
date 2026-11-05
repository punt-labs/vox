"""``TransportRow`` -- the prev / play-pause / next / stop control bar of the scene.

The row is a columns group of four buttons whose glyph, tooltip, ``publish`` topic,
and ``disabled`` state are all pure functions of the :class:`PlayerView` (Z ``T6``
for the play/pause glyph, ``T5`` for the prev/next bounds, and the idle-inert rule).
It renders every button ``disabled`` when nothing plays -- playback starts from the
album-list Play buttons, which stay enabled in every mode and switch -- so the
transport is inert exactly when the player is idle.

The play/pause button is one button: while ``playing`` it shows ``⏸`` and publishes
``music.pause``; while ``paused`` it shows ``⏵`` and publishes ``music.resume`` -- the
single unambiguous transition for the mode it was rendered in. The glyphs are the
operator-verified media characters on the lux ImGui/macOS font stack (``U+23F5`` for
play, never the undersized ``U+25B6``).
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Final, final

from punt_vox.voxd.music_player.player_view import PlayerMode, PlayerView
from punt_vox.voxd.music_player.wire import MusicTopic

__all__ = ["TransportRow"]

_PREV_GLYPH: Final = "⏮"  # ⏮
_PAUSE_GLYPH: Final = "⏸"  # ⏸ (shown while playing -- press to pause)
_PLAY_GLYPH: Final = "⏵"  # ⏵ (shown while paused -- press to resume)
_NEXT_GLYPH: Final = "⏭"  # ⏭
_STOP_GLYPH: Final = "⏹"  # ⏹

_ROW_ID: Final = "music.transport"


@final
@dataclass(frozen=True, slots=True)
class _TransportButton:
    """One transport button: a glyph label, a tooltip, a topic, and a disabled flag."""

    element_id: str
    label: str
    tooltip: str
    topic: MusicTopic
    disabled: bool = False

    def to_dict(self) -> dict[str, object]:
        """Return the button's wire dict (matching the lux button element shape)."""
        return {
            "kind": "button",
            "id": self.element_id,
            "label": self.label,
            "tooltip": self.tooltip,
            "disabled": self.disabled,
            "publish": {"topic": self.topic.value},
        }


# Each button's face -- id, glyph, tooltip, topic -- is fixed; only whether it is
# disabled depends on the view. The play/pause button has two faces, each carrying
# its glyph, tooltip, and topic together so they can never disagree about which
# transition a press means.
_PREV: Final = _TransportButton(
    "music.transport.prev", _PREV_GLYPH, "Previous", MusicTopic.PREV
)
_PAUSE: Final = _TransportButton(
    "music.transport.playpause", _PAUSE_GLYPH, "Pause", MusicTopic.PAUSE
)
_PLAY: Final = replace(
    _PAUSE, label=_PLAY_GLYPH, tooltip="Play", topic=MusicTopic.RESUME
)
_NEXT: Final = _TransportButton(
    "music.transport.next", _NEXT_GLYPH, "Next", MusicTopic.NEXT
)
_STOP: Final = _TransportButton(
    "music.transport.stop", _STOP_GLYPH, "Stop", MusicTopic.STOP
)


@final
@dataclass(frozen=True, slots=True)
class TransportRow:
    """Project the player view onto the four-button transport control row."""

    view: PlayerView

    def to_dict(self) -> dict[str, object]:
        """Return the transport row's wire dict: a columns group of four buttons.

        Prev and next also grey out at the pool's bounds (T5), where a press would
        be a no-op; stop and play/pause grey out only when nothing plays.
        """
        idle = self._idle
        buttons = (
            replace(_PREV, disabled=idle or self._at_first),
            replace(self._play_pause, disabled=idle),
            replace(_NEXT, disabled=idle or self._at_last),
            replace(_STOP, disabled=idle),
        )
        return {
            "kind": "group",
            "id": _ROW_ID,
            "layout": "columns",
            "children": [button.to_dict() for button in buttons],
        }

    @property
    def _play_pause(self) -> _TransportButton:
        """Return the play/pause face for the mode (T6).

        Playing shows ``⏸`` and publishes ``music.pause``; paused shows ``⏵`` and
        publishes ``music.resume``. When idle the button wears the play glyph,
        ready to resume nothing (and :meth:`to_dict` disables it).
        """
        return _PAUSE if self.view.mode is PlayerMode.PLAYING else _PLAY

    @property
    def _idle(self) -> bool:
        """Return whether the transport is inert (no album active)."""
        return not self.view.mode.is_active

    @property
    def _at_first(self) -> bool:
        """Return whether the cursor sits on the first part (prev is a no-op)."""
        cursor = self.view.now_playing
        return cursor is not None and cursor.index <= 1

    @property
    def _at_last(self) -> bool:
        """Return whether the cursor sits on the last part (next is a no-op)."""
        cursor = self.view.now_playing
        return cursor is not None and cursor.index >= cursor.of
