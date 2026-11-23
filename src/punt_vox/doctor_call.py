"""Advisory diagnostics for the Mode B call on the local host."""

from __future__ import annotations

import shutil
from typing import Self, final

from punt_vox.doctor_call_claude import ClaudeLoginCheck
from punt_vox.doctor_call_scope import ConvAIKeyCheck
from punt_vox.doctor_result import OK, OPTIONAL, WARN, CheckResult


@final
class CallChecks:
    """Collect the optional Mode B prerequisites without starting a call."""

    __slots__ = ()

    def __new__(cls) -> Self:
        """Create the call prerequisite checks."""
        return super().__new__(cls)

    def run(self) -> list[CheckResult]:
        """Report the local credential, launcher and audio prerequisites."""
        return [
            ConvAIKeyCheck().run(),
            ClaudeLoginCheck().run(),
            self._tmux(),
            CheckResult.of(
                OPTIONAL,
                "Echo cancellation: not built in; use 'vox call --headset'"
                " or declare an already configured system AEC path with --aec",
                required=False,
            ),
        ]

    @staticmethod
    def _tmux() -> CheckResult:
        if shutil.which("tmux"):
            return CheckResult.of(OK, "tmux: present", required=False)
        return CheckResult.of(
            WARN, "tmux: not found — install it to launch call workers", required=False
        )
