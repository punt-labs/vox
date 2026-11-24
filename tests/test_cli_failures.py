"""Tests for the CLI failure boundary."""

from __future__ import annotations

import pytest

from punt_vox.cli_failures import CliFailures


def test_unrelated_decode_errors_are_not_presented_as_config_errors() -> None:
    failure = UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte")
    with (
        pytest.raises(UnicodeDecodeError) as caught,
        CliFailures.report(json_output=True),
    ):
        raise failure

    assert caught.value is failure
