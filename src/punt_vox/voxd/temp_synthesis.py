"""One synthesis request run into a temp file that no failure leaves behind."""

from __future__ import annotations

import asyncio
import logging
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Self, final

from punt_vox.core import TTSClient
from punt_vox.log_sanitize import SANITIZER

if TYPE_CHECKING:
    from collections.abc import Callable

    from punt_vox.types import AudioRequest, AudioResult, TTSProvider
    from punt_vox.types_synthesis import SynthesisSpec

__all__ = ["TempFileSynthesis"]

logger = logging.getLogger(__name__)


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
            work.add_done_callback(self._discard_when_done(output_path))
            raise
        except BaseException:
            output_path.unlink(missing_ok=True)
            raise
        return output_path

    @staticmethod
    def _discard_when_done(
        output_path: Path,
    ) -> Callable[[asyncio.Future[AudioResult]], None]:
        """Return a done-callback that removes *output_path* after its writer.

        The callback also retrieves the abandoned thread's exception, so a
        provider failure after the cancel is not reported as never retrieved.
        It logs the type and message, never the repr: an encoding error's repr
        quotes the value it could not encode, which can be the API key. The
        message is escaped onto one line: it can carry caller text, such as a
        voice name, and a raw newline there would forge a log record.
        """

        def _discard(done: asyncio.Future[AudioResult]) -> None:
            if not done.cancelled() and (error := done.exception()) is not None:
                logger.debug(
                    "cancelled synthesis then failed: %s: %s",
                    type(error).__name__,
                    SANITIZER.escape(str(error)),
                )
            output_path.unlink(missing_ok=True)

        return _discard

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
        msg = (
            f"synthesis produced missing or empty output file: {output_path} "
            f"(provider={self._spec.provider or ''}, voice={self._spec.voice or ''}, "
            f"size={synth_size}, chars_in={self._chars_in})"
        )
        raise RuntimeError(msg)
