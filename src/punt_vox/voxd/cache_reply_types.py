"""Success reply contracts for cache administration."""

from __future__ import annotations

from typing import Literal, TypedDict


class CacheStatusReply(TypedDict):
    """Cache entry count, size, and relative path."""

    type: Literal["cache_status"]
    entries: int
    size_bytes: int
    path: str


class CacheClearedReply(TypedDict):
    """Number of cache files removed."""

    type: Literal["cache_cleared"]
    cleared: int


type CacheReply = CacheStatusReply | CacheClearedReply
