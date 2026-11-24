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
        self._names = cls._unique(tuple(PurePath(project) for project in projects))
        return self

    def spoken(self) -> str:
        """Return the greeting, naming the repositories or how to allowlist one."""
        if not self._names:
            return (
                f"{_INTRO}, but no repository is allowlisted yet, "
                f"so run {_ALLOW_COMMAND} to add one."
            )
        return f"{_INTRO} in {self._listed()}."

    @classmethod
    def _unique(cls, paths: tuple[PurePath, ...]) -> tuple[str, ...]:
        """Grow each shared name by one parent segment until every name is unique.

        A name stops growing at its root: the policy resolves and de-duplicates
        the allowlist, so two entries never share every segment.
        """
        segments = tuple(cls._segments(path) for path in paths)
        depths = [1] * len(paths)
        while True:
            names = tuple(
                " ".join(parts[-depth:])
                for parts, depth in zip(segments, depths, strict=True)
            )
            shared = Counter(names)
            grown = [
                depth + 1 if shared[name] > 1 and depth < len(parts) else depth
                for parts, depth, name in zip(segments, depths, names, strict=True)
            ]
            if grown == depths:
                return names
            depths = grown

    @staticmethod
    def _segments(path: PurePath) -> tuple[str, ...]:
        """Return the spoken segments of a path, without its root."""
        return tuple(part for part in path.parts if part != path.anchor) or (str(path),)

    def _listed(self) -> str:
        if len(self._names) == 1:
            return self._names[0]
        return f"{', '.join(self._names[:-1])} and {self._names[-1]}"
