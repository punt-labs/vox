"""Shared chunked synthesis: split text, synthesize chunks, stitch."""

from __future__ import annotations

import logging
import tempfile
from collections.abc import Callable
from pathlib import Path

from punt_vox.core import split_text, stitch_audio

__all__ = ["chunked_synthesize"]

logger = logging.getLogger(__name__)


def chunked_synthesize(
    text: str,
    char_limit: int,
    synthesize_chunk: Callable[[str, Path], None],
    output_path: Path,
    pause_ms: int = 0,
) -> None:
    """Split text into chunks, synthesize each, then stitch into one file."""
    chunks = split_text(text, char_limit)
    logger.debug("Chunked %d chars into %d parts", len(text), len(chunks))

    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        paths: list[Path] = []
        for i, chunk in enumerate(chunks):
            chunk_path = tmp_dir / f"chunk_{i:04d}.mp3"
            synthesize_chunk(chunk, chunk_path)
            paths.append(chunk_path)
        stitch_audio(paths, output_path, pause_ms=pause_ms)
