"""One synthesis request run into a temp file that no failure leaves behind."""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Self, final

from punt_vox.core import TTSClient
from punt_vox.log_sanitize import SANITIZER
from punt_vox.voxd.output_discard import OutputDiscard

if TYPE_CHECKING:
    from punt_vox.types import AudioRequest, TTSProvider
    from punt_vox.types_synthesis import SynthesisSpec

__all__ = ["TempFileSynthesis"]


@final
class TempFileSynthesis:
    """Synthesize one request into a new temp file, removing it on any failure.

    The file is created before the provider runs, so any failure -- the
    provider raising, or the output coming back empty -- removes it before the
    error propagates; otherwise every failed request strands a file in the temp
    directory. A cancelled request removes it too, but only once the worker
    thread is done: the thread cannot be stopped, and it may write the file
    after the cancel lands.
    """

    __slots__ = ("_chars_in", "_client", "_request", "_spec")
    _client: TTSClient
    _request: AudioRequest
    _spec: SynthesisSpec
    # The caller's text length, before normalization: what an operator sent.
    _chars_in: int

    def __new__(
        cls,
        provider: TTSProvider,
        request: AudioRequest,
        spec: SynthesisSpec,
        chars_in: int,
    ) -> Self:
        self = super().__new__(cls)
        self._client = TTSClient(provider)
        self._request = request
        self._spec = spec
        self._chars_in = chars_in
        return self

    async def run(self) -> Path:
        """Synthesize the request and return the path of the non-empty output."""
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp:
            output_path = Path(tmp.name)
        work = asyncio.ensure_future(
            asyncio.to_thread(self._client.synthesize, self._request, output_path)
        )
        try:
            # Shielded, so a cancel reaches this await and not the thread's
            # future, which then still completes when the thread does.
            await asyncio.shield(work)
            self._require_nonempty(output_path)
        except asyncio.CancelledError:
            work.add_done_callback(OutputDiscard(output_path))
            raise
        except BaseException:
            output_path.unlink(missing_ok=True)
            raise
        return output_path

    def _require_nonempty(self, output_path: Path) -> None:
        """Reject a missing or empty synthesis output; caching it would poison
        every identical request, so it fails fast. The caller removes the file
        and logs the exception (traceback + fields) -- no separate log here.
        """
        try:
            synth_size = output_path.stat().st_size
        except OSError:
            synth_size = -1
        if synth_size > 0:
            return
        # Provider and voice are caller text, and the daemon logs this message;
        # escaped, a newline in either cannot start a forged record.
        provider = SANITIZER.escape(self._spec.provider or "")
        voice = SANITIZER.escape(self._spec.voice or "")
        msg = (
            f"synthesis produced missing or empty output file: {output_path} "
            f"(provider={provider}, voice={voice}, "
            f"size={synth_size}, chars_in={self._chars_in})"
        )
        raise RuntimeError(msg)
