"""DeviceWatch: a stalled device is abandoned, a slow one is waited for."""

from __future__ import annotations

import asyncio
import logging

import pytest

from punt_vox.voxd import pcm_watch
from punt_vox.voxd.pcm_output import OutputDeviceLostError
from punt_vox.voxd.pcm_watch import DeviceWatch


class TestUntil:
    async def test_returns_at_once_when_the_event_is_already_set(self) -> None:
        done = asyncio.Event()
        done.set()
        await asyncio.wait_for(
            DeviceWatch(asyncio.get_running_loop()).until(done), timeout=1
        )

    async def test_returns_when_the_event_is_set_later(self) -> None:
        done = asyncio.Event()
        watch = DeviceWatch(asyncio.get_running_loop())
        waiting = asyncio.create_task(watch.until(done))
        await asyncio.sleep(0.01)
        done.set()
        await asyncio.wait_for(waiting, timeout=1)
        assert not watch.is_lost

    async def test_a_device_with_no_callbacks_is_lost_with_a_log_line(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        monkeypatch.setattr(pcm_watch, "STALL_WAIT_S", 0.01)
        watch = DeviceWatch(asyncio.get_running_loop())
        with (
            caplog.at_level(logging.WARNING, logger="punt_vox.voxd.pcm_watch"),
            pytest.raises(OutputDeviceLostError),
        ):
            await asyncio.wait_for(watch.until(asyncio.Event()), timeout=2)
        assert watch.is_lost
        assert "no callback for at least 0.01 s" in caplog.text

    async def test_a_device_that_keeps_calling_back_is_waited_for(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(pcm_watch, "STALL_WAIT_S", 0.02)
        done = asyncio.Event()
        watch = DeviceWatch(asyncio.get_running_loop())
        waiting = asyncio.create_task(watch.until(done))
        for _ in range(10):  # five stall periods, each with a callback in it
            await asyncio.sleep(0.01)
            watch.tick()
        done.set()
        await asyncio.wait_for(waiting, timeout=1)
        assert not watch.is_lost


class TestPlayedOut:
    async def test_true_once_the_device_reports_finished(self) -> None:
        watch = DeviceWatch(asyncio.get_running_loop())
        waiting = asyncio.create_task(watch.played_out())
        await asyncio.sleep(0.01)
        await asyncio.to_thread(watch.finished)  # the device thread
        assert await asyncio.wait_for(waiting, timeout=1)
        assert not watch.is_lost

    async def test_false_and_lost_when_the_device_never_does(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        monkeypatch.setattr(pcm_watch, "PLAYOUT_WAIT_S", 0.01)
        watch = DeviceWatch(asyncio.get_running_loop())
        with caplog.at_level(logging.WARNING, logger="punt_vox.voxd.pcm_watch"):
            assert not await watch.played_out()
        assert watch.is_lost
        assert "did not finish playing" in caplog.text


class TestSignal:
    async def test_a_closed_loop_at_shutdown_is_ignored(self) -> None:
        loop = asyncio.new_event_loop()
        loop.close()
        DeviceWatch(loop).signal(asyncio.Event())  # no RuntimeError
