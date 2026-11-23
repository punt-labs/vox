"""Host requirements for voice calls."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from punt_vox.doctor_call import CallChecks


@pytest.mark.parametrize("tmux_found", [True, False])
def test_call_checks_report_host_requirements(
    monkeypatch: pytest.MonkeyPatch,
    tmux_found: bool,
) -> None:
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)

    def which(name: str) -> str | None:
        return "/bin/tmux" if tmux_found and name == "tmux" else None

    with (
        patch("punt_vox.doctor_call.shutil.which", side_effect=which),
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
