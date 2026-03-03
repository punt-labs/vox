"""Output path helpers for TTS generation."""

from __future__ import annotations

import os
from pathlib import Path

from punt_vox.types import SynthesisRequest, generate_filename


def default_output_dir() -> Path:
    """Resolve the default output directory from environment or fallback.

    Resolution order: ``VOX_OUTPUT_DIR`` env var → ``~/vox-output``.
    """
    env_dir = os.environ.get("VOX_OUTPUT_DIR")
    if env_dir:
        return Path(env_dir)
    return Path.home() / "vox-output"


def resolve_output_path(request: SynthesisRequest) -> Path:
    """Resolve output path for a synthesis request."""
    metadata = request.metadata
    output_path = metadata.get("output_path")
    if output_path:
        path = Path(output_path)
    else:
        output_dir_raw = metadata.get("output_dir")
        output_dir = Path(output_dir_raw) if output_dir_raw else default_output_dir()
        filename = metadata.get("filename") or generate_filename(request.text)
        path = output_dir / filename

    path.parent.mkdir(parents=True, exist_ok=True)
    return path
