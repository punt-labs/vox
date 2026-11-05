"""Tests for punt_vox.doctor_desktop.DesktopCheck.

``tests/test_doctor.py`` drives the same verdicts through
``DoctorCheck.check_claude_desktop``; these pin the extracted class's own
boundary cases: a config whose top level is not an object, and one that is
not UTF-8, both read as "could not read", never as a crash or a false
"not registered".
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import patch

from punt_vox.doctor_desktop import DesktopCheck

if TYPE_CHECKING:
    from pathlib import Path


def _run_against(config: Path) -> list[str]:
    with patch(
        "punt_vox.desktop_install.DesktopInstaller.config_path",
        return_value=config,
    ):
        return [result.message for result in DesktopCheck().run()]


def test_a_config_whose_top_level_is_a_list_could_not_be_read(
    tmp_path: Path,
) -> None:
    config = tmp_path / "config.json"
    config.write_text('["vox"]', encoding="utf-8")
    assert _run_against(config) == [
        "Claude Desktop config: present",
        "Claude Desktop MCP: could not read config",
    ]


def test_a_config_that_is_not_utf8_could_not_be_read(tmp_path: Path) -> None:
    config = tmp_path / "config.json"
    config.write_bytes(b"\xff\xfe{}")
    assert _run_against(config)[1] == "Claude Desktop MCP: could not read config"


def test_a_missing_config_is_two_optional_rows(tmp_path: Path) -> None:
    with patch(
        "punt_vox.desktop_install.DesktopInstaller.config_path",
        return_value=tmp_path / "absent.json",
    ):
        results = DesktopCheck().run()
    assert [r.required for r in results] == [False, False]
    assert results[1].message.endswith("(run 'vox desktop install')")
