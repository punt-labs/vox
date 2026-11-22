"""Exercise safe mode rejection through both client entry points."""

from __future__ import annotations

import logging
import re
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from punt_vox import server
from punt_vox.__main__ import app
from punt_vox.config import SpeakMode


@pytest.mark.parametrize("mode", ["private-mode\nforged", ""])
def test_cli_rejection(mode: str, caplog: pytest.LogCaptureFixture) -> None:
    with (
        patch("punt_vox.cli_io.configure_client_logging"),
        caplog.at_level(logging.INFO),
    ):
        result = CliRunner().invoke(app, ["speak", mode])
    assert result.exit_code == 1
    records = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(records) == 1
    assert re.fullmatch(
        r"speak rejected reason=invalid_mode id=[0-9a-f]{12}", records[0].getMessage()
    )
    assert "private-mode" not in caplog.text
    assert "forged" not in caplog.text


@pytest.mark.parametrize("mode", ["private-mode\nforged", ""])
async def test_mcp_rejection(mode: str, caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO):
        result = await server.mcp.call_tool("speak", {"mode": mode})
    assert "error" in str(result)
    records = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(records) == 1
    assert re.fullmatch(
        r"speak rejected reason=invalid_mode id=[0-9a-f]{12}", records[0].getMessage()
    )
    assert "private-mode" not in caplog.text
    assert "forged" not in caplog.text
    assert sum(r.getMessage() == "mic:speak" for r in caplog.records) == 1


@pytest.mark.parametrize("mode", ["y", "n"])
def test_valid_mode_is_silent(mode: str, caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.DEBUG):
        assert SpeakMode.accepts(mode)
    assert not caplog.records


def test_session_rejection(caplog: pytest.LogCaptureFixture) -> None:
    session = server.SessionConfig(_speak="y")
    with caplog.at_level(logging.INFO), pytest.raises(ValueError):
        session.set_speak("private-mode\nforged")
    assert session.speak == "y"
    assert not session.speak_explicit
    records = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(records) == 1
    assert re.fullmatch(
        r"speak rejected reason=invalid_mode id=[0-9a-f]{12}", records[0].getMessage()
    )
    assert "private-mode" not in caplog.text
    assert "forged" not in caplog.text
