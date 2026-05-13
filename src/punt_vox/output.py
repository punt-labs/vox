"""Output path helpers for TTS generation."""

from __future__ import annotations

from pathlib import Path

from punt_vox.dirs import default_output_dir
from punt_vox.types import SynthesisRequest, generate_filename

__all__ = ["OutputResolver", "default_output_dir"]


class OutputResolver:
    """Resolves output file paths for TTS synthesis requests."""

    @staticmethod
    def resolve(request: SynthesisRequest) -> Path:
        """Resolve output path for a synthesis request."""
        metadata = request.metadata
        output_path = metadata.get("output_path")
        if output_path:
            path = Path(output_path)
        else:
            output_dir_raw = metadata.get("output_dir")
            output_dir = (
                Path(output_dir_raw) if output_dir_raw else default_output_dir()
            )
            filename = metadata.get("filename") or generate_filename(request.text)
            path = output_dir / filename

        path.parent.mkdir(parents=True, exist_ok=True)
        return path
