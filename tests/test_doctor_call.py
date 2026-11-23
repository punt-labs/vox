"""Host requirements for voice calls."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from punt_vox.doctor_call import CallChecks
from punt_vox.doctor_result import WARN, CheckResult


@pytest.mark.parametrize("tmux_found", [True, False])
def test_call_checks_report_host_requirements(
    monkeypatch: pytest.MonkeyPatch,
    tmux_found: bool,
) -> None:
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)

    def which(name: str) -> str | None:
        return "/bin/tmux" if tmux_found and name == "tmux" else None

    with (
        patch("shutil.which", side_effect=which),
        patch("punt_vox.doctor_call_scope.httpx.get") as request,
    ):
        results = CallChecks().run()

    request.assert_not_called()
    assert [result.message.split(":")[0] for result in results] == [
        "ElevenLabs convai_write (local ELEVENLABS_API_KEY)",
        "Claude login",
        "tmux",
        "Echo cancellation",
    ]
    assert [result.status_kind for result in results] == [
        "skip",
        "skip",
        "pass" if tmux_found else "warn",
        "skip",
    ]
    assert not any(result.required for result in results)


def test_call_checks_includes_systemd_result(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    worker_result = CheckResult.of(WARN, "systemd version: 249", required=False)
    with (
        patch("shutil.which", return_value=None),
        patch(
            "punt_vox.doctor_call.CallWorkerChecks.run",
            side_effect=lambda: [worker_result],
        ),
    ):
        assert worker_result in CallChecks().run()
