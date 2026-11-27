"""The spoken opener of a call: a conversational hello."""

from __future__ import annotations

from typing import Final, Self, final

__all__ = ["CallGreeting"]

_HELLO: Final = "Hi, it's Vox. What are we working on?"
# Spoken, not typed: a TTS engine would read "<path>" aloud, brackets and all.
_ALLOW_HINT: Final = (
    "To have me start coding workers, run vox worker allow "
    "followed by the repository path."
)


@final
class CallGreeting:
    """Greet first, so the caller never has to speak first."""

    __slots__ = ("_has_repositories",)
    _has_repositories: bool

    def __new__(cls, projects: tuple[str, ...]) -> Self:
        """Note whether any repository is allowlisted for workers."""
        self = super().__new__(cls)
        self._has_repositories = bool(projects)
        return self

    def spoken(self) -> str:
        """Return the hello, with how to allowlist a repository when none is."""
        if self._has_repositories:
            return _HELLO
        return f"{_HELLO} {_ALLOW_HINT}"
