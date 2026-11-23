from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from punt_vox.hook_context_client import HookContextClient


async def test_worker_session_identity_requires_server_admission(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("VOX_WORKER_SESSION_ID", "worker")
    payload: dict[str, object] = {
        "session_id": "worker",
        "cwd": "/repo",
        "hook_event_name": "Stop",
        "relay_seq": 2,
    }
    request = AsyncMock(side_effect=[{"admitted": True}, {}])

    with patch.object(HookContextClient, "_request", request):
        await HookContextClient.send_event(payload)

    assert [call.args[0] for call in request.await_args_list] == [
        {
            "type": "worker_hook_admission",
            "session_id": "worker",
            "cwd": "/repo",
            "hook_event_name": "Stop",
        },
        {"type": "hook_event", "payload": payload, "worker_only": True},
    ]


async def test_pending_admission_exhausts_retry_budget_as_timeout() -> None:
    payload: dict[str, object] = {"session_id": "worker", "hook_event_name": "Stop"}
    request = AsyncMock(return_value={"admitted": False, "retryable": True})

    with (
        patch.object(HookContextClient, "_request", request),
        patch("asyncio.sleep", new_callable=AsyncMock) as delay,
        pytest.raises(TimeoutError, match="pending liveness"),
    ):
        await HookContextClient.send_event(payload, worker_only=True)

    assert request.await_count == delay.await_count + 1
    assert delay.await_count > 0
    assert all(
        call.args[0]["type"] == "worker_hook_admission"
        for call in request.await_args_list
    )
