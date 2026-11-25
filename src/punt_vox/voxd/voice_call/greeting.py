"""The spoken opener of a call: who Vox is and which repositories it may use."""

from __future__ import annotations

from typing import Final, Self, final

from punt_vox.voxd.voice_call.repository_names import RepositoryNames

__all__ = ["CallGreeting"]

_INTRO: Final = "Hello, this is Vox. I start, steer, check and stop coding workers"
# Spoken, not typed: a TTS engine would read "<path>" aloud, brackets and all.
_ALLOW_COMMAND: Final = "vox worker allow followed by the repository path"


@final
class CallGreeting:
    """Two spoken sentences, so the caller never has to speak first."""

    __slots__ = ("_names",)
    _names: RepositoryNames

    def __new__(cls, projects: tuple[str, ...]) -> Self:
        """Name each repository uniquely, by its last segments."""
        self = super().__new__(cls)
        self._names = RepositoryNames(projects)
        return self

    def spoken(self) -> str:
        """Return the greeting, naming the repositories or how to allowlist one."""
        if not self._names:
            return (
                f"{_INTRO}, but no repository is allowlisted yet, "
                f"so run {_ALLOW_COMMAND} to add one."
            )
        return f"{_INTRO} in {self._names.listed()}."
