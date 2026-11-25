"""Advisory diagnostics for the voice call on the local host."""

from __future__ import annotations

from typing import Self, final

from punt_vox.doctor_call_claude import ClaudeLoginCheck
from punt_vox.doctor_call_daemon import MicCaptureCheck, WorkerReposCheck
from punt_vox.doctor_call_scope import ConvAIKeyCheck
from punt_vox.doctor_call_workers import CallWorkerChecks
from punt_vox.doctor_result import OPTIONAL, CheckResult


@final
class CallChecks:
    """Collect the optional voice call prerequisites without starting a call."""

    __slots__ = ()

    def __new__(cls) -> Self:
        """Create the call prerequisite checks."""
        return super().__new__(cls)

    def run(self) -> list[CheckResult]:
        """Report the credential, launcher, daemon-side mic and allowlist rows."""
        return [
            ConvAIKeyCheck().run(),
            ClaudeLoginCheck().run(),
            *CallWorkerChecks().run(),
            MicCaptureCheck().run(),
            WorkerReposCheck().run(),
            CheckResult.of(
                OPTIONAL,
                "Echo cancellation: not built in; use 'vox call --headset'"
                " or declare an already configured system AEC path with --aec",
                required=False,
            ),
        ]
