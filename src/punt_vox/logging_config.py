"""Logging configuration for punt-vox."""

from __future__ import annotations

import logging
import logging.config

from punt_vox.log_handlers import PrivateRotatingFileHandler
from punt_vox.paths import log_dir as _paths_log_dir

logger = logging.getLogger(__name__)

_LOG_DIR = _paths_log_dir()
_LOG_FILE = _LOG_DIR / "tts.log"

_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

_MAX_BYTES = 5_242_880  # 5 MB
_BACKUP_COUNT = 5

# dictConfig "()" factory that re-tightens pre-existing 0644 files at startup.
_HANDLER_FACTORY = "punt_vox.log_handlers.PrivateRotatingFileHandler.from_config"


def configure_logging(*, stderr_level: str = "WARNING") -> None:
    """Configure logging with rotating file and stderr handlers.

    File handler is always active at INFO level.
    Stderr handler level is controlled by the caller.
    """
    levels = logging.getLevelNamesMapping()
    normalized_level = stderr_level.upper()
    if normalized_level not in levels:
        valid = ", ".join(sorted(levels))
        msg = f"unknown stderr_level {stderr_level!r}; valid levels: {valid}"
        raise ValueError(msg)
    root_level = min(normalized_level, "INFO", key=levels.__getitem__)

    _LOG_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)

    logging.config.dictConfig(
        {
            "version": 1,
            "disable_existing_loggers": False,
            "formatters": {
                "standard": {
                    "format": _FORMAT,
                    "datefmt": _DATE_FORMAT,
                },
            },
            "handlers": {
                "file": {
                    "()": _HANDLER_FACTORY,
                    "filename": str(_LOG_FILE),
                    "maxBytes": _MAX_BYTES,
                    "backupCount": _BACKUP_COUNT,
                    "encoding": "utf-8",
                    "formatter": "standard",
                    "level": "INFO",
                },
                "stderr": {
                    "class": "logging.StreamHandler",
                    "stream": "ext://sys.stderr",
                    "formatter": "standard",
                    "level": normalized_level,
                },
            },
            "root": {
                "level": root_level,
                "handlers": ["file", "stderr"],
            },
            "loggers": {
                "boto3": {"level": "WARNING"},
                "botocore": {"level": "WARNING"},
                "urllib3": {"level": "WARNING"},
                "s3transfer": {"level": "WARNING"},
                "httpx": {"level": "WARNING"},
            },
        }
    )
    PrivateRotatingFileHandler.warn_untightened(logger)
