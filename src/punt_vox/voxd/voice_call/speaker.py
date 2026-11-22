"""Play the agent's voice through the daemon's playback stream, cut on barge-in."""

from __future__ import annotations

from contextlib import AsyncExitStack, suppress
from typing import TYPE_CHECKING, Self, final

from punt_vox.voxd.pcm_output import OutputDeviceLostError

if TYPE_CHECKING:
    from punt_vox.voxd.pcm_stream import PcmStreamSink
    from punt_vox.voxd.playback import PlaybackQueue
    from punt_vox.voxd.voice_agent.events import Audio

__all__ = ["AgentSpeaker"]


@final
class AgentSpeaker:
    """Own the daemon's audio output from the agent's first word to hang-up.

    The speaker holds the playback mutex for the whole call, not per reply:
    hook speech, chimes or music played between replies would talk over the
    agent and leak into the open microphone as a false barge-in. Each reply
    gets its own sink; an interruption flushes it at once and drops any late
    audio from the response it cut, and the next response opens a new sink.
    """

    __slots__ = ("_cut", "_held", "_owns", "_playback", "_reply", "_sink")
    _playback: PlaybackQueue
    _held: AsyncExitStack  # The playback mutex, from the first reply to hang-up.
    _owns: bool  # Whether ``_held`` holds the mutex now.
    _reply: AsyncExitStack  # The current reply's sink.
    _sink: PcmStreamSink | None  # None between replies and after a cut.
    _cut: int  # The newest interrupted response; its audio is never played.

    def __new__(cls, playback: PlaybackQueue) -> Self:
        """Bind the daemon's playback queue."""
        self = super().__new__(cls)
        self._playback = playback
        self._held, self._reply = AsyncExitStack(), AsyncExitStack()
        self._sink, self._owns = None, False
        self._cut = -1
        return self

    async def play(self, audio: Audio) -> None:
        """Queue one chunk, opening a sink for a new response."""
        if audio.event_id <= self._cut:
            return
        if self._sink is None:
            if not self._owns:
                await self._held.enter_async_context(self._playback.mutex)
                self._owns = True
            self._sink = await self._reply.enter_async_context(
                self._playback.stream_pcm(locked=True)
            )
        self._sink.feed(audio.pcm)

    async def interrupt(self, event_id: int) -> None:
        """Discard queued audio of the response the person talked over."""
        self._cut = max(self._cut, event_id)
        if self._sink is not None:
            self._sink.flush()
        await self._end_reply()

    async def finish(self) -> None:
        """Let audio already received play out, then release the output."""
        try:
            if self._sink is not None:
                self._sink.finish()
                with suppress(OutputDeviceLostError):
                    await self._sink.wait()
        finally:
            await self.close()

    async def close(self) -> None:
        """Release the reply's sink and the playback mutex."""
        try:
            await self._end_reply()
        finally:
            held, self._held = self._held, AsyncExitStack()
            self._owns = False
            await held.aclose()

    async def _end_reply(self) -> None:
        self._sink = None
        reply, self._reply = self._reply, AsyncExitStack()
        await reply.aclose()
