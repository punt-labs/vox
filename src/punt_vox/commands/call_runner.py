"""Run one call end to end: spawn its agent, drive the audio loop, end the agent.

Extracted from :mod:`punt_vox.commands.call` so the CLI module keeps only CLI
concerns (verbs, the call lock, the crash boundary) and this one owns the
call's own sequence:

1. :meth:`~punt_vox.voxd.conversation_mode.call_agent_host.CallAgentHost.open`
   reads the context snapshot and spawns the call agent -- before the call
   opens, so a bad snapshot or an agent that will not start is a clean usage
   error, not a call that begins and then fails.
2. One drive loop runs the call: scripted (``--script``) or live.
3. The agent is ended: with a summary when the loop returned normally,
   without one when it raised -- the process never outlives the call.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Self, final

import typer

from punt_vox.commands.call_live_driver import LiveCallDriver
from punt_vox.commands.call_scripted import ScriptedCallDriver
from punt_vox.commands.call_wiring import CallWiring
from punt_vox.voxd.conversation_mode.call_agent_errors import CallAgentSpawnError
from punt_vox.voxd.conversation_mode.call_agent_host import CallAgentHost

if TYPE_CHECKING:
    from punt_vox.voxd.conversation_mode.call_agent_host import CallOutcome
    from punt_vox.voxd.conversation_mode.call_control import ApplyControlFn, CallControl
    from punt_vox.voxd.conversation_mode.speak_fn import SpeakFn
    from punt_vox.voxd.conversation_mode.wait_cue import ChimeFn

__all__ = ["CallRunner"]

logger = logging.getLogger(__name__)


@final
class CallRunner:
    """Run one call: spawn its agent, drive it, end it."""

    __slots__ = ("_apply_control", "_chime", "_control", "_speak")
    _speak: SpeakFn
    _chime: ChimeFn
    _control: CallControl
    _apply_control: ApplyControlFn

    def __new__(
        cls,
        *,
        speak: SpeakFn,
        chime: ChimeFn,
        control: CallControl,
        apply_control: ApplyControlFn,
    ) -> Self:
        self = super().__new__(cls)
        self._speak = speak
        self._chime = chime
        self._control = control
        self._apply_control = apply_control
        return self

    async def run(
        self, context: Path, script: Path | None, *, repo_root: Path
    ) -> CallOutcome:
        """Run the call opened with the snapshot at *context*; return its outcome."""
        try:
            host = await CallAgentHost.open(
                context, cwd=Path.cwd(), repo_root=repo_root, stdin=sys.stdin
            )
        except (ValueError, CallAgentSpawnError) as exc:
            raise typer.BadParameter(str(exc)) from exc
        wiring = CallWiring(
            session_attach=host.agent,
            speak=self._speak,
            chime=self._chime,
            control=self._control,
            apply_control=self._apply_control,
            turn_timer=host.turn_timer,
        )
        try:
            await self._drive(script, wiring)
        except BaseException:
            await self._abandon(host)
            raise
        return await host.finish()

    @staticmethod
    async def _abandon(host: CallAgentHost) -> None:
        """End the agent after a failed call; never mask the call's own error."""
        try:
            await host.abandon()
        except Exception:
            logger.exception("could not end the call agent after the call failed")

    @staticmethod
    async def _drive(script: Path | None, wiring: CallWiring) -> None:
        if script is not None:
            await ScriptedCallDriver.create(script, wiring).run()
            return
        driver = await LiveCallDriver.create(wiring)
        await driver.run()
