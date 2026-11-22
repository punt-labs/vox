"""Stamp hooks in the sender, then hand their delivery to a detached process."""

from __future__ import annotations

import asyncio
import json
import logging
import subprocess
import sys
import tempfile
from collections.abc import Callable
from typing import Self, cast

import typer

from punt_vox.client_errors import VoxdConnectionError, VoxdProtocolError
from punt_vox.hook_context_admission import HookContextAdmission
from punt_vox.hook_context_client import HookContextClient
from punt_vox.hook_context_event import HookIdentity
from punt_vox.hook_context_stage import HookContextStage
from punt_vox.hook_sequence import HookSequence
from punt_vox.logging_config import configure_client_logging
from punt_vox.paths import user_state_dir

logger = logging.getLogger(__name__)


class HookContextCommand:
    """Translate the live relay's stamping error into a visible CLI failure."""

    __slots__ = ("_read",)
    _read: Callable[[], dict[str, object]]

    def __new__(cls, read: Callable[[], dict[str, object]]) -> Self:
        """Read stdin using the shared hook reader."""
        self = super().__new__(cls)
        self._read = read
        return self

    def run(self, *, enabled_only: bool = False) -> None:
        """Stamp and relay a live Claude Code hook payload."""
        read_payload = self._read
        try:
            relay = HookContextRelay(read_payload())
            if enabled_only and not relay.enabled():
                return
            relay.submit()
        except (OSError, ValueError, RecursionError) as exc:
            reason = str(exc) if type(exc) is ValueError else type(exc).__name__
            logger.error("hook context stamping or staging failed: %s", reason)
            typer.echo(f"Hook context stamping or staging failed: {reason}", err=True)
            raise typer.Exit(1) from None


class HookContextRelay:
    """The sender-side hook command's context fanout."""

    __slots__ = ("_payload",)
    _payload: dict[str, object]

    def __new__(cls, payload: dict[str, object]) -> Self:
        """Bind one live Claude hook payload."""
        self = super().__new__(cls)
        self._payload = payload
        return self

    def enabled(self) -> bool:
        """Honor the plugin's existing per-repo enable boundary."""
        return HookContextAdmission(self._payload).enabled()

    def submit(self) -> None:
        """Validate and stamp synchronously; never wait for network delivery."""
        identity = HookIdentity.parse(self._payload)
        if "relay_seq" in self._payload:
            msg = "refusing to stamp an already stamped hook payload"
            raise ValueError(msg)
        state_dir = user_state_dir()
        sequence_dir = state_dir / "hook-sequences"
        staged = HookContextStage.prepare(identity, self._payload, sequence_dir)
        counter = HookSequence(sequence_dir, identity.session)
        staged["relay_seq"] = counter.next()
        with tempfile.TemporaryFile(mode="w+", encoding="utf-8") as source:
            json.dump(staged, source, ensure_ascii=True, allow_nan=False)
            source.seek(0)
            subprocess.Popen(
                [sys.executable, "-E", "-P", "-m", "punt_vox.hook_context_relay"],
                cwd=state_dir,
                stdin=source,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )


def main() -> int:
    """Deliver the staged payload; log failures without printing its content."""
    configure_client_logging(role="hook")
    try:
        payload = cast("dict[str, object]", json.load(sys.stdin))
        asyncio.run(HookContextClient.send_event(payload))
    except (
        ValueError,
        OSError,
        RecursionError,
        VoxdConnectionError,
        VoxdProtocolError,
    ) as exc:
        logger.warning(
            "hook context delivery failed: %s; check vox daemon status and logs",
            type(exc).__name__,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
