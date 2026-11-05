"""Where voxd is: the run files, and the URI one connect is made with."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from websockets.uri import parse_uri

from punt_vox.client_endpoint import RunFiles, VoxdEndpoint
from punt_vox.client_errors import VoxdNotPublishedError, VoxdRefusedError
from punt_vox.service.health import HealthTarget

# ---------------------------------------------------------------------------
# Port / token file readers
# ---------------------------------------------------------------------------


def test_read_port_file(tmp_path: Path) -> None:
    port_file = tmp_path / "serve.port"
    port_file.write_text("9999")
    with patch("punt_vox.client_endpoint._user_run_dir", return_value=tmp_path):
        assert RunFiles.port() == 9999


def test_read_port_file_missing(tmp_path: Path) -> None:
    with patch("punt_vox.client_endpoint._user_run_dir", return_value=tmp_path):
        assert RunFiles.port() is None


def test_read_token_file(tmp_path: Path) -> None:
    token_file = tmp_path / "serve.token"
    token_file.write_text("secret123")
    with patch("punt_vox.client_endpoint._user_run_dir", return_value=tmp_path):
        assert RunFiles.token() == "secret123"


def test_read_token_file_missing(tmp_path: Path) -> None:
    with patch("punt_vox.client_endpoint._user_run_dir", return_value=tmp_path):
        assert RunFiles.token() is None


# ---------------------------------------------------------------------------
# The URI: an IPv6 literal is bracketed so it parses at all
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("host", "uri"),
    [
        ("127.0.0.1", "ws://127.0.0.1:8421/ws?token=tok"),
        ("voxd.example", "ws://voxd.example:8421/ws?token=tok"),
        ("::1", "ws://[::1]:8421/ws?token=tok"),
        ("fe80::1%lo", "ws://[fe80::1%lo]:8421/ws?token=tok"),
    ],
)
def test_the_uri_brackets_an_ipv6_literal(host: str, uri: str) -> None:
    assert VoxdEndpoint(host, 8421, "tok").uri() == uri


def test_an_ipv6_bind_gives_a_health_uri_that_parses() -> None:
    """A unit bound to ::1 is polled at ws://[::1]:8421/..., never ws://::1:..."""
    target = HealthTarget("::1")
    uri = VoxdEndpoint(target.host, target.port, "tok").uri()
    assert uri.startswith("ws://[::1]:8421/")
    assert parse_uri(uri).host == "::1"


def test_no_published_port_is_a_refusal_naming_the_fix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No serve.port, no VOXD_PORT: nothing published a port, so nothing listens."""
    monkeypatch.setattr("punt_vox.client_endpoint._user_run_dir", lambda: tmp_path)
    monkeypatch.delenv("VOXD_PORT", raising=False)
    with pytest.raises(VoxdNotPublishedError, match="vox daemon install") as caught:
        VoxdEndpoint(None, None, "tok").uri()
    assert isinstance(caught.value, VoxdRefusedError)
