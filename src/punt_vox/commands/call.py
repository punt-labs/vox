"""``vox call`` -- a live voice call with a persistent, read-only call agent.

Wires :class:`~punt_vox.voxd.conversation_mode.call_session.CallSession` (the
tested orchestration in ``voxd/conversation_mode/``) to the CLI. The call's
agent is one ``pi --mode rpc`` process spawned when the call starts and held
for its whole length, opened with a context snapshot the user's main session
wrote (``--context``); :class:`~.call_runner.CallRunner` runs that sequence.
This module keeps the CLI's own concerns: the verbs, the UserPromptSubmit
lock (:class:`~.call_lock.CallLock`), cross-process control
(:class:`~.call_control.CallControl`), speech through the existing daemon
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
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Self, final

import typer

from punt_vox.client_sync import VoxClientSync
from punt_vox.commands.call_cues import DaemonCues
from punt_vox.commands.call_options import (
    ContextOpt as _ContextOpt,
    ScriptOpt as _ScriptOpt,
    TraceTurnsOpt as _TraceTurnsOpt,
)
from punt_vox.commands.call_runner import CallRunner
from punt_vox.commands.call_spec import resolve_call_spec
from punt_vox.dirs import find_repo_root
from punt_vox.logging_config import configure_turn_timer_logging
from punt_vox.voxd.conversation_mode.call_control import CallControl
from punt_vox.voxd.conversation_mode.call_lock import CallLock, CallLockActiveError

if TYPE_CHECKING:
    from punt_vox.voxd.conversation_mode.call_session import CallSession, SpeakFn

__all__ = ["CallCli"]

logger = logging.getLogger(__name__)


@final
class CallCli:
    """The ``vox call`` verbs, as bound methods registered on a Typer app.

    Methods, not decorator-wrapped local functions, so each is a real,
    referenced attribute -- avoiding the "defined but never read" false
    positive a locally-scoped ``@app.command`` closure triggers, the same
    shape :class:`~punt_vox.cli_rec.RecCli` already uses for ``vox rec``.
    """

    __slots__ = ()

    def __new__(cls) -> Self:
        return super().__new__(cls)

    @classmethod
    def build_app(cls) -> typer.Typer:
        """Return the ``vox call`` Typer group with bound methods (no wrappers)."""
        cli = cls()
        app = typer.Typer(
            help="A live voice call with a read-only call agent.",
            no_args_is_help=True,
        )
        app.command("start")(cli.start)
        app.command("stop")(cli.stop)
        return app

    def start(
        self,
        context: _ContextOpt,
        script: _ScriptOpt = None,
        *,
        trace_turns: _TraceTurnsOpt = False,
    ) -> None:
        """Start a call: listen, detect turns, forward them, speak the reply."""
        # Always on, regardless of *trace_turns*: the turn-timer's own
        # logger is forced to DEBUG on vox.log for every call, not only
        # ones run with --trace-turns -- see configure_turn_timer_logging's
        # docstring. trace_turns only decides whether those same lines also
        # echo to this terminal.
        configure_turn_timer_logging(echo_to_console=trace_turns)
        asyncio.run(self._run(context, script))

    def stop(self) -> None:
        """Ask the running call to hang up (FR-2's explicit end).

        Reduces, but does not eliminate, the stale-mailbox race: refuses to
        write a request when :meth:`_require_live_call` finds no call
        currently live, so a stop written against an already-dead lock no
        longer sits in the mailbox for the *next* call to silently consume
        on its first chunk. The check and the write are two separate
        filesystem operations in this process, uncoordinated with the live
        call's own ``lock.release()`` in a different process -- if the live
        call ends in the narrow window between them, the request still gets
        written and still lands in the next call's mailbox. A full fix needs
        the request tagged (pid/timestamp) and validated again on
        :meth:`~.call_control.CallControl.consume`.
        """
        self._require_live_call()
        CallControl.for_repo().request_stop()

    def _require_live_call(self) -> None:
        # is_live(), not read() is not None: a lock file can outlive the
        # process that wrote it (a killed/crashed vox call start leaves the
        # file behind with a now-dead pid). A stop against that stale file
        # must refuse the same as "no call is active" -- writing one anyway
        # lands in a mailbox nobody will ever read, since the process it
        # thinks it is stopping is long gone.
        if not CallLock.for_repo().is_live():
            msg = "no call is active"
            raise typer.BadParameter(msg)

    async def _run(self, context: Path, script: Path | None) -> None:
        """Run one call under the lock, behind the crash boundary."""
        client = VoxClientSync()
        # Resolved once, before the call state machine starts -- see
        # call_spec.py's module docstring for why: a call that cannot
        # speak must refuse to start, not begin and then have every
        # speak() call fail silently on the wire.
        cues = DaemonCues(client, resolve_call_spec())
        speak = cues.speak
        runner = CallRunner(
            speak=speak,
            chime=cues.chime,
            control=CallControl.for_repo(),
            apply_control=self._apply_control,
        )
        lock = CallLock.for_repo()
        try:
            lock.acquire("conversation mode call active")
        except CallLockActiveError as exc:
            raise typer.BadParameter(str(exc)) from exc

        try:
            outcome = await runner.run(
                context, script, repo_root=find_repo_root() or Path.cwd()
            )
        except typer.BadParameter:
            # An expected usage error (an unreadable snapshot, an agent that
            # would not start, an unhealthy STT provider) must reach typer's
            # clean CLI handling untouched, not the crash boundary below --
            # BadParameter is an Exception subclass and would otherwise be
            # logged as a scary, unrelated "call ended unexpectedly".
            raise
        except Exception:
            # System boundary (PY-EH-6): the CLI entry point for a live call.
            # Without this, a provider fault, a subprocess failure, or a mic
            # device-open error dies as a bare traceback with total silence to
            # the human on the other end of the call -- the terminal is not
            # where they are looking. Logs the full exception for diagnosis,
            # speaks a short summary, then re-raises so the process still
            # exits non-zero and the traceback remains available.
            logger.exception("call ended unexpectedly")
            try:
                # A fixed sentence, never {exc} itself: an agent failure can
                # embed the agent's stderr verbatim, which can carry secrets
                # or run to minutes of speech -- the voice-disclosure hazard
                # this whole feature guards against. The full exception is
                # already captured, safely, in the log line above.
                await speak(
                    "The call ended unexpectedly. Check the terminal for details."
                )
            except Exception:
                # speak() is itself a daemon RPC that can fail for the same
                # root cause the call just died from -- Python's implicit
                # exception chaining would otherwise let THIS failure
                # replace *exc* as what propagates from this handler, and
                # the finally block below would release the lock against
                # the wrong exception context. Logged as a secondary
                # failure, never re-raised: the original *exc* is what the
                # human's terminal and this process's exit code must show.
                logger.exception("also failed to speak the call-ended summary")
            raise
        finally:
            lock.release()
        typer.echo(outcome.line())

    @staticmethod
    async def _apply_control(
        control: CallControl, session: CallSession, speak: SpeakFn
    ) -> bool:
        """Consume one pending control request; return whether the loop should stop.

        Shared by both drive loops so a stop request is handled identically
        whether the audio is scripted or live.
        """
        del session, speak
        request = control.consume()
        return request is not None and request.kind == "stop"
