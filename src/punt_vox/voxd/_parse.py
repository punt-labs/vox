"""Parse helpers for extracting optional typed values from WebSocket messages."""

from __future__ import annotations

__all__ = [
    "parse_optional_float",
    "parse_optional_int",
    "parse_optional_str",
]


def parse_optional_float(msg: dict[str, object], key: str) -> float | None:
    """Extract an optional float field from a message dict."""
    raw = msg.get(key)
    if raw is None:
        return None
    return float(str(raw))


def parse_optional_int(msg: dict[str, object], key: str) -> int | None:
    """Extract an optional int field from a message dict."""
    raw = msg.get(key)
    if raw is None:
        return None
    return int(str(raw))


def parse_optional_str(msg: dict[str, object], key: str) -> str | None:
    """Extract an optional string field, returning None for empty strings."""
    raw = str(msg.get(key, ""))
    return raw or None
