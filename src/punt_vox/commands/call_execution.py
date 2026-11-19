"""Run a call under the CLI lock and report unexpected failures aloud."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, final

import typer

from punt_vox.client_sync import VoxClientSync
from punt_vox.commands.call_cues import DaemonCues
from punt_vox.commands.call_runner import CallRunner
from punt_vox.commands.call_spec import resolve_call_spec
from punt_vox.dirs import find_repo_root
from punt_vox.voxd.conversation_mode.call_control import CallControl
from punt_vox.voxd.conversation_mode.call_lock import CallLock, CallLockActiveError

if TYPE_CHECKING:
    from punt_vox.voxd.conversation_mode.call_session import CallSession, SpeakFn

__all__ = ["CallExecution"]

logger = logging.getLogger(__name__)


@final
class CallExecution:
    """Own the CLI call lifecycle: wire speech, hold the lock, and report crashes."""

    __slots__ = ()

    @classmethod
    async def run(cls, context: Path, script: Path | None) -> None:
        """Run one call under the lock, behind the crash boundary.

        Raises:
            typer.BadParameter: If the call configuration or script is invalid.
        """
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
            apply_control=cls._apply_control,
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
