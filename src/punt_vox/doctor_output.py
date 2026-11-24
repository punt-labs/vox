"""Doctor sub-checks for the music dir and the ``output`` data root.

Saved music albums are what vox writes under the output root (recordings go to
the daemon's state store and synthesis uses temp files), so a fresh install has
neither directory until ``vox music on`` saves its first album. Both rows are
relative verdicts: an absolute path never crosses to a client.
"""

from __future__ import annotations

import tempfile
from typing import Self, final

from punt_vox.dirs import (
    _resolve_music_dir,  # pyright: ignore[reportPrivateUsage]
    default_output_dir,
)
from punt_vox.doctor_result import CheckResult

__all__ = ["OutputDirCheck"]

_CREATED_BY = "created when 'vox music on' saves its first album"


@final
class OutputDirCheck:
    """Verdicts for the music dir and the output root it normally contains.

    Stateless, like :class:`~punt_vox.doctor_guide.GuideCheck`: both paths are
    resolved afresh on every call.
    """

    __slots__ = ()

    def __new__(cls) -> Self:
        """Create an output dir check."""
        return super().__new__(cls)

    def music_dir(self) -> list[CheckResult]:
        """Return the music-dir row: a warning when it is absent, else no row.

        The row stands even when this shell sets ``VOX_OUTPUT_DIR``: the
        installed voxd unit does not carry that variable, so the daemon still
        saves albums under the music dir.
        """
        music_dir = _resolve_music_dir()  # pyright: ignore[reportPrivateUsage]
        if not music_dir.is_dir():
            return [CheckResult.warn(f"output music dir: absent — {_CREATED_BY}")]
        return []

    def output_dir(self) -> list[CheckResult]:
        """Return the output-root row: writable, not writable, or absent.

        The dir *is* the ``output`` data root, so its own label is the name; the
        raw ``OSError`` stays out of the client-facing reply.
        """
        out_dir = default_output_dir()
        if not out_dir.is_dir():
            return [CheckResult.warn(f"output: absent — {_CREATED_BY}")]
        try:
            with tempfile.TemporaryFile(buffering=0, dir=out_dir) as probe:
                probe.write(b"ok")
        except OSError:
            return [CheckResult.fail("output: not writable — check permissions")]
        return [CheckResult.ok("output: writable")]
