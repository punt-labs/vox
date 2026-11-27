"""Every setting of the voice agent that voxd defines, in one immutable object."""

from __future__ import annotations

from typing import Final, Self, final

from punt_vox.voxd.voice_agent.host_zone import HostZone

__all__ = ["AgentSettings"]

# Eric, a premade ElevenLabs voice every account has; no dashboard step needed.
VOICE_ID: Final = "cjVigY5qzO86Huf0OWal"
LLM: Final = "deepseek-v41-flash"
# -1 waits for the person; the agent never asks whether they are still there.
TURN_TIMEOUT: Final = -1
TURN_EAGERNESS: Final = "patient"
SKIP_TURN_WAIT: Final = -1
FIRST_MESSAGE: Final = "Hi, it's Vox. What are we working on?"
_MINIMAL_PROMPT: Final = "You are Vox, a voice assistant."


@final
class AgentSettings:
    """The prompt, greeting and zone voxd supplies; the rest are fixed defaults.

    The create body and the fingerprint are rendered from this object, so the
    agent that voxd provisions is the agent that this code describes, and a
    dashboard edit is never part of it.
    """

    __slots__ = ("_first_message", "_prompt", "_timezone")
    _prompt: str
    _first_message: str
    _timezone: str

    def __new__(
        cls,
        prompt: str,
        *,
        timezone: str,
        first_message: str = FIRST_MESSAGE,
    ) -> Self:
        """Validate the zone as an IANA name; an abbreviation is wrong half the year."""
        if not HostZone.is_iana(timezone):
            msg = (
                f"timezone must be an IANA name like America/New_York, not {timezone!r}"
            )
            raise ValueError(msg)
        self = super().__new__(cls)
        self._prompt, self._first_message, self._timezone = (
            prompt,
            first_message,
            timezone,
        )
        return self

    @classmethod
    def for_host(cls, prompt: str) -> Self:
        """Return the settings for this host: its zone, the given baseline prompt."""
        return cls(prompt, timezone=HostZone().name())

    @classmethod
    def minimal(cls) -> Self:
        """Return settings with a one-line prompt and UTC, for tests and doctor."""
        return cls(_MINIMAL_PROMPT, timezone="UTC")

    @property
    def prompt(self) -> str:
        """Return the baseline system prompt, replaced by the prompt of each call."""
        return self._prompt

    @property
    def first_message(self) -> str:
        """Return the agent's own greeting, for a conversation voxd did not start."""
        return self._first_message

    @property
    def timezone(self) -> str:
        """Return the IANA zone the agent starts with."""
        return self._timezone

    def with_zone(self, timezone: str) -> Self:
        """Return the same settings in another zone."""
        return type(self)(
            self._prompt, timezone=timezone, first_message=self._first_message
        )
