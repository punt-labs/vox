"""The single logging owner: one daemon file config, one client ship config.

The daemon is the sole writer of the durable ``vox.log``; every client process
(MCP server, hook, CLI, detached playback) ships its records to the daemon and
holds no file handler of its own. This module builds both ends from shared
constants -- the one format, the third-party + ``mcp`` framework suppression
table, the max-bytes/backup rotation -- so the two configurations can never drift.

Both entry points first tighten ``~/.punt-labs/vox`` and ``…/logs`` to 0700 via
:class:`PrivateState`, so a client that never ran ``ensure_user_dirs`` still
re-tightens a pre-existing loose directory. Client tightening is best-effort
(never crash a hook); the daemon additionally fail-closes in ``main`` before this.

The active level follows the ``log_level`` config key (default INFO): a client
re-reads it each run, so ``/vox log debug`` applies immediately; the daemon reads
it at startup, so a running daemon picks up a change on ``vox daemon restart``.
"""

from __future__ import annotations

import logging
import logging.config
from pathlib import Path

from punt_vox.config import ConfigStore, find_config_dir
from punt_vox.log_handlers import PrivateRotatingFileHandler
from punt_vox.log_wire import LOG_DATE_FORMAT, LOG_FORMAT, Role
from punt_vox.paths import log_dir as _paths_log_dir
from punt_vox.private_state import PrivateState

__all__ = ["Role", "configure_client_logging", "configure_daemon_logging"]

logger = logging.getLogger(__name__)

_LOG_DIR = _paths_log_dir()
_LOG_FILE = _LOG_DIR / "vox.log"
_FALLBACK_FILE = _LOG_DIR / "vox-fallback.log"

_MAX_BYTES = 5_242_880  # 5 MB
_BACKUP_COUNT = 5

_FILE_HANDLER_FACTORY = "punt_vox.log_handlers.PrivateRotatingFileHandler.from_config"
_SHIP_HANDLER_FACTORY = "punt_vox.log_ship.LogShipper.build_handler"
# Escape the final formatted line so no field (a client-shipped name, a provider
# error body) can forge a second physical line in vox.log.
_FORMATTER_CLASS = "punt_vox.log_sanitize.SanitizingFormatter"

# Third-party and mcp-framework loggers pinned to WARNING so vox owns the INFO
# surface -- notably the 38x "Processing request of type CallToolRequest" noise.
_SUPPRESSED: tuple[str, ...] = (
    "boto3",
    "botocore",
    "urllib3",
    "s3transfer",
    "httpx",
    "websockets",
    "mcp",
    "mcp.server",
    "mcp.server.lowlevel",
)


def configure_daemon_logging() -> None:
    """Configure the daemon as the single writer of the 0600 ``vox.log``.

    One private rotating file handler, no stderr handler: a parallel
    ``StreamHandler`` would have the service manager capture a second, unprotected
    copy of every record. Re-tightens the log tree first, then sweeps for any
    pre-existing file it could not force to 0600.
    """
    _ensure_tree(_LOG_FILE, best_effort=False)
    level = _resolve_level(verbose=False)
    logging.config.dictConfig(
        {
            "version": 1,
            "disable_existing_loggers": False,
            "formatters": {
                "standard": {
                    "class": _FORMATTER_CLASS,
                    "format": LOG_FORMAT,
                    "datefmt": LOG_DATE_FORMAT,
                },
            },
            "handlers": {
                "file": {
                    "()": _FILE_HANDLER_FACTORY,
                    "filename": str(_LOG_FILE),
                    "maxBytes": _MAX_BYTES,
                    "backupCount": _BACKUP_COUNT,
                    "encoding": "utf-8",
                    "formatter": "standard",
                    "level": level,
                },
            },
            "root": {"level": level, "handlers": ["file"]},
            "loggers": _suppression_table(),
        }
    )
    PrivateRotatingFileHandler.warn_untightened(logger)


def configure_client_logging(*, role: Role, verbose: bool = False) -> None:
    """Configure a client to ship its records to ``voxd``; no local file handler.

    The root logger's one handler buffers each record and drains it over the
    WebSocket the client already opens. ``verbose`` (the CLI ``--verbose``) is a
    one-shot ``debug``; otherwise the level follows the ``log_level`` config key.
    """
    _ensure_tree(_FALLBACK_FILE, best_effort=True)
    level = _resolve_level(verbose=verbose)
    logging.config.dictConfig(
        {
            "version": 1,
            "disable_existing_loggers": False,
            "handlers": {
                "ship": {
                    "()": _SHIP_HANDLER_FACTORY,
                    "role": role,
                    "level": level,
                },
            },
            "root": {"level": level, "handlers": ["ship"]},
            "loggers": _suppression_table(),
        }
    )


def _suppression_table() -> dict[str, dict[str, str]]:
    """Return the shared third-party + framework logger level overrides."""
    return {name: {"level": "WARNING"} for name in _SUPPRESSED}


def _resolve_level(*, verbose: bool) -> str:
    """Return the active level: DEBUG when verbose or configured, else INFO."""
    if verbose:
        return "DEBUG"
    return _config_level()


def _config_level() -> str:
    """Read ``log_level`` from config; DEBUG only when explicitly set, else INFO.

    An absent or unrecognised value is the quiet default. Reading config needs no
    logging configured, so this is safe to call before the handlers exist.
    """
    raw = ConfigStore(find_config_dir()).read_field("log_level")
    return "DEBUG" if (raw or "").strip().lower() == "debug" else "INFO"


def _ensure_tree(anchor: Path, *, best_effort: bool) -> None:
    """Create and re-tighten the log directory tree to 0700 (djb re-tighten).

    The daemon fails closed on a tree it cannot secure; a client never lets a
    tightening failure crash a hook -- privacy here is defense-in-depth, not a
    precondition for logging.
    """
    guard = PrivateState(anchor)
    if not best_effort:
        guard.ensure_private_tree()
        return
    try:
        guard.ensure_private_tree()
    except OSError as exc:
        logger.debug("client log-dir tighten skipped: %s", exc)
