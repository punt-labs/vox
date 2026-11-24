"""Check the read-only Claude Desktop registration verdict."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from punt_vox.desktop_install import DesktopInstaller
from punt_vox.desktop_registration import DesktopRegistration


@pytest.mark.parametrize(
    ("contents", "expected"),
    [
        (None, "no config"),
        ("bad json", "config unreadable"),
        ("[]", "not registered"),
        ('{"mcpServers": {"vox": {}}}', "registered"),
        ('{"mcpServers": {}}', "not registered"),
    ],
)
def test_desktop_registration_readback(
    tmp_path: Path, contents: str | None, expected: str
) -> None:
    path = tmp_path / "desktop.json"
    if contents is not None:
        path.write_text(contents, encoding="utf-8")
    with patch.object(DesktopInstaller, "config_path", return_value=path):
        assert DesktopRegistration.read() == expected


def test_desktop_registration_reports_config_read_denied(tmp_path: Path) -> None:
    path = tmp_path / "desktop.json"
    with (
        patch.object(DesktopInstaller, "config_path", return_value=path),
        patch.object(Path, "read_text", side_effect=PermissionError),
    ):
        assert DesktopRegistration.read() == "config unreadable"
