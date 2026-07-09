"""The on-disk album manifest -- the source of truth CLI, MCP, and daemon share.

An :class:`AlbumManifest` is the persisted description of one album: its unique
:class:`AlbumId`, its queryable :class:`AlbumTags`, the tz-aware ``created``
stamp, the hidden :class:`PromptFingerprint` of the authoring prompt-set, and the
ordered Parts. The directory name is *never* parsed back -- identity lives in the
manifest -- so a cosmetic slug collision is harmless. Deserialization is total:
it raises on a malformed record (PY-EH-8). :class:`ManifestDraft` is the
pre-``created`` record the store stamps and persists.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Self, final

from punt_vox.voxd.programs.album_id import AlbumId
from punt_vox.voxd.programs.album_tags import AlbumTags, PromptFingerprint
from punt_vox.voxd.programs.format import Format
from punt_vox.voxd.programs.identifiers import PartRef
from punt_vox.voxd.programs.part import Part, PartStatus
from punt_vox.voxd.programs.wire import JsonObject

__all__ = ["AlbumManifest", "ManifestDraft", "PartEntry"]


@final
@dataclass(frozen=True, slots=True)
class PartEntry:
    """One Part's on-disk record: intrinsic index, file, status, and outcome.

    A ``ready`` entry carries a ``duration_ms`` and no reason; a ``failed``
    entry carries a ``reason`` and no duration. Both optionals are the
    documented shape of the two mutually-exclusive outcomes -- ``duration_ms``
    is absent precisely when the Part never produced audio, and ``reason`` is
    absent precisely when it succeeded.
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
class AlbumManifest:
    """The persisted description of one album -- id, tags, timestamp, and Parts."""

    __slots__ = (
        "_created",
        "_fingerprint",
        "_format",
        "_id",
        "_parts",
        "_tags",
    )
    _id: AlbumId
    _format: Format
    _tags: AlbumTags
    _created: datetime
    _fingerprint: PromptFingerprint
    _parts: tuple[PartEntry, ...]

    def __new__(
        cls,
        *,
        album_id: AlbumId,
        fmt: Format,
        tags: AlbumTags,
        created: datetime,
        fingerprint: PromptFingerprint,
        parts: tuple[PartEntry, ...],
    ) -> Self:
        self = super().__new__(cls)
        self._id = album_id
        self._format = fmt
        self._tags = tags
        self._created = created
        self._fingerprint = fingerprint
        self._parts = cls._sorted(parts)
        return self

    @staticmethod
    def _sorted(parts: tuple[PartEntry, ...]) -> tuple[PartEntry, ...]:
        """Return the parts ordered by intrinsic index (the stable manifest order)."""
        return tuple(sorted(parts, key=lambda entry: entry.index))

    @property
    def id(self) -> AlbumId:
        """Return the album's unique id."""
        return self._id

    @property
    def format(self) -> Format:
        """Return the Program format."""
        return self._format

    @property
    def tags(self) -> AlbumTags:
        """Return the album's queryable tags."""
        return self._tags

    @property
    def created(self) -> datetime:
        """Return the tz-aware UTC creation timestamp."""
        return self._created

    @property
    def prompt_fingerprint(self) -> PromptFingerprint:
        """Return the fingerprint of the prompt-set that authored this album."""
        return self._fingerprint

    @property
    def parts(self) -> tuple[PartEntry, ...]:
        """Return every Part entry, ordered by intrinsic index."""
        return self._parts

    def ready_parts(self) -> tuple[Part, ...]:
        """Return the domain Parts for the ready entries, ordered by index."""
        return tuple(entry.as_part() for entry in self._parts if entry.is_ready)

    def resolve_part(self, ref: PartRef) -> Part:
        """Return the ready Part whose intrinsic index matches ``ref``, else raise.

        Matches on the intrinsic manifest index, never list position:
        with a gap from a permanent fill failure (ready indices 1, 2, 4),
        ``playlist:4`` finds the index-4 Part, not the fourth pool slot.
        """
        ready = self.ready_parts()
        for part in ready:
            if part.index == ref.index:
                return part
        available = ", ".join(str(part.index) for part in ready)
        msg = f"album {self._id.value!r} has no part {ref.index}; ready: {available}"
        raise ValueError(msg)

    def next_index(self) -> int:
        """Return the 1-based index the next recorded Part will take."""
        return 1 + max((entry.index for entry in self._parts), default=0)

    def with_part(self, entry: PartEntry) -> AlbumManifest:
        """Return a successor manifest with ``entry`` appended (re-sorted)."""
        return AlbumManifest(
            album_id=self._id,
            fmt=self._format,
            tags=self._tags,
            created=self._created,
            fingerprint=self._fingerprint,
            parts=(*self._parts, entry),
        )

    def to_json(self) -> str:
        """Return the pretty-printed JSON serialization."""
        record = {
            "id": self._id.value,
            "format": self._format.value,
            "tags": self._tags.to_dict(),
            "created": self._created.isoformat(),
            "prompt_fingerprint": self._fingerprint.value,
            "parts": [entry.to_dict() for entry in self._parts],
        }
        return json.dumps(record, indent=2, ensure_ascii=False)

    @classmethod
    def from_json(cls, text: str) -> Self:
        """Parse a manifest from JSON, raising ``ValueError`` on a bad record."""
        return cls.from_wire(JsonObject.parse(text, "manifest"))

    @classmethod
    def from_wire(cls, obj: JsonObject) -> Self:
        """Build a manifest from a wire object, raising on a malformed record.

        Total (PY-EH-8): it requires ``id``/``tags``/``created``/
        ``prompt_fingerprint`` and raises on a missing or malformed field. The
        scan boundary peeks ``opt_str("id")`` to skip idless legacy dirs *before*
        this full parse, so an idless record never reaches here.
        """
        return cls(
            album_id=AlbumId(obj.require_str("id")),
            fmt=Format(obj.require_str("format")),
            tags=AlbumTags.from_wire(obj.require_object("tags")),
            created=datetime.fromisoformat(obj.require_str("created")),
            fingerprint=PromptFingerprint(obj.require_str("prompt_fingerprint")),
            parts=tuple(
                PartEntry.from_wire(JsonObject.coerce(item, "manifest.parts"))
                for item in obj.require_list("parts")
            ),
        )

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, AlbumManifest):
            return NotImplemented
        return self._identity() == other._identity()

    def __hash__(self) -> int:
        return hash((AlbumManifest, *self._identity()))

    def __repr__(self) -> str:
        return f"AlbumManifest(id={self._id!s}, parts={len(self._parts)})"

    def _identity(
        self,
    ) -> tuple[
        AlbumId, Format, AlbumTags, datetime, PromptFingerprint, tuple[PartEntry, ...]
    ]:
        return (
            self._id,
            self._format,
            self._tags,
            self._created,
            self._fingerprint,
            self._parts,
        )


@final
@dataclass(frozen=True, slots=True)
class ManifestDraft:
    """A pre-``created`` album record: the store stamps the clock and persists it.

    The caller hands the store an id, tags, the authoring fingerprint, and any
    seed parts; the store owns the clock, stamping ``created = now(UTC)`` when it
    materialises the manifest. Keeping ``created``
    off the draft means no caller can forge a creation time.
    """

    album_id: AlbumId
    tags: AlbumTags
    fingerprint: PromptFingerprint
    fmt: Format = Format.PLAYLIST
    parts: tuple[PartEntry, ...] = field(default_factory=tuple)

    @property
    def locator(self) -> str:
        """Return the ``<slug>-<id>`` directory name the store materialises into."""
        return f"{self.tags.slug()}-{self.album_id.value}"

    def stamped(self, created: datetime) -> AlbumManifest:
        """Return the manifest for this draft stamped with ``created``.

        The store is the sole clock owner: it calls this with
        ``datetime.now(UTC)`` at materialisation, so the draft stays a pure value
        object no caller can use to forge a creation time.
        """
        return AlbumManifest(
            album_id=self.album_id,
            fmt=self.fmt,
            tags=self.tags,
            created=created,
            fingerprint=self.fingerprint,
            parts=self.parts,
        )
