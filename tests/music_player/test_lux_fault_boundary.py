"""Metadata-only boundary diagnostics preserve process-control exceptions."""

import asyncio
import logging

import pytest

from punt_vox.voxd.music_player.lux_fault_boundary import LuxFaultBoundary


def test_success_has_no_diagnostic(caplog: pytest.LogCaptureFixture) -> None:
    with LuxFaultBoundary(logging.getLogger(__name__), "operation failed") as fault:
        pass
    assert not fault.failed()
    assert not caplog.records


def test_fault_omits_exception_values(caplog: pytest.LogCaptureFixture) -> None:
    with LuxFaultBoundary(logging.getLogger(__name__), "operation failed") as fault:
        raise ValueError("private-value")
    assert fault.failed()
    assert len(caplog.records) == 1
    assert caplog.records[0].getMessage() == "[lux] operation failed"
    assert caplog.records[0].exc_info is None
    assert "private-value" not in caplog.text


def test_cancellation_propagates_without_logging(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with (
        pytest.raises(asyncio.CancelledError),
        LuxFaultBoundary(logging.getLogger(__name__), "operation failed"),
    ):
        raise asyncio.CancelledError
    assert not caplog.records
