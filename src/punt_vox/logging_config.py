"""Own process logging policy over punt-observe's private shared sinks."""

from __future__ import annotations

import logging
import sys
from typing import TYPE_CHECKING, cast

from punt_observe.logging import AtomicAppendLog, configure_logging
from punt_observe.logging.formatter import RedactingFormatter
from punt_observe.logging.handler import PrivateFileHandler, RedactingStreamHandler

from punt_vox.config import ConfigStore
from punt_vox.logging_role import ClientRoleFilter, Role as Role
from punt_vox.paths import log_dir

if TYPE_CHECKING:
    from punt_observe.logging.append_log import SinkHealth

_TURN_TIMER_LOGGER_NAME = "punt_vox.voxd.conversation_mode.turn_timer"
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
    configure_logging(
        "vox", role, level=_resolve_level(verbose=verbose), quiet=_SUPPRESSED
    )
    for handler in logging.getLogger().handlers:
        handler.addFilter(ClientRoleFilter(role))


def configure_turn_timer_logging(*, echo_to_console: bool) -> None:
    """Keep turn timings durable at DEBUG independently of normal verbosity."""
    logger = _reset_turn_timer()
    handlers: list[logging.Handler] = [PrivateFileHandler(log_dir() / "vox.log")]
    if echo_to_console:
        handlers.append(RedactingStreamHandler(sys.stderr))
    for handler in handlers:
        handler.setLevel(logging.DEBUG)
        handler.setFormatter(
            RedactingFormatter(
                "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
                "%Y-%m-%d %H:%M:%S",
            )
        )
        handler.addFilter(ClientRoleFilter("cli"))
        logger.addHandler(handler)


def _reset_turn_timer() -> logging.Logger:
    """Reset the dedicated timer's handlers without leaking resources."""
    logger = logging.getLogger(_TURN_TIMER_LOGGER_NAME)
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    for handler in logger.handlers:
        handler.close()
    logger.handlers.clear()
    return logger


def log_health() -> SinkHealth:
    """Return the shared diagnostic file's observable sink health."""
    return AtomicAppendLog(log_dir() / "vox.log").health()


def reapply_client_log_level() -> None:
    """Re-read the config for a long-lived client's durable handler."""
    apply_log_level(logging.getLevelNamesMapping()[_resolve_level()])


def apply_log_level(numeric: int) -> None:
    """Change only the durable root file handler's threshold."""
    _file_handler().setLevel(numeric)


def _file_handler() -> PrivateFileHandler:
    """Return the named durable handler installed by shared configuration."""
    return cast("PrivateFileHandler", logging.getHandlerByName("file"))


def _resolve_level(*, verbose: bool = False) -> str:
    """Resolve CLI verbosity over the durable config setting."""
    return "DEBUG" if verbose else ConfigStore.resolve_log_level().upper()
