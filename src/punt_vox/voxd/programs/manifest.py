"""The on-disk Program manifest -- the source of truth CLI, MCP, and daemon share.

A ``ProgramManifest`` is the serialized description of a Program: its name,
format, authoring subject, and the ordered Parts (each with an intrinsic index,
a file, a status, and either a duration or a failure reason). It is the minimal
record the CLI needs to play and advance a saved Program with no daemon and no
regeneration. Serialization round-trips through JSON; deserialization raises on
a malformed record rather than returning a half-built manifest (PY-EH-8).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Self, final

from punt_vox.voxd.programs.format import Format
from punt_vox.voxd.programs.identifiers import PartRef, ProgramName
from punt_vox.voxd.programs.part import Part, PartStatus
from punt_vox.voxd.programs.wire import JsonObject

__all__ = ["PartEntry", "PlaylistSubject", "ProgramManifest"]


@final
@dataclass(frozen=True, slots=True)
class PlaylistSubject:
    """The authoring key of a playlist Program: its (vibe, style).

    Phase 1's only ``Subject`` variant. Podcast/audiobook add their own
    variants later; the manifest's top-level ``format`` tags which one a
    record carries, so deserialization stays total and typed -- never
    ``dict[str, object]`` in the domain.
    """

    vibe: str
    style: str

    def to_dict(self) -> dict[str, str]:
        """Return the JSON object form ``{"vibe": ..., "style": ...}``."""
        return {"vibe": self.vibe, "style": self.style}

    @classmethod
    def from_wire(cls, obj: JsonObject) -> Self:
        """Build a subject from a wire object, raising on a missing field."""
        return cls(vibe=obj.require_str("vibe"), style=obj.require_str("style"))


@final
@dataclass(frozen=True, slots=True)
class PartEntry:
    """One Part's on-disk record: intrinsic index, file, status, and outcome.

    A ``ready`` entry carries a ``duration_ms`` and no reason; a ``failed``
    entry carries a ``reason`` and no duration. Both optionals are the
    documented shape of the two mutually-exclusive outcomes, not "the type
    system gave up" -- ``duration_ms`` is absent precisely when the Part never
    produced audio, and ``reason`` is absent precisely when it succeeded.
    """

    index: int
    file: str
    status: PartStatus
    duration_ms: int | None = None  # present iff status is READY
    reason: str | None = None  # present iff status is FAILED

    @property
    def is_ready(self) -> bool:
        """Return whether this entry is a playable, ready Part."""
        return self.status is PartStatus.READY

    def as_part(self) -> Part:
        """Return the domain :class:`Part` this entry addresses.

        The file name is the content-addressed identity; the intrinsic index
        is the stable ``playlist:N`` position.
        """
        return Part(self.file, self.index)

    def to_dict(self) -> dict[str, object]:
        """Return the JSON object form, omitting the absent optional."""
        record: dict[str, object] = {
            "index": self.index,
            "file": self.file,
            "status": self.status.value,
        }
        if self.duration_ms is not None:
            record["duration_ms"] = self.duration_ms
        if self.reason is not None:
            record["reason"] = self.reason
        return record

    @classmethod
    def from_wire(cls, obj: JsonObject) -> Self:
        """Build an entry from a wire object, raising on a malformed record."""
        return cls(
            index=obj.require_int("index"),
            file=obj.require_str("file"),
            status=PartStatus(obj.require_str("status")),
            duration_ms=obj.opt_int("duration_ms"),
            reason=obj.opt_str("reason"),
        )


@final
class ProgramManifest:
    """The persisted description of one Program and its ordered Parts."""

    __slots__ = ("_format", "_name", "_parts", "_subject")
    _name: ProgramName
    _format: Format
    _subject: PlaylistSubject
    _parts: tuple[PartEntry, ...]

    def __new__(
        cls,
        *,
        name: ProgramName,
        fmt: Format,
        subject: PlaylistSubject,
        parts: tuple[PartEntry, ...],
    ) -> Self:
        self = super().__new__(cls)
        self._name = name
        self._format = fmt
        self._subject = subject
        self._parts = tuple(sorted(parts, key=lambda entry: entry.index))
        return self

    @property
    def name(self) -> ProgramName:
        """Return the Program's addressable name."""
        return self._name

    @property
    def format(self) -> Format:
        """Return the Program format."""
        return self._format

    @property
    def subject(self) -> PlaylistSubject:
        """Return the authoring subject."""
        return self._subject

    @property
    def parts(self) -> tuple[PartEntry, ...]:
        """Return every Part entry, ordered by intrinsic index."""
        return self._parts

    def ready_parts(self) -> tuple[Part, ...]:
        """Return the domain Parts for the ready entries, ordered by index."""
        return tuple(entry.as_part() for entry in self._parts if entry.is_ready)

    def resolve_part(self, ref: PartRef) -> Part:
        """Return the ready Part whose intrinsic index matches ``ref``, else raise.

        Matches on the intrinsic manifest index (MAJOR-1), never list position:
        with a gap from a permanent fill failure (ready indices 1, 2, 4),
        ``playlist:4`` finds the index-4 Part, not the fourth pool slot. An
        unmatched index raises ``ValueError`` naming it and the ready indices.
        """
        ready = self.ready_parts()
        for part in ready:
            if part.index == ref.index:
                return part
        available = ", ".join(str(part.index) for part in ready)
        msg = f"{self._name.value!r} has no part {ref.index}; ready parts: {available}"
        raise ValueError(msg)

    def next_index(self) -> int:
        """Return the 1-based index the next recorded Part will take."""
        return 1 + max((entry.index for entry in self._parts), default=0)

    def with_part(self, entry: PartEntry) -> ProgramManifest:
        """Return a successor manifest with ``entry`` appended (re-sorted)."""
        return ProgramManifest(
            name=self._name,
            fmt=self._format,
            subject=self._subject,
            parts=(*self._parts, entry),
        )

    def to_json(self) -> str:
        """Return the pretty-printed JSON serialization."""
        record = {
            "name": self._name.value,
            "format": self._format.value,
            "subject": self._subject.to_dict(),
            "parts": [entry.to_dict() for entry in self._parts],
        }
        return json.dumps(record, indent=2, ensure_ascii=False)

    @classmethod
    def from_json(cls, text: str) -> Self:
        """Parse a manifest from JSON, raising ``ValueError`` on a bad record."""
        obj = JsonObject.parse(text, "manifest")
        parts = obj.require_list("parts")
        return cls(
            name=ProgramName(obj.require_str("name")),
            fmt=Format(obj.require_str("format")),
            subject=PlaylistSubject.from_wire(obj.require_object("subject")),
            parts=tuple(
                PartEntry.from_wire(JsonObject.coerce(item, "manifest.parts"))
                for item in parts
            ),
        )

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, ProgramManifest):
            return NotImplemented
        return self._identity() == other._identity()

    def __hash__(self) -> int:
        return hash((ProgramManifest, *self._identity()))

    def __repr__(self) -> str:
        return f"ProgramManifest(name={self._name!s}, parts={len(self._parts)})"

    def _identity(
        self,
    ) -> tuple[ProgramName, Format, PlaylistSubject, tuple[PartEntry, ...]]:
        return (self._name, self._format, self._subject, self._parts)
