"""The queryable album tags, the tag query, and the prompt fingerprint.

An album's identity is an :class:`AlbumId` plus a set of *tags*: ``style`` and
``vibe`` are the non-unique filter axes, ``name`` is the enforced-unique handle.
:class:`TagQuery` owns the ``matches`` predicate, replacing the repeated
``(str | None, str | None)`` tuple across the catalog queries.
:class:`PromptFingerprint` is a stable hash of the authored prompt-set -- hidden
album metadata that pins one pool to one coherent prompt-set, so a differing
prompt-set never resumes and grows a foreign pool.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Container, Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from typing import ClassVar, Final, Self, final

from punt_vox.types_programs.wire import JsonObject
from punt_vox.voxd.programs.hex_token import HexToken
from punt_vox.voxd.programs.vibe_label import VibeLabel

__all__ = ["AlbumTags", "PromptFingerprint", "TagQuery"]

_UNSAFE: Final = re.compile(r"[^a-z0-9]+")
_FINGERPRINT_CHARS: Final = 16  # 64-bit truncation of the sha256 hex digest
_NAME_SEGMENT_CHARS: Final = 32  # per-segment cap keeping an auto-name a short handle


@final
@dataclass(frozen=True, slots=True)
class AlbumTags:
    """One album's queryable tags: ``style``, ``vibe``, and the optional ``name``.

    ``style`` and ``vibe`` are the freely non-unique filter axes; ``name`` is the
    unique curated handle (``None`` for a tag-addressed album). ``slug`` derives
    the cosmetic ``<style>--<vibe>`` (or curated-``name``) Finder prefix; the
    directory's short id guarantees uniqueness, so a slug collision is harmless.
    """

    style: str
    vibe: str
    name: str | None = None  # None means an unnamed, tag-addressed album

    def __post_init__(self) -> None:
        """Bound the raw session mood to a short, tag-safe vibe before storing it.

        The auto-vibe mood is a whole session narrative; the persisted tag (and
        any ID3 frame derived from it) must be a short label, not prose. This is
        the one write-path seam that enforces that -- ``VibeLabel`` is idempotent,
        so a re-stored value stays stable.
        """
        object.__setattr__(self, "vibe", VibeLabel(self.vibe).value)

    def with_auto_name(self, created: datetime) -> AlbumTags:
        """Return a copy carrying a guaranteed name, minting one when unnamed.

        A curated ``name`` is kept verbatim; an unnamed pool gets a slug-safe
        ``{vibe}-{style}-{YYYYMMDD-HHMM}`` handle so a generated pool is never
        persisted nameless. ``created`` is passed in (the store's clock), so the
        name is deterministic under a fixed clock. An empty or style-equal vibe
        segment collapses out, yielding the bare ``{style}-{stamp}`` form.
        """
        if self.name is not None:
            return self
        segments = self._dedupe(
            VibeLabel(self.vibe).name_segment(_NAME_SEGMENT_CHARS),
            VibeLabel(self.style).name_segment(_NAME_SEGMENT_CHARS),
        )
        stamp = created.strftime("%Y%m%d-%H%M")
        return replace(self, name="-".join((*segments, stamp)))

    @staticmethod
    def _dedupe(*segments: str) -> tuple[str, ...]:
        """Return the non-empty segments with adjacent duplicates collapsed."""
        kept: list[str] = []
        for segment in segments:
            if segment and (not kept or kept[-1] != segment):
                kept.append(segment)
        return tuple(kept)

    def slug(self) -> str:
        """Return the filesystem-safe Finder prefix (curated name, else tags)."""
        if self.name is not None:
            return self._slugify(self.name)
        return f"{self._slugify(self.style)}--{self._slugify(self.vibe)}"

    def to_dict(self) -> dict[str, object]:
        """Return the wire object ``{style, vibe, name}`` (``name`` may be null)."""
        return {"style": self.style, "vibe": self.vibe, "name": self.name}

    @classmethod
    def from_wire(cls, obj: JsonObject) -> Self:
        """Build tags from a wire object, raising on a missing style or vibe."""
        return cls(
            style=obj.require_str("style"),
            vibe=obj.require_str("vibe"),
            name=obj.opt_str("name"),
        )

    @staticmethod
    def canonical(value: str) -> str:
        """Return the canonical stored form of a tag value (trimmed).

        The one place a raw tag string is normalized before it is minted or
        queried, so the write path (``turn_on``) and the read path
        (``program_select``) agree: ``" trance "`` and ``"trance"`` name the same
        tag. (Case folding is a separate, deferred policy -- this only trims.)
        """
        return value.strip()

    @classmethod
    def mint_unique_name(cls, desired: str, taken: Container[str]) -> str:
        """Return ``desired`` if free, else auto-suffix ``X1``/``X2``/...

        The name-space mint parallel to :meth:`AlbumId.mint`: a colliding desired
        name is disambiguated at creation so ``by_name`` always returns 0 or 1.
        """
        if desired not in taken:
            return desired
        suffix = 1
        while f"{desired}{suffix}" in taken:
            suffix += 1
        return f"{desired}{suffix}"

    @staticmethod
    def _slugify(text: str) -> str:
        """Return ``text`` as a single filesystem-safe lowercase segment."""
        cleaned = _UNSAFE.sub("-", text.strip().lower()).strip("-")
        return cleaned or "album"


@final
@dataclass(frozen=True, slots=True)
class TagQuery:
    """A tag filter over the catalog: any of ``style``/``vibe``/``name`` may wildcard.

    A ``None`` field matches every album on that axis; a present field must equal
    the album's corresponding tag. An all-``None`` query matches everything. The
    ``id`` axis is *not* here: an id is served by ``Catalog.by_id``, a direct
    lookup, never routed through a tag filter.
    """

    style: str | None = None  # None wildcards the style axis
    vibe: str | None = None  # None wildcards the vibe axis
    name: str | None = None  # None wildcards the name axis

    def __post_init__(self) -> None:
        """Bound a present vibe filter to the same label the write path stores.

        The resume path queries on the raw session mood; the pool stores the
        *bounded* vibe. Applying the identical ``VibeLabel`` here keeps the two
        sides in agreement, so a matching mood resumes its pool instead of
        minting a fresh one every session.
        """
        if self.vibe is not None:
            object.__setattr__(self, "vibe", VibeLabel(self.vibe).value)

    @classmethod
    def normalized(
        cls, *, style: str | None, vibe: str | None, name: str | None
    ) -> Self:
        """Build a query with each present tag canonicalized to its stored form.

        Applies the same trim :meth:`AlbumTags.canonical` applies on the write
        path, so ``" trance "`` queried matches ``"trance"`` minted. A tag that is
        empty after trimming collapses to ``None`` (a wildcard), never an
        impossible ``""`` filter.
        """
        return cls(
            style=cls._clean(style),
            vibe=cls._clean(vibe),
            name=cls._clean(name),
        )

    @staticmethod
    def _clean(value: str | None) -> str | None:
        """Return the canonical tag, or ``None`` when absent or blank (a wildcard)."""
        if value is None:
            return None
        return AlbumTags.canonical(value) or None

    def matches(self, tags: AlbumTags) -> bool:
        """Return whether ``tags`` satisfies every present (non-wildcard) axis."""
        return all(
            wanted is None or wanted == actual
            for wanted, actual in (
                (self.style, tags.style),
                (self.vibe, tags.vibe),
                (self.name, tags.name),
            )
        )


@final
class PromptFingerprint(HexToken):
    """A stable hash of the authored prompt-set (base + variations).

    Two albums authored from the same prompt-set share a fingerprint; any change
    to the base or a variation yields a different one. It is hidden album
    metadata -- never a user-facing tag -- so ``catalog.resume``/``newest`` resume
    only a pool whose fingerprint equals the incoming prompt-set's. Validation and
    the value-object dunders come from :class:`HexToken`; this subclass adds only
    the prompt-set hashing factory.
    """

    __slots__ = ()
    _LABEL: ClassVar[str] = "prompt fingerprint"

    @classmethod
    def from_prompts(cls, base: str, variations: Sequence[str]) -> Self:
        """Return the fingerprint of a prompt-set (order-stable canonical hash)."""
        canonical = "\n".join([base, *variations])
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        return cls(digest[:_FINGERPRINT_CHARS])
