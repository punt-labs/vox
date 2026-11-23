from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, patch

from punt_vox.hook_context_client import HookContextClient

if TYPE_CHECKING:
    import pytest


async def test_worker_session_identity_requires_server_admission(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("VOX_WORKER_SESSION_ID", "worker")
    payload = {
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
