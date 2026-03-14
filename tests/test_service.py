"""Tests for punt_vox.service — daemon lifecycle management."""

from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

import pytest

from punt_vox.daemon import DEFAULT_PORT
from punt_vox.service import (
    _launchd_plist_content,  # pyright: ignore[reportPrivateUsage]
    _systemd_unit_content,  # pyright: ignore[reportPrivateUsage]
    _vox_exec_args,  # pyright: ignore[reportPrivateUsage]
    detect_platform,
)

# ---------------------------------------------------------------------------
# Exec args
# ---------------------------------------------------------------------------


def test_vox_exec_args() -> None:
    args = _vox_exec_args()
    assert args[0] == sys.executable
    assert "-m" in args
    assert "punt_vox" in args
    assert "serve" in args
    assert "--port" in args
    assert str(DEFAULT_PORT) in args


# ---------------------------------------------------------------------------
# launchd plist content
# ---------------------------------------------------------------------------


def test_launchd_plist_contains_label() -> None:
    content = _launchd_plist_content()
    assert "com.punt-labs.vox" in content


def test_launchd_plist_contains_args() -> None:
    content = _launchd_plist_content()
    assert "serve" in content
    assert str(DEFAULT_PORT) in content


def test_launchd_plist_contains_log_paths() -> None:
    content = _launchd_plist_content()
    assert "daemon-stdout.log" in content
    assert "daemon-stderr.log" in content


def test_launchd_plist_keepalive() -> None:
    content = _launchd_plist_content()
    assert "<key>KeepAlive</key>" in content
    assert "<true/>" in content


# ---------------------------------------------------------------------------
# systemd unit content
# ---------------------------------------------------------------------------


def test_systemd_unit_contains_exec_start() -> None:
    content = _systemd_unit_content()
    assert "ExecStart=" in content
    assert "serve" in content
    assert str(DEFAULT_PORT) in content


def test_systemd_unit_restart_policy() -> None:
    content = _systemd_unit_content()
    assert "Restart=on-failure" in content
    assert "RestartSec=5" in content


def test_systemd_unit_description() -> None:
    content = _systemd_unit_content()
    assert "Vox text-to-speech daemon" in content


# ---------------------------------------------------------------------------
# Platform detection
# ---------------------------------------------------------------------------


@patch("punt_vox.service.platform.system", return_value="Darwin")
def test_detect_platform_macos(_mock: MagicMock) -> None:
    assert detect_platform() == "macos"


@patch("punt_vox.service.platform.system", return_value="Linux")
def test_detect_platform_linux(_mock: MagicMock) -> None:
    assert detect_platform() == "linux"


@patch("punt_vox.service.platform.system", return_value="Windows")
def test_detect_platform_unsupported(_mock: MagicMock) -> None:
    with pytest.raises(SystemExit):
        detect_platform()
