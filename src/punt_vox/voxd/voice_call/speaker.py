"""Play the agent's voice through the daemon's playback stream, cut on barge-in."""

from __future__ import annotations

from contextlib import AsyncExitStack
from typing import TYPE_CHECKING, Self, final

if TYPE_CHECKING:
    from punt_vox.voxd.pcm_stream import PcmStreamSink
    from punt_vox.voxd.playback import PlaybackQueue
    from punt_vox.voxd.voice_agent.events import Audio

__all__ = ["AgentSpeaker"]


@final
class AgentSpeaker:
    """Hold one PCM stream per stretch of agent speech.

    The stream holds the playback mutex, so chimes and music wait while the
    agent talks. An interruption flushes queued audio at once and drops any
    late audio from the response it cut; the next response opens a new stream.
    """

    __slots__ = ("_cut", "_playback", "_sink", "_stack")
    _playback: PlaybackQueue
    _stack: AsyncExitStack
    _sink: PcmStreamSink | None
    _cut: int  # The newest interrupted response; its audio is never played.

    def __new__(cls, playback: PlaybackQueue) -> Self:
        """Bind the daemon's playback queue."""
        self = super().__new__(cls)
        self._playback = playback
        self._stack = AsyncExitStack()
        self._sink = None
        self._cut = -1
        return self

    async def play(self, audio: Audio) -> None:
        """Queue one chunk, opening the stream for a new response."""
        if audio.event_id <= self._cut:
            return
        if self._sink is None:
            self._sink = await self._stack.enter_async_context(
                self._playback.stream_pcm()
            )
        self._sink.feed(audio.pcm)

    async def interrupt(self, event_id: int) -> None:
        """Discard queued audio of the response the person talked over."""
        self._cut = max(self._cut, event_id)
        if self._sink is not None:
            self._sink.flush()
        await self.close()

    async def close(self) -> None:
        """Release the stream and the playback mutex."""
        self._sink = None
        stack, self._stack = self._stack, AsyncExitStack()
        await stack.aclose()
