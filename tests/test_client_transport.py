"""The run files that locate voxd: ``serve.port`` and ``serve.token``."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from punt_vox.client_transport import read_port_file, read_token_file

# ---------------------------------------------------------------------------
# Port / token file readers
# ---------------------------------------------------------------------------


def test_read_port_file(tmp_path: Path) -> None:
    port_file = tmp_path / "serve.port"
    port_file.write_text("9999")
    with patch("punt_vox.client_transport._user_run_dir", return_value=tmp_path):
        assert read_port_file() == 9999


def test_read_port_file_missing(tmp_path: Path) -> None:
    with patch("punt_vox.client_transport._user_run_dir", return_value=tmp_path):
        assert read_port_file() is None


def test_read_token_file(tmp_path: Path) -> None:
    token_file = tmp_path / "serve.token"
    token_file.write_text("secret123")
    with patch("punt_vox.client_transport._user_run_dir", return_value=tmp_path):
        assert read_token_file() == "secret123"


def test_read_token_file_missing(tmp_path: Path) -> None:
    with patch("punt_vox.client_transport._user_run_dir", return_value=tmp_path):
        assert read_token_file() is None
