"""Check entry-point role dispatch through vox's shared logging adapter."""

from __future__ import annotations

import logging
import runpy
import sys
from collections.abc import Iterator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import typer
from punt_observe import logging as shared_logging

from punt_vox import hooks, logging_config, mcp_lifecycle, server
from punt_vox.cli_io import OutputFlags
from punt_vox.output_formatter import OutputFormatter
from punt_vox.panel import applet
from punt_vox.voxd import boot
from punt_vox.voxd.config import DaemonConfig


@pytest.fixture
def configured() -> Iterator[MagicMock]:
    """Observe shared setup without leaving root handlers behind."""
    root = logging.getLogger()
    handlers, level, fallback = root.handlers[:], root.level, logging.lastResort
    with patch.object(
        logging_config, "configure_logging", wraps=shared_logging.configure_logging
    ) as configure:
        yield configure
    for handler in root.handlers:
        handler.close()
    root.handlers[:] = handlers
    root.setLevel(level)
    logging.lastResort = fallback


def test_cli_role(configured: MagicMock) -> None:
    OutputFlags(OutputFormatter()).apply(json_output=False, verbose=True, quiet=False)
    assert configured.call_args.args == ("vox", "cli")
    assert configured.call_args.kwargs["level"] == "DEBUG"


def test_hook_role(configured: MagicMock) -> None:
    context = typer.Context(typer.main.get_command(hooks.hook_app))
    context.invoked_subcommand = "stop"
    hooks._hook_callback(context)
    assert configured.call_args.args == ("vox", "hook")


def test_mcp_role(configured: MagicMock) -> None:
    with (
        patch.object(server, "_find_config_dir", return_value=None),
        patch.object(server.mcp, "run"),
        patch.object(server, "_session"),
    ):
        server.run_server()
    assert configured.call_args.args == ("vox", "mcp")


def test_panel_worker_role(configured: MagicMock) -> None:
    worker = MagicMock()
    worker.run = AsyncMock()
    with patch.object(applet.VoxPanelApplet, "unattended", return_value=worker):
        applet.main(session_pid=0)
    assert configured.call_args.args == ("vox", "worker")


def test_playback_worker_role(configured: MagicMock) -> None:
    with patch.object(sys, "argv", ["playback"]), pytest.raises(SystemExit) as exit_:
        runpy.run_module("punt_vox.playback", run_name="__main__")
    assert exit_.value.code == 1
    assert configured.call_args.args == ("vox", "worker")


def test_boot_daemon_role(configured: MagicMock) -> None:
    with (
        patch.object(boot, "ensure_user_dirs"),
        patch.object(DaemonConfig, "for_user"),
        patch.object(boot, "CrashLogger"),
    ):
        boot.DaemonBoot._prepare_logging_and_config()
    assert configured.call_args.args == ("vox", "daemon")


@pytest.fixture
def lifecycle_logging() -> Iterator[MagicMock]:
    """Keep caplog's root handler: the real setup would replace it."""
    with patch.object(mcp_lifecycle, "configure_client_logging") as configure:
        yield configure


def _lifecycle_records(caplog: pytest.LogCaptureFixture) -> list[tuple[int, str]]:
    return [
        (r.levelno, r.getMessage())
        for r in caplog.records
        if r.name == "punt_vox.mcp_lifecycle"
    ]


def test_mcp_lifecycle_records_a_clean_stop(
    lifecycle_logging: MagicMock, caplog: pytest.LogCaptureFixture
) -> None:
    with (
        patch.object(server, "run_server") as run_server,
        caplog.at_level(logging.INFO),
    ):
        mcp_lifecycle.McpLifecycle().run()
    run_server.assert_called_once_with()
    lifecycle_logging.assert_called_once_with(role="mcp")
    assert _lifecycle_records(caplog) == [
        (logging.INFO, "vox MCP server stopped: outcome=completed")
    ]


@pytest.mark.usefixtures("lifecycle_logging")
def test_mcp_lifecycle_records_a_server_failure(
    caplog: pytest.LogCaptureFixture,
) -> None:
    boom = RuntimeError("SYNTHETIC-PRIVATE-DETAIL")
    with (
        patch.object(server, "run_server", side_effect=boom),
        caplog.at_level(logging.INFO),
        pytest.raises(RuntimeError),
    ):
        mcp_lifecycle.McpLifecycle().run()
    assert _lifecycle_records(caplog) == [
        (logging.ERROR, "vox MCP server stopped: outcome=failed reason=RuntimeError")
    ]
    assert "SYNTHETIC-PRIVATE-DETAIL" not in caplog.text


@pytest.mark.usefixtures("lifecycle_logging")
def test_mcp_lifecycle_records_an_incompatible_sdk(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # ``None`` in sys.modules makes the import raise, as mcp 2.x's removed
    # ``mcp.server.fastmcp`` does on a mis-resolved install.
    with (
        patch.dict(sys.modules, {"punt_vox.server": None}),
        caplog.at_level(logging.INFO),
        pytest.raises(ImportError),
    ):
        mcp_lifecycle.McpLifecycle().run()
    assert _lifecycle_records(caplog) == [
        (
            logging.ERROR,
            (
                "vox MCP server failed to start: outcome=failed "
                "reason=import_error module=punt_vox.server"
            ),
        )
    ]
