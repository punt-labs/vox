"""The queryable album tags, the tag query, and the prompt fingerprint.

An album's identity is an :class:`AlbumId` plus a set of *tags*: ``style`` and
``vibe`` are the non-unique filter axes, ``name`` is the enforced-unique handle
(R5). :class:`TagQuery` owns the ``matches`` predicate, replacing the repeated
``(str | None, str | None)`` tuple across the catalog queries (finding #7).
:class:`PromptFingerprint` is a stable hash of the authored prompt-set -- hidden
album metadata that pins one pool to one coherent prompt-set (vox-1uo5).
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Container, Sequence
from dataclasses import dataclass
from typing import ClassVar, Final, Self, final

from punt_vox.voxd.programs.hex_token import HexToken
from punt_vox.voxd.programs.wire import JsonObject

__all__ = ["AlbumTags", "PromptFingerprint", "TagQuery"]

_UNSAFE: Final = re.compile(r"[^a-z0-9]+")
_FINGERPRINT_CHARS: Final = 16  # 64-bit truncation of the sha256 hex digest


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
    name: str | None = None  # None means an unnamed, tag-addressed album (R5)

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

    @classmethod
    def mint_unique_name(cls, desired: str, taken: Container[str]) -> str:
        """Return ``desired`` if free, else auto-suffix ``X1``/``X2``/... (R5).

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
    ``id`` axis is *not* here (F#7): an id is served by ``Catalog.by_id``, a
    direct lookup, never routed through a tag filter.
    """

    style: str | None = None  # None wildcards the style axis
    vibe: str | None = None  # None wildcards the vibe axis
    name: str | None = None  # None wildcards the name axis

    def matches(self, tags: AlbumTags) -> bool:
        """Return whether ``tags`` satisfies every present (non-wildcard) axis."""
        return (
            (self.style is None or self.style == tags.style)
            and (self.vibe is None or self.vibe == tags.vibe)
            and (self.name is None or self.name == tags.name)
        )


@final
class PromptFingerprint(HexToken):
    """A stable hash of the authored prompt-set (base + variations, vox-1uo5).

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
