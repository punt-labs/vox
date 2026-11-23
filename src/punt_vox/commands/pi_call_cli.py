"""``vox call start`` -- a live voice call with a persistent, read-only pi call agent.

Kept reachable unchanged beside bare ``vox call`` (voxd's Mode B call) until
Mode B retires it.

Wires :class:`~punt_vox.voxd.conversation_mode.call_session.CallSession` (the
tested orchestration in ``voxd/conversation_mode/``) to the CLI. The call's
agent is one ``pi --mode rpc`` process spawned when the call starts and held
for its whole length, opened with a context snapshot the user's main session
wrote (``--context``); :class:`~.call_runner.CallRunner` runs that sequence and
is imported lazily at ``vox call start`` so CLI and hook startup skip the
live-call stack.
The pi call's CLI concerns: the UserPromptSubmit lock
(:class:`~.call_lock.CallLock`), cross-process control
(:class:`~.call_control.CallControl`, sent by
:class:`~.call_hangup.CallHangup`), speech through the existing daemon
client (:class:`VoxClientSync.synthesize`), and the crash boundary.

Two ways to drive one call, chosen at ``vox call start`` by whether
``--script`` is passed:

- **Live (default).** :class:`~.mic_audio_source.MicAudioSource` captures
  the real microphone; :class:`~punt_vox.providers.elevenlabs_stt.ElevenLabsSTTProvider`
  transcribes each closed turn. This is the path a human uses.
- **Scripted (``--script``, dev/test).** A JSON Lines file of pre-written
  utterances drives synthetic speech/silence :class:`AudioChunk` values
  through the same *real* :class:`TurnDetector`, transcribed by
  :class:`ScriptedSTTProvider` reading the same script -- no microphone, no
  ElevenLabs credentials, no network for the human side of the turn.

Every other component -- the detector, the call state machine, the call
agent, the audible cues -- is shared between both paths.

When the call ends cleanly the agent writes a summary, and ``vox call start``
prints one final line naming the file (``call summary: <path>``) or saying
why there is none -- the line the session that started the call reads.

**Known limitation: the mic-echo guard is duration-estimated, not
signal-based.** ``client.py`` has no true playback-completion signal --
``VoxClientSync.synthesize`` returns once voxd reports audio *enqueued*,
not once it finishes playing. :class:`~.call_live_driver.LiveCallDriver`
closes the mic gate before speaking and holds it shut for an *estimated*
duration (:func:`~punt_vox.providers.convert.estimate_speech_duration_s`)
plus a safety margin -- bounding, not eliminating, the echo window. A real
fix needs either a genuine "wait for playback done" option on the client,
or continuous-stream playback ownership (``docs/conversation-mode-prd.tex``
Chapter 2) -- follow-up work, not attempted here.
"""

from __future__ import annotations

import asyncio
from importlib import import_module
from typing import TYPE_CHECKING, Self, cast, final

from punt_vox.commands.call_options import (
    ContextOpt as _ContextOpt,
    ScriptOpt as _ScriptOpt,
    TraceTurnsOpt as _TraceTurnsOpt,
)
from punt_vox.logging_config import configure_turn_timer_logging

if TYPE_CHECKING:
    from punt_vox.commands.call_execution import CallExecution

__all__ = ["PiCallCli"]


@final
class PiCallCli:
    """``vox call start --context``: the pi call agent's entry point."""

    __slots__ = ()

    def __new__(cls) -> Self:
        """Create the pi call verb."""
        return super().__new__(cls)

    def start(
        self,
        context: _ContextOpt,
        script: _ScriptOpt = None,
        *,
        trace_turns: _TraceTurnsOpt = False,
    ) -> None:
        """Start a pi call: listen, detect turns, forward them, speak the reply."""
        # Always on, regardless of *trace_turns*: the turn-timer's own
        # logger is forced to DEBUG on vox.log for every call, not only
        # ones run with --trace-turns -- see configure_turn_timer_logging's
        # docstring. trace_turns only decides whether those same lines also
        # echo to this terminal.
        configure_turn_timer_logging(echo_to_console=trace_turns)
        execution = cast(
            "type[CallExecution]",
            import_module("punt_vox.commands.call_execution").CallExecution,
        )
        asyncio.run(execution.run(context, script))
