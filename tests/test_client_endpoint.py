"""Where voxd is: the run files, and the URI one connect is made with."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from websockets.uri import parse_uri

from punt_vox.client_endpoint import RunFiles, VoxdEndpoint
from punt_vox.client_errors import (
    VoxdConnectionError,
    VoxdNotPublishedError,
    VoxdRefusedError,
)
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
    _local_only(tmp_path, monkeypatch)
    with pytest.raises(VoxdNotPublishedError, match="vox daemon install") as caught:
        VoxdEndpoint(None, None, "tok").uri()
    assert isinstance(caught.value, VoxdRefusedError)


def _local_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Run files under *tmp_path*; no VOXD_HOST or VOXD_PORT override."""
    monkeypatch.setattr("punt_vox.client_endpoint._user_run_dir", lambda: tmp_path)
    monkeypatch.delenv("VOXD_HOST", raising=False)
    monkeypatch.delenv("VOXD_PORT", raising=False)


def _not_a_refusal(match: str) -> None:
    """Resolve the URI; it must fail as a plain connection error naming *match*."""
    with pytest.raises(VoxdConnectionError, match=match) as caught:
        VoxdEndpoint(None, None, "tok").uri()
    assert not isinstance(caught.value, VoxdRefusedError)


def test_an_unreadable_serve_port_is_not_a_refusal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The file is there; what it says is unknown, not "nothing listens"."""
    _local_only(tmp_path, monkeypatch)
    (tmp_path / "serve.port").mkdir()
    _not_a_refusal(r"cannot read .*serve\.port")


@pytest.mark.parametrize("content", ["garbage", "", "84 21"])
def test_a_corrupt_serve_port_is_not_a_refusal(
    content: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _local_only(tmp_path, monkeypatch)
    (tmp_path / "serve.port").write_text(content)
    _not_a_refusal(r"serve\.port .*does not hold a port")


@pytest.mark.parametrize("raw", ["abc", "70000", "0"])
def test_a_malformed_voxd_port_is_not_a_refusal(
    raw: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A bad override is named, never silently replaced by the local file."""
    _local_only(tmp_path, monkeypatch)
    (tmp_path / "serve.port").write_text("8421")
    monkeypatch.setenv("VOXD_PORT", raw)
    _not_a_refusal(rf"VOXD_PORT={raw!r} is not a port")


def test_a_remote_voxd_host_with_no_port_is_not_a_refusal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No local serve.port says nothing about a daemon on another host."""
    _local_only(tmp_path, monkeypatch)
    monkeypatch.setenv("VOXD_HOST", "voxd.example")
    _not_a_refusal("set VOXD_PORT")
