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

from punt_vox import hooks, logging_config, server
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
