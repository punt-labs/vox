"""``PanelState`` -- the panel's settings, read once and held between clicks.

Held by :class:`~punt_vox.panel.service.VoxPanelService` so a click's visible
answer never waits on a fresh read: the state is refreshed off the loop and
projected onto a scene only when someone is about to look at it.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Self, final

from punt_vox.panel.panel_scene import PanelScene
from punt_vox.voice_target import VoiceTarget

if TYPE_CHECKING:
    from punt_vox.panel.ports import SettingsSource
    from punt_vox.voice_target import RosterClient

__all__ = ["PanelState"]


@final
@dataclass(frozen=True, slots=True)
class PanelState:
    """The settings snapshot the panel shows: notify, speak, voice engine, roster."""

    notify: str
    speak: str
    voice: str | None
    roster: tuple[str, ...]
    provider: str | None = None
    model: str | None = None

    @staticmethod
    def _normalise(raw: str | None) -> str | None:
        """Return a stripped provider or model, treating blanks as absent."""
        return (raw or "").strip() or None

    @classmethod
    def empty(cls) -> Self:
        """Return the safe default held before the first successful read."""
        return cls(notify="n", speak="y", voice=None, roster=())

    @classmethod
    def read(cls, store: SettingsSource) -> Self:
        """Read the config independently of the daemon's voice roster."""
        cfg = store.read()
        return cls(
            notify=cfg.notify,
            speak=cfg.speak,
            voice=cfg.voice,
            roster=(),
            provider=cls._normalise(cfg.provider),
            model=cls._normalise(cfg.model),
        )

    def with_roster(self, client: RosterClient) -> Self:
        """Read the voice roster for the configured provider and model."""
        if self.provider is None:
            return self
        roster = client.voices(VoiceTarget(self.provider, self.model))
        return replace(self, roster=tuple(roster))

    def with_notify(self, code: str) -> Self:
        """Return a copy with ``notify`` set to ``code``."""
        return replace(self, notify=code)

    def with_speak(self, code: str) -> Self:
        """Return a copy with ``speak`` set to ``code``."""
        return replace(self, speak=code)

    def with_voice(self, voice: str) -> Self:
        """Return a copy with ``voice`` set to ``voice``."""
        return replace(self, voice=voice)

    def with_provider(
        self,
        provider: str,
        *,
        roster: tuple[str, ...],
        model: str | None,
        voice: str | None,
    ) -> Self:
        """Return a copy with the supplied provider, model, voice, and roster.

        Store all fields even when the provider is unchanged: a refreshed roster can
        accompany the same provider. Model and voice names must be valid for that
        provider; None means no model or no available voice.
        """
        return replace(self, provider=provider, model=model, roster=roster, voice=voice)

    def scene(self) -> PanelScene:
        """Return this state as the scene it projects onto."""
        return PanelScene(
            notify=self.notify,
            speak=self.speak,
            voice=self.voice,
            roster=self.roster,
            provider=self.provider,
            model=self.model,
        )
