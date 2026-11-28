"""Play the agent's voice through the daemon's playback stream, cut on barge-in."""

from __future__ import annotations

from contextlib import AsyncExitStack, suppress
from typing import TYPE_CHECKING, Self, final

from punt_vox.voxd.pcm_output import OutputDeviceLostError
from punt_vox.voxd.voice_call.utterance_breaks import UtteranceBreaks

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

    __slots__ = (
        "_breaks",
        "_cut",
        "_held",
        "_notes",
        "_owns",
        "_playback",
        "_reply",
        "_sink",
    )
    _playback: PlaybackQueue
    _held: AsyncExitStack  # The playback mutex, from the first reply to hang-up.
    _owns: bool  # Whether ``_held`` holds the mutex now.
    _reply: AsyncExitStack  # The current reply's sink.
    _sink: PcmStreamSink | None  # None between replies and after a cut.
    _cut: int  # The newest interrupted response; its audio is never played.
    _breaks: UtteranceBreaks
    _notes: list[str]  # Non-fatal device degradations, one per affected reply.

    def __new__(cls, playback: PlaybackQueue) -> Self:
        """Bind the daemon's playback queue."""
        self = super().__new__(cls)
        self._playback = playback
        self._held, self._reply = AsyncExitStack(), AsyncExitStack()
        self._sink, self._owns = None, False
        self._cut = -1
        self._breaks = UtteranceBreaks()
        self._notes = []
        return self

    @property
    def notes(self) -> tuple[str, ...]:
        """Return what degraded the output without failing the call."""
        return tuple(self._notes)

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
        self._sink.feed(audio.pcm, starts_utterance=self._breaks.begins(audio.event_id))

    def note_tool_call(self) -> None:
        """Note a tool call: the agent paused, so its next audio is new."""
        self._breaks.note_tool_call()

    async def interrupt(self, event_id: int) -> None:
        """Discard queued audio of the response the person talked over."""
        self._cut = max(self._cut, event_id)
        if self._sink is not None:
            self._sink.flush()
        await self._end_reply()

    # Optional by contract: None means the reply played out, not a missing value.
    async def finish(self) -> str | None:
        """Let audio already received play out, then release the output.

        Returns why the last reply did not play out (the device stopped before
        it did), or None when it did. The output is released either way.
        """
        failure: str | None = None
        try:
            if self._sink is not None:
                self._sink.finish()
                with suppress(OutputDeviceLostError):
                    await self._sink.wait()
        finally:
            try:
                await self.close(is_teardown=False)
            except OutputDeviceLostError as exc:
                failure = str(exc)
        return failure

    async def close(self, *, is_teardown: bool = True) -> None:
        """Release the reply's sink and the playback mutex.

        A teardown ignores a device that was already lost: the call is ending
        anyway. :meth:`finish` is not one, and lets the loss through.
        """
        try:
            await self._end_reply()
        except OutputDeviceLostError:
            if not is_teardown:
                raise
        finally:
            held, self._held = self._held, AsyncExitStack()
            self._owns = False
            await held.aclose()

    async def _end_reply(self) -> None:
        sink, self._sink = self._sink, None
        self._breaks.reset()
        reply, self._reply = self._reply, AsyncExitStack()
        try:
            await reply.aclose()
        finally:
            if sink is not None and sink.note is not None:
                self._notes.append(sink.note)
