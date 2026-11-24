"""Own process logging policy over punt-observe's private shared sinks."""

from __future__ import annotations

import logging
import sys
from contextlib import suppress
from typing import TYPE_CHECKING

from punt_observe.logging import AtomicAppendLog, configure_logging

from punt_vox.config import ConfigStore
from punt_vox.logging_role import ClientRoleFilter, Role as Role
from punt_vox.paths import log_dir

if TYPE_CHECKING:
    from punt_observe.logging.append_log import SinkHealth

_SUPPRESSED = (
    "boto3", "botocore", "urllib3", "s3transfer", "httpx", "httpx2",
    "websockets", "mcp", "mcp.server", "mcp.server.lowlevel", "asyncio", "httpcore",
)  # fmt: skip


def configure_daemon_logging() -> None:
    """Configure the daemon's durable file without a service-manager copy."""
    configure_logging("vox", "daemon", level=_resolve_level(), quiet=_SUPPRESSED)
    root = logging.getLogger()
    stderr = logging.getHandlerByName("stderr")
    if stderr is not None:
        root.removeHandler(stderr)
        stderr.close()
    logging.lastResort = logging.NullHandler()


def configure_client_logging(*, role: Role, verbose: bool = False) -> None:
    """Configure a client with its role and effective file verbosity."""
    with suppress(OSError):
        configure_logging(
            "vox", role, level=_resolve_level(verbose=verbose), quiet=_SUPPRESSED
        )
        for handler in logging.getLogger().handlers:
            handler.addFilter(ClientRoleFilter(role))
        return
    with suppress(OSError):
        _file_handler().close()
    logging.getLogger().handlers[:] = [logging.NullHandler()]
    _warn_logging_unavailable()


def log_health() -> SinkHealth:
    """Return the shared diagnostic file's observable sink health."""
    return AtomicAppendLog(log_dir() / "vox.log").health()


def reapply_client_log_level() -> None:
    """Re-read the config for a long-lived client's durable handler."""
    apply_log_level(logging.getLevelNamesMapping()[_resolve_level()])


def apply_log_level(numeric: int) -> None:
    """Admit DEBUG records at root, leaving output policy to each handler."""
    logging.getLogger().setLevel(logging.DEBUG)
    _file_handler().setLevel(numeric)


def daemon_log_level() -> str | None:
    """Return the live file logging threshold, or None without a file handler."""
    handler = logging.getHandlerByName("file")
    return logging.getLevelName(handler.level).lower() if handler else None


def _file_handler() -> logging.Handler:
    """Return the named durable handler installed by shared configuration."""
    return logging.getHandlerByName("file") or logging.NullHandler()


def _warn_logging_unavailable() -> None:
    """Emit only fixed metadata when private logging setup fails."""
    with suppress(OSError, ValueError, AttributeError):
        sys.stderr.write("vox: private logging unavailable; continuing without file\n")


def _resolve_level(*, verbose: bool = False) -> str:
    """Resolve CLI verbosity over the durable config setting."""
    return "DEBUG" if verbose else ConfigStore.resolve_log_level().upper()
