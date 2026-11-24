"""Assemble one voice call from a call_start request and run it as a task."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Self, final

from punt_vox.voxd.conversation_mode.input_device import PortAudio
from punt_vox.voxd.conversation_mode.mic_audio_source import MicAudioSource
from punt_vox.voxd.log_text import LogText
from punt_vox.voxd.voice_call.active_call import ActiveCall
from punt_vox.voxd.voice_call.boss_tools import BossTools
from punt_vox.voxd.voice_call.call import VoiceAgentCall
from punt_vox.voxd.voice_call.call_log import CallLog
from punt_vox.voxd.voice_call.call_trace import CallTrace
from punt_vox.voxd.voice_call.cursor_commit import CursorCommit
from punt_vox.voxd.voice_call.inputs import ScriptInput
from punt_vox.voxd.voice_call.mic_input import MicInput
from punt_vox.voxd.voice_call.return_brief import ReturnBrief
from punt_vox.voxd.voice_call.speaker import AgentSpeaker

if TYPE_CHECKING:
    from collections.abc import Callable
    from contextlib import AbstractContextManager
    from pathlib import Path
    from uuid import UUID

    from punt_vox.voxd.playback import PlaybackQueue
    from punt_vox.voxd.voice_agent.contracts import SessionOptions
    from punt_vox.voxd.voice_call.agent_operations import AgentOperations
    from punt_vox.voxd.voice_call.call import Opener
    from punt_vox.voxd.voice_call.contracts import (
        CallInput,
        ProjectAllowlist,
        WorkerControl,
    )
    from punt_vox.voxd.voice_call.progress import ProgressEvents

__all__ = ["CallLauncher"]

logger = logging.getLogger(__name__)


@final
class CallLauncher:
    """Build a call's input, tools, speaker and trace, then start its task."""

    __slots__ = (
        "_hold_music",
        "_opener",
        "_operations",
        "_playback",
        "_progress",
        "_projects",
        "_trace_dir",
        "_workers",
    )
    _workers: WorkerControl
    _projects: ProjectAllowlist
    _playback: PlaybackQueue
    _operations: AgentOperations
    _progress: ProgressEvents
    _hold_music: Callable[[], AbstractContextManager[None]]
    _trace_dir: Path
    _opener: Opener

    def __new__(
        cls,
        workers: WorkerControl,
        projects: ProjectAllowlist,
        playback: PlaybackQueue,
        operations: AgentOperations,
        *,
        progress: ProgressEvents,
        hold_music: Callable[[], AbstractContextManager[None]],
        trace_dir: Path,
        opener: Opener,
    ) -> Self:
        """Bind what every call this daemon starts has in common."""
        self = super().__new__(cls)
        self._workers, self._projects, self._playback = workers, projects, playback
        self._operations, self._progress = operations, progress
        self._hold_music, self._trace_dir, self._opener = hold_music, trace_dir, opener
        return self

    async def launch(self, call_id: UUID, msg: dict[str, object]) -> ActiveCall:
        """Start the call ``msg`` asks for and return its handle.

        Raises:
            ValueError: When the script or echo declaration is unusable, or
                the call ID was already used.
            OSError: When the trace directory cannot be prepared.
            ConvAIError: When the agent cannot be provisioned.
        """
        audio = await self._input(msg)
        with CallLog.starting(msg):  # A start line always gets an end line.
            await self._operations.prepare()
            await asyncio.to_thread(CursorCommit.prepare, self._trace_dir)
            trace = CallTrace.reserve(self._trace_dir, call_id)
            try:
                audio.open()  # A mic that cannot open refuses the call, undialled.
            except OSError:
                trace.release()  # The ID was never used: a retry may reuse it.
                raise
            returning = ReturnBrief(self._trace_dir / "departure.json")
            call = VoiceAgentCall(
                BossTools(
                    self._workers, self._projects, spoken_labels=returning.labels
                ),
                audio,
                AgentSpeaker(self._playback),
                trace=trace.events,
                opener=self._opener,
                progress=self._progress,
            )

        async def run() -> None:
            with CallLog(trace.path, call.outcome):
                try:
                    with self._hold_music():
                        await call.run(
                            lambda: self._dial(returning),
                            returning.covered,
                            depart=lambda reported: self._depart(returning, reported),
                        )
                except Exception as exc:
                    # Outside call.run nothing else reports a failure; a caller
                    # waiting to connect would otherwise time out on a false message.
                    call.mark_failed(LogText.failure(exc))
                    raise
                finally:
                    audio.close()

        task = asyncio.create_task(run())
        task.add_done_callback(lambda _: trace.stream.close())
        return ActiveCall(call_id, call, task, trace.path)

    async def teardown(self) -> None:
        """Delete the agent registration once outstanding operations settle."""
        await self._operations.teardown()

    @staticmethod
    async def _input(msg: dict[str, object]) -> CallInput:
        if "script" in msg:
            return await asyncio.to_thread(ScriptInput.from_wire, msg["script"])
        return MicInput.from_wire(msg, MicAudioSource(PortAudio()))

    async def _dial(self, returning: ReturnBrief) -> tuple[str, SessionOptions]:
        url, tool_ids = await self._operations.connection(BossTools.CATALOG)
        repos, workers = self._projects.repos(), await self._workers.workers()
        return url, await asyncio.to_thread(returning.options, workers, repos, tool_ids)

    @staticmethod
    async def _depart(returning: ReturnBrief, reported: dict[str, int]) -> None:
        try:
            await asyncio.to_thread(returning.depart, reported)
        except (OSError, RuntimeError, ValueError) as exc:
            logger.warning("Call departure not saved (%s)", LogText.failure(exc))
