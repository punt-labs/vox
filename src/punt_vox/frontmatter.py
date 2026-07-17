"""Read/write access to a single vox config file's YAML frontmatter."""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Self, final

logger = logging.getLogger(__name__)

__all__ = ["Frontmatter"]

_FIELD_RE = re.compile(r'^([a-z_]+):\s*"?([^"\n]*)"?\s*$', re.MULTILINE)
_CLOSING_FENCE_RE = re.compile(r"\n---\s*$", re.MULTILINE)

# Expressive mood text: log its length, never the content (PY-CS-11 privacy).
_REDACTED_KEYS = frozenset({"vibe", "vibe_tags"})


@final
class Frontmatter:
    """Owns one config file and reads/writes its YAML frontmatter fields."""

    __slots__ = ("_path",)

    _path: Path

    def __new__(cls, path: Path) -> Self:
        self = super().__new__(cls)
        self._path = path
        return self

    @property
    def path(self) -> Path:
        """Return the backing file path."""
        return self._path

    @staticmethod
    def validate_value(value: str) -> None:
        """Reject values that would corrupt the ``key: "<value>"`` round-trip.

        The parser reads up to the first quote or newline, so either would
        truncate the field. Apostrophes are safe, so ``I'm tired`` survives.
        """
        if "\n" in value or "\r" in value:
            msg = f"config values must not contain newlines, got: {value!r}"
            raise ValueError(msg)
        if '"' in value:
            msg = f"config values must not contain double-quotes, got: {value!r}"
            raise ValueError(msg)

    def _validate_all(self, updates: dict[str, str]) -> None:
        """Reject the whole batch if any value would corrupt the file."""
        for value in updates.values():
            self.validate_value(value)

    def read_fields(self) -> dict[str, str]:
        """Return all non-empty frontmatter fields, or ``{}`` if unreadable."""
        text = self._read_text()
        if text is None:
            return {}
        fields: dict[str, str] = {}
        for match in _FIELD_RE.finditer(text):
            val = match.group(2).strip()
            if val:
                fields[match.group(1)] = val
        return fields

    def read_field(self, field: str) -> str | None:
        """Return a single frontmatter field, or ``None`` if absent/unreadable."""
        text = self._read_text()
        if text is None:
            return None
        pattern = re.compile(
            rf"^{re.escape(field)}:\s*\"?([^\"\n]*)\"?\s*$", re.MULTILINE
        )
        match = pattern.search(text)
        if match and match.group(1).strip():
            return match.group(1).strip()
        return None

    def write_field(self, key: str, value: str) -> None:
        """Write a single key-value pair into the frontmatter."""
        self.write_fields({key: value})

    def write_fields(self, updates: dict[str, str]) -> None:
        """Write multiple key-value pairs in a single read-write cycle.

        Every value is validated up front, so the serialization invariant
        is enforced by the class that serializes -- a caller bypassing
        ``ConfigStore`` still cannot corrupt the frontmatter (PY-EH-1).
        """
        self._validate_all(updates)
        self._path.parent.mkdir(parents=True, exist_ok=True)

        if not self._path.exists():
            self._path.write_text(self._render(updates), encoding="utf-8")
            return

        text = self._path.read_text(encoding="utf-8")
        for key, value in updates.items():
            replacement = f'{key}: "{value}"'
            field_re = re.compile(
                rf"^{re.escape(key)}:\s*\"?[^\"\n]*\"?\s*$", re.MULTILINE
            )
            if field_re.search(text):
                text = field_re.sub(replacement, text)
            elif _CLOSING_FENCE_RE.search(text):
                text = _CLOSING_FENCE_RE.sub(f"\n{replacement}\n---", text, count=1)
            else:
                logger.warning("Malformed config (no closing ---): %s", self._path)
                text = self._render(updates)
                break

        self._path.write_text(text, encoding="utf-8")
        for key, value in updates.items():
            shown = f"<{len(value)} chars>" if key in _REDACTED_KEYS else repr(value)
            logger.info("Config: set %s = %s in %s", key, shown, self._path)

    def _read_text(self) -> str | None:
        """Return the file's text, or ``None`` when missing or unreadable.

        Symmetric with the write path: an existing-but-unreadable config
        (permissions or IO fault) degrades to defaults rather than crashing
        the hook subprocess (PY-EH-1).
        """
        if not self._path.exists():
            return None
        try:
            return self._path.read_text(encoding="utf-8")
        except OSError as exc:
            logger.warning(
                "Config unreadable, using defaults: %s (%s)", self._path, exc
            )
            return None

    @staticmethod
    def _render(updates: dict[str, str]) -> str:
        """Return a complete frontmatter block for *updates*."""
        lines = [f'{k}: "{v}"' for k, v in updates.items()]
        return "---\n" + "\n".join(lines) + "\n---\n"
