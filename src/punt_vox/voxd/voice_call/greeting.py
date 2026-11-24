"""The spoken opener of a call: who Vox is and which repositories it may use."""

from __future__ import annotations

from pathlib import PurePath
from typing import Final, Self, final

__all__ = ["CallGreeting"]

_INTRO: Final = "Hello, this is Vox. I start, steer, check and stop coding workers"
_ALLOW_COMMAND: Final = "vox worker allow <path>"


@final
class CallGreeting:
    """Two spoken sentences, so the caller never has to speak first."""

    __slots__ = ("_names",)
    _names: tuple[str, ...]

    def __new__(cls, projects: tuple[str, ...]) -> Self:
        """Keep only the last path segment of each allowlisted repository."""
        self = super().__new__(cls)
        self._names = tuple(PurePath(project).name or project for project in projects)
        return self

    def spoken(self) -> str:
        """Return the greeting, naming the repositories or how to allowlist one."""
        if not self._names:
            return (
                f"{_INTRO}, but no repository is allowlisted yet, "
                f"so run {_ALLOW_COMMAND} to add one."
            )
        return f"{_INTRO} in {self._listed()}."

    def _listed(self) -> str:
        if len(self._names) == 1:
            return self._names[0]
        return f"{', '.join(self._names[:-1])} and {self._names[-1]}"
