from __future__ import annotations

import asyncio
import threading
from unittest.mock import AsyncMock

import pytest

from punt_vox.voxd.workers.startup_handlers import StartupHandlers


async def test_worker_handlers_wait_for_shared_recovery() -> None:
    loop = asyncio.get_running_loop()
    started = asyncio.Event()
    release = threading.Event()
    handler = AsyncMock()

    def recover() -> None:
        loop.call_soon_threadsafe(started.set)
        assert release.wait(5)

    gated = StartupHandlers.start(recover, {"worker_hook_admission": handler})
    admission = gated["worker_hook_admission"]
    socket = AsyncMock()
    async with asyncio.timeout(2):
        try:
            await started.wait()
            first = asyncio.create_task(admission({"id": "first"}, socket))
            done, _ = await asyncio.wait({first}, timeout=0.1)
            assert not done
            first.cancel()
            with pytest.raises(asyncio.CancelledError):
                await first
            handler.assert_not_awaited()
        finally:
            release.set()
        await admission({"id": "second"}, socket)
    handler.assert_awaited_once_with({"id": "second"}, socket)


async def test_failed_recovery_is_logged_and_returns_rpc_error(
    caplog: pytest.LogCaptureFixture,
) -> None:
    def recover() -> None:
        raise OSError("not for the wire")

    handler = AsyncMock()
    gated = StartupHandlers.start(recover, {"worker_hook_admission": handler})
    socket = AsyncMock()
    await gated["worker_hook_admission"]({"id": "admit"}, socket)

    handler.assert_not_awaited()
    socket.send_json.assert_awaited_once_with(
        {
            "type": "error",
            "id": "admit",
            "message": "Worker startup recovery failed; check the daemon log",
        }
    )
    assert "Worker startup recovery failed (OSError)" in caplog.text
