"""Write a provider's audio stream to a file: all of it, or nothing.

An SDK's ``stream()`` is a lazy generator: the request is not even sent until
the first chunk is pulled, and the connection can fail on any chunk after that.
Writing straight to the target would leave a truncated file behind on such a
failure -- one the cache or the playlist would then serve as if it were whole.
:class:`StreamFile` drains the stream into a temp file beside the target and
renames it into place only once the last chunk has arrived.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Self, final

if TYPE_CHECKING:
    from collections.abc import Iterable

__all__ = ["StreamFile"]


@final
class StreamFile:
    """The target path for one provider stream, written atomically."""

    __slots__ = ("_target",)
    _target: Path

    def __new__(cls, target: Path) -> Self:
        self = super().__new__(cls)
        self._target = target
        return self

    def write(self, chunks: Iterable[bytes]) -> int:
        """Drain *chunks* into the target and return the number of bytes written.

        Whatever *chunks* raises propagates unchanged, with no file left at the
        target and no temp file left beside it.

        Raises:
            RuntimeError: The stream ended without yielding any audio.
        """
        self._target.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(dir=self._target.parent, suffix=".tmp")
        tmp = Path(tmp_name)
        try:
            with os.fdopen(fd, "wb") as f:
                written = sum(f.write(chunk) for chunk in chunks)
            if written == 0:
                msg = "provider stream returned no audio data"
                raise RuntimeError(msg)
            tmp.replace(self._target)
        except BaseException:
            # BaseException: a cancelled worker or Ctrl-C must not strand a temp file.
            tmp.unlink(missing_ok=True)
            raise
        return written
