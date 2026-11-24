"""Render a list of speech segments against a shared synthesis default.

The multi-voice ``unmute``/``rec new`` tools accept a ``segments`` list where each
segment may override ``voice``, ``language``, and ``vibe_tags`` on top of the
call-level :class:`~punt_vox.types_synthesis.SynthesisSpec`.  :class:`SegmentBatch`
validates the whole batch before the tools hand off a synth/record ``handler``.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import replace
from typing import Self, final

from websockets.exceptions import WebSocketException

from punt_vox.client_errors import VoxdConnectionError, VoxdProtocolError
from punt_vox.types_synthesis import SynthesisSpec

logger = logging.getLogger(__name__)

SegmentHandler = Callable[[str, SynthesisSpec], dict[str, object]]
_SEGMENT_KEYS = frozenset({"text", "voice", "language", "vibe_tags"})


@final
class SegmentBatch:
    """Validate a batch and synthesize every segment against shared defaults."""

    __slots__ = ("_segments",)

    _segments: list[dict[str, str]]

    def __new__(cls, segments: list[dict[str, str]]) -> Self:
        """Reject malformed input before the caller can start synthesis."""
        if not segments:
            msg = "segments must contain at least one segment."
            raise ValueError(msg)
        self = super().__new__(cls)
        self._segments = [
            cls._validated_segment(index, segment)
            for index, segment in enumerate(segments)
        ]
        return self

    @staticmethod
    def text_segment(text: str) -> dict[str, str]:
        """Wrap plain text, naming the caller's argument if it is blank."""
        if not text.strip():
            msg = "text must be a non-blank string."
            raise ValueError(msg)
        return {"text": text}

    @staticmethod
    def _validated_segment(index: int, segment: dict[str, str]) -> dict[str, str]:
        extra = segment.keys() - _SEGMENT_KEYS
        if extra:
            offending = ", ".join(repr(key) for key in sorted(extra))
            allowed = ", ".join(sorted(_SEGMENT_KEYS))
            msg = (
                f"segments[{index}] contains unknown key(s): {offending}. "
                f"Allowed keys: {allowed}."
            )
            raise ValueError(msg)
        if not segment.get("text", "").strip():
            msg = f"segments[{index}].text must be a non-blank string."
            raise ValueError(msg)
        return segment.copy()

    def render(
        self, defaults: SynthesisSpec, *, handler: SegmentHandler, error_label: str
    ) -> str:
        """Return a JSON list of per-segment results, or a JSON error string.

        Each segment is synthesized via *handler* against its
        per-segment spec; a daemon failure short-circuits to an error string,
        logged under *error_label*.
        """
        results: list[dict[str, object]] = []
        try:
            results.extend(
                handler(seg["text"], self._spec_for(defaults, seg))
                for seg in self._segments
            )
        except VoxdConnectionError as exc:
            return self._error(str(exc), results)
        except (VoxdProtocolError, WebSocketException, OSError, ValueError) as exc:
            logger.exception("%s failed", error_label)
            return self._error(str(exc), results)
        return json.dumps(results)

    @staticmethod
    def _spec_for(defaults: SynthesisSpec, seg: dict[str, str]) -> SynthesisSpec:
        """Return the per-segment spec: segment overrides win over the defaults."""
        return replace(
            defaults,
            voice=seg.get("voice") or defaults.voice,
            language=seg.get("language") or defaults.language,
            vibe_tags=seg.get("vibe_tags") or defaults.vibe_tags,
        )

    @staticmethod
    def _error(message: str, results: list[dict[str, object]]) -> str:
        """Return the ``{"error": ...}`` envelope, carrying any landed results.

        A batch that fails partway still recorded the earlier segments; the
        landed ids ride beside the error so they stay addressable rather than
        orphaned and invisible. A total failure omits the empty ``results`` key.
        """
        envelope: dict[str, object] = {"error": message}
        if results:
            envelope["results"] = results
        return json.dumps(envelope)
