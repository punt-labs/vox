"""Assemble one voice call from a call_start request and run it as a task."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Final, Self, final

from punt_vox.voxd.conversation_mode.input_device import PortAudio
from punt_vox.voxd.conversation_mode.mic_audio_source import MicAudioSource
from punt_vox.voxd.voice_call.active_call import ActiveCall, CallTrace
from punt_vox.voxd.voice_call.boss_tools import BossTools
from punt_vox.voxd.voice_call.call import VoiceAgentCall
from punt_vox.voxd.voice_call.call_log import CallLog
from punt_vox.voxd.voice_call.cursor_commit import CursorCommit
from punt_vox.voxd.voice_call.inputs import MicInput, ScriptInput
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

_ECHO_CONTROL: Final = ("headset", "aec")  # The declarations a live call accepts.


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
    _opener: Opener | None  # None: the ElevenLabs conversation opener.

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
        opener: Opener | None,
    ) -> Self:
        """Bind what every call this daemon starts has in common."""
        self = super().__new__(cls)
        self._workers, self._projects, self._playback = workers, projects, playback
        self._operations, self._progress = operations, progress
        self._hold_music, self._trace_dir = hold_music, trace_dir
        self._opener = opener
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
        CallLog.starting(msg)
        await self._operations.prepare()
        await asyncio.to_thread(CursorCommit.prepare, self._trace_dir)
        trace = CallTrace.reserve(self._trace_dir, call_id)
        returning = ReturnBrief(self._trace_dir / "departure.json")
        call = VoiceAgentCall(
            BossTools(self._workers, self._projects, spoken_labels=returning.labels),
            audio,
            AgentSpeaker(self._playback),
            trace=trace.events,
            opener=self._opener,
            progress=self._progress,
        )

        async def run() -> None:
            with CallLog(trace.path, call.outcome), self._hold_music():
                await call.run(
                    lambda: self._dial(returning),
                    returning.covered,
                    depart=lambda reported: self._depart(returning, reported),
                )

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
        if msg.get("echo_control") not in _ECHO_CONTROL:
            raise ValueError(
                "Live calls require echo protection: wear a headset and use "
                "'vox call --headset', or configure system echo cancellation "
                "and use 'vox call --aec'"
            )
        return MicInput(MicAudioSource(PortAudio()))

    async def _dial(self, returning: ReturnBrief) -> tuple[str, SessionOptions]:
        url, tool_ids = await self._operations.connection(BossTools.CATALOG)
        workers = await self._workers.workers()
        options = await asyncio.to_thread(
            returning.options, workers, self._projects.repos(), tool_ids
        )
        return url, options

    @staticmethod
    async def _depart(returning: ReturnBrief, reported: dict[str, int]) -> None:
        try:
            await asyncio.to_thread(returning.depart, reported)
        except (OSError, RuntimeError, ValueError) as exc:
            logger.warning("Call departure not saved (%s)", type(exc).__name__)
