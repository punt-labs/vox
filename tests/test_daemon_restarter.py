"""Tests for punt_vox.daemon_restarter."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
import typer

from punt_vox.daemon_restarter import DaemonRestarter
from punt_vox.output_formatter import OutputFormatter

_MOD = "punt_vox.daemon_restarter"


class TestDaemonRestarter:
    def test_refuse_windows(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Raises BadParameter on Windows."""
        monkeypatch.setattr(f"{_MOD}.sys", MagicMock(platform="win32"))
        fmt = OutputFormatter()
        restarter = DaemonRestarter(fmt)
        with pytest.raises(typer.BadParameter, match="only supported on macOS"):
            restarter.run()

    @patch(f"{_MOD}.os")
    @patch(f"{_MOD}.sys")
    def test_refuse_root(
        self,
        mock_sys: MagicMock,
        mock_os: MagicMock,
    ) -> None:
        """Raises BadParameter when run as root."""
        mock_sys.platform = "linux"
        mock_os.geteuid.return_value = 0
        fmt = OutputFormatter()
        restarter = DaemonRestarter(fmt)
        with pytest.raises(typer.BadParameter, match="not root"):
            restarter.run()

    @patch(f"{_MOD}.VoxClientSync")
    @patch(f"{_MOD}.subprocess.run")
    @patch(f"{_MOD}.installed_version", return_value="5.0.0")
    def test_successful_restart(
        self,
        _mock_version: MagicMock,
        mock_subprocess: MagicMock,
        mock_client_cls: MagicMock,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Full restart succeeds when health reports matching version."""
        monkeypatch.setattr(f"{_MOD}.sys", MagicMock(platform="linux"))
        mock_os = MagicMock(geteuid=MagicMock(return_value=1000))
        monkeypatch.setattr(f"{_MOD}.os", mock_os)

        # Mock service functions
        monkeypatch.setattr(
            f"{_MOD}.DaemonRestarter._detect_platform",
            staticmethod(lambda: "linux"),
        )
        monkeypatch.setattr(
            f"{_MOD}.DaemonRestarter._stop",
            staticmethod(lambda plat: None),  # pyright: ignore[reportUnknownLambdaType]
        )
        monkeypatch.setattr(
            f"{_MOD}.DaemonRestarter._wait_port_free",
            staticmethod(lambda: None),
        )

        mock_subprocess.return_value = MagicMock(returncode=0)
        mock_client_cls.return_value.health.return_value = {
            "pid": 1234,
            "port": 8421,
            "daemon_version": "5.0.0",
        }

        fmt = OutputFormatter()
        restarter = DaemonRestarter(fmt)
        restarter.run()  # should not raise

    @patch(f"{_MOD}.VoxClientSync")
    @patch(f"{_MOD}.time")
    @patch(f"{_MOD}.subprocess.run")
    def test_version_mismatch_exits(
        self,
        mock_subprocess: MagicMock,
        mock_time: MagicMock,
        mock_client_cls: MagicMock,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Exit code 1 when daemon version does not match wheel."""
        monkeypatch.setattr(f"{_MOD}.sys", MagicMock(platform="linux"))
        mock_os = MagicMock(geteuid=MagicMock(return_value=1000))
        monkeypatch.setattr(f"{_MOD}.os", mock_os)
        monkeypatch.setattr(
            f"{_MOD}.DaemonRestarter._detect_platform",
            staticmethod(lambda: "linux"),
        )
        monkeypatch.setattr(
            f"{_MOD}.DaemonRestarter._stop",
            staticmethod(lambda plat: None),  # pyright: ignore[reportUnknownLambdaType]
        )
        monkeypatch.setattr(
            f"{_MOD}.DaemonRestarter._wait_port_free",
            staticmethod(lambda: None),
        )
        monkeypatch.setattr(f"{_MOD}.installed_version", lambda: "5.1.0")

        mock_subprocess.return_value = MagicMock(returncode=0)
        mock_client_cls.return_value.health.return_value = {
            "pid": 1234,
            "port": 8421,
            "daemon_version": "5.0.0",
        }
        # Make time.monotonic() return a value within the deadline
        mock_time.monotonic.side_effect = [0.0, 0.1]
        mock_time.sleep = MagicMock()

        fmt = OutputFormatter()
        restarter = DaemonRestarter(fmt)
        with pytest.raises(typer.Exit):
            restarter.run()

    def test_uses_stop_daemon_public_api(self) -> None:
        """DaemonRestarter._stop imports the public stop_daemon function."""
        with patch("punt_vox.service.stop_daemon") as mock_stop:
            DaemonRestarter._stop("macos")  # pyright: ignore[reportPrivateUsage]
            mock_stop.assert_called_once_with("macos")
