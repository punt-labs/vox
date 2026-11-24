"""The spoken opener of a call: who Vox is and which repositories it may use."""

from __future__ import annotations

from collections import Counter
from pathlib import PurePath
from typing import Final, Self, final

__all__ = ["CallGreeting"]

_INTRO: Final = "Hello, this is Vox. I start, steer, check and stop coding workers"
# Spoken, not typed: a TTS engine would read "<path>" aloud, brackets and all.
_ALLOW_COMMAND: Final = "vox worker allow followed by the repository path"


@final
class CallGreeting:
    """Two spoken sentences, so the caller never has to speak first."""

    __slots__ = ("_names",)
    _names: tuple[str, ...]

    def __new__(cls, projects: tuple[str, ...]) -> Self:
        """Name each repository by its last path segment, qualified when shared."""
        self = super().__new__(cls)
        paths = tuple(PurePath(project) for project in projects)
        shared = Counter(path.name for path in paths)
        self._names = tuple(cls._spoken(path, shared) for path in paths)
        return self

    def spoken(self) -> str:
        """Return the greeting, naming the repositories or how to allowlist one."""
        if not self._names:
            return (
                f"{_INTRO}, but no repository is allowlisted yet, "
                f"so run {_ALLOW_COMMAND} to add one."
            )
        return f"{_INTRO} in {self._listed()}."

    @staticmethod
    def _spoken(path: PurePath, shared: Counter[str]) -> str:
        """Return the last segment, with its parent in front when another shares it."""
        if shared[path.name] > 1:
            return f"{path.parent.name} {path.name}".strip()
        return path.name or str(path)

    def _listed(self) -> str:
        if len(self._names) == 1:
            return self._names[0]
        return f"{', '.join(self._names[:-1])} and {self._names[-1]}"
