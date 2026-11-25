"""Tests for the doctor CLI's rendering and exit status."""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest
import typer

from punt_vox.cli_doctor import DoctorCli
from punt_vox.client_sync import VoxClientSync
from punt_vox.doctor_result import CheckResult
from punt_vox.output_formatter import OutputFormatter


def test_required_check_failure_emits_the_report_before_exiting(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with (
        patch(
            "punt_vox.cli_doctor.DoctorCheck.run_all",
            return_value=[CheckResult.fail("Daemon: not running")],
        ),
        pytest.raises(typer.Exit) as exc,
    ):
        DoctorCli.emit(OutputFormatter(json_output=True), VoxClientSync())

    assert exc.value.exit_code == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["failed"] == 1
    assert payload["checks"][0]["message"] == "Daemon: not running"
