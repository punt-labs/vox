"""Retry diagnostics summarize a whole outage instead of logging every tick."""

import logging

import pytest
from punt_lux import HubUnavailableError

from punt_vox.lux_common import HubOutageLog
from punt_vox.voxd.music_player.lux_receive_retry import LuxReceiveRetry


@pytest.mark.parametrize("error", [HubUnavailableError, ValueError])
async def test_outage_has_one_warning_and_one_recovery(
    caplog: pytest.LogCaptureFixture, error: type[Exception]
) -> None:
    retry = LuxReceiveRetry(HubOutageLog(logging.getLogger(__name__)))
    attempts = 0

    async def listen() -> None:
        nonlocal attempts
        attempts += 1
        if attempts <= 3:
            raise error("private-failure-value")
        retry.connected()

    with caplog.at_level(logging.DEBUG):
        await retry.run(listen, 0)
    warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
    summaries = [r for r in caplog.records if "outcome=" in r.getMessage()]
    assert len(warnings) == 1
    assert len(summaries) == 1
    assert summaries[0].getMessage().endswith("outcome=recovered failures=3")
    assert summaries[0].levelno == logging.INFO
    assert (
        sum(
            r.levelno == logging.DEBUG and "failed; retrying" in r.getMessage()
            for r in caplog.records
        )
        == 2
    )
    assert "private-failure-value" not in caplog.text
