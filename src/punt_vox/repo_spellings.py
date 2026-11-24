"""How a disallow names a stored allowlist entry: by text, or by resolved path."""

from __future__ import annotations

from pathlib import Path
from typing import Self, final

__all__ = ["RepoSpellings"]


@final
class RepoSpellings:
    """The spellings one disallow offers for a stored entry, matched leniently.

    A disallow names an entry by the path the client resolved and, when it
    differs, by the path as the operator typed it. A stored entry matches a
    spelling by string equality, or by resolving to the same path when both
    are absolute, so an entry that does not validate (one written by hand,
    which would otherwise block every strict read) can still be removed by
    typing it exactly.
    """

    __slots__ = ("_spellings",)

    _spellings: tuple[str, ...]

    def __new__(
        cls,
        resolved: str,
        spelling: str | None = None,  # None: the resolved path is the only spelling.
    ) -> Self:
        """Bind the resolved path and the spelling as typed; refuse a blank one.

        Raises:
            ValueError: If either spelling is blank.
        """
        spellings = (
            (resolved,) if spelling in (None, resolved) else (resolved, spelling)
        )
        for raw in spellings:
            if not raw.strip():
                raise ValueError(f"worker_repos entry blank: {raw!r}")
        self = super().__new__(cls)
        self._spellings = spellings
        return self

    @property
    def resolved(self) -> str:
        """Return the resolved path, the spelling a refusal names."""
        return self._spellings[0]

    def fields(self) -> dict[str, str]:
        """Return the wire fields: ``project``, plus ``spelling`` when it differs."""
        fields = {"project": self.resolved}
        if len(self._spellings) == 2:
            fields["spelling"] = self._spellings[1]
        return fields

    def names(self, stored: str) -> bool:
        """Return whether *stored* is one of the spellings, by text or resolved path."""
        return any(self._same(stored, raw) for raw in self._spellings)

    @staticmethod
    def _same(stored: str, raw: str) -> bool:
        if stored == raw:
            return True
        resolved = RepoSpellings._resolved(raw)
        return resolved is not None and RepoSpellings._resolved(stored) == resolved

    @staticmethod
    def _resolved(
        text: str,
    ) -> Path | None:  # None: the spelling has no resolved form.
        """Return the resolved path of an absolute spelling, else ``None``.

        A relative spelling has no resolved form. Neither does one whose
        home directory cannot be found (``~user`` for an unknown account) or
        whose resolution fails on disk: such an entry can only ever match by
        text, and it must never stop the other entries from being compared.
        """
        try:
            path = Path(text).expanduser()
            return path.resolve() if path.is_absolute() else None
        except (RuntimeError, OSError):
            return None
