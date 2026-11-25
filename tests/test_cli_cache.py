"""Tests for cache CLI rendering and failure exits."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Self, final

import pytest
import typer

from punt_vox.cli_cache import CacheCli
from punt_vox.client_errors import VoxdConnectionError, VoxdProtocolError
from punt_vox.client_results import CacheStatus
from punt_vox.client_sync import VoxClientSync
from punt_vox.output_formatter import OutputFormatter

if TYPE_CHECKING:
    from collections.abc import Callable


@final
class StubCacheClient(VoxClientSync):
    __slots__ = ("_error",)
    _error: VoxdConnectionError | VoxdProtocolError | None

    def __new__(
        cls, error: VoxdConnectionError | VoxdProtocolError | None = None
    ) -> Self:
        self = super().__new__(cls, host="127.0.0.1")
        self._error = error
        return self

    def cache_status(self) -> CacheStatus:
        if self._error is not None:
            raise self._error
        return CacheStatus(entries=4, size_bytes=8192, path=Path("/daemon/cache"))

    def cache_clear(self) -> int:
        if self._error is not None:
            raise self._error
        return 4


@pytest.mark.parametrize("json_output", [False, True])
def test_status_output(capsys: pytest.CaptureFixture[str], json_output: bool) -> None:
    CacheCli(OutputFormatter(json_output=json_output)).status(StubCacheClient())

    result = capsys.readouterr()
    assert result.err == ""
    if json_output:
        assert json.loads(result.out) == {
            "entries": 4,
            "size_bytes": 8192,
            "path": "/daemon/cache",
        }
    else:
        assert result.out == "Entries: 4\nSize:    8.0 KB\nPath:    /daemon/cache\n"


@pytest.mark.parametrize("json_output", [False, True])
def test_clear_output(capsys: pytest.CaptureFixture[str], json_output: bool) -> None:
    CacheCli(OutputFormatter(json_output=json_output)).clear(StubCacheClient())

    result = capsys.readouterr()
    assert result.err == ""
    if json_output:
        assert json.loads(result.out) == {"cleared": 4}
    else:
        assert result.out == "Cleared 4 cached files.\n"


@pytest.mark.parametrize("command", [CacheCli.status, CacheCli.clear])
@pytest.mark.parametrize("error_type", [VoxdConnectionError, VoxdProtocolError])
@pytest.mark.parametrize("json_output", [False, True])
def test_daemon_errors(
    capsys: pytest.CaptureFixture[str],
    command: Callable[[CacheCli, VoxClientSync], None],
    error_type: type[VoxdConnectionError] | type[VoxdProtocolError],
    json_output: bool,
) -> None:
    presenter = CacheCli(OutputFormatter(json_output=json_output))

    with pytest.raises(typer.Exit) as exc:
        command(presenter, StubCacheClient(error_type("cache unavailable")))

    assert exc.value.exit_code == 1
    result = capsys.readouterr()
    if json_output:
        assert json.loads(result.out) == {"error": "cache unavailable"}
        assert result.err == ""
    else:
        assert result.out == ""
        assert result.err == "Error: cache unavailable\n"
