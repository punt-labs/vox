"""Daemon configuration: paths, keys, logging, auth tokens, port files."""
# pyright: reportUnusedFunction=false
# All functions here are re-exported via __init__.py and used by _monolith.py.

from __future__ import annotations

import logging
import logging.config
import os
import re
import secrets
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Self, cast

from punt_vox.keys import PROVIDER_KEY_NAMES
from punt_vox.log_handlers import PrivateRotatingFileHandler
from punt_vox.paths import (
    config_dir as _user_config_dir,
    log_dir as _user_log_dir,
    run_dir as _user_run_dir,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Per-user state paths
#
# Thin wrappers over ``punt_vox.paths`` so tests can monkey-patch them
# without reaching across modules.  The source of truth is
# ``punt_vox.paths``; every path resolves to a subdirectory of
# ``~/.punt-labs/vox/`` -- same on macOS and Linux.
# ---------------------------------------------------------------------------


def _config_dir() -> Path:
    """Return directory holding ``keys.env``.

    Pure path resolution -- no ``mkdir``, no ``chmod``.  ``main()``
    calls :func:`punt_vox.paths.ensure_user_dirs` at startup, which
    creates every per-user subdirectory with mode 0700 (and tightens
    dirs that were created under a looser umask).
    """
    return _user_config_dir()


def _log_dir() -> Path:
    """Return directory holding ``voxd.log`` and rotated logs.

    Pure path resolution -- see :func:`_config_dir`.
    """
    return _user_log_dir()


def _run_dir() -> Path:
    """Return directory holding ``serve.port`` and ``serve.token``.

    Pure path resolution -- see :func:`_config_dir`.
    """
    return _user_run_dir()


# ---------------------------------------------------------------------------
# Logging constants and helpers
# ---------------------------------------------------------------------------

_LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
_LOG_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"
_LOG_MAX_BYTES = 5_242_880  # 5 MB
_LOG_BACKUP_COUNT = 5

# dictConfig "()" factory that re-tightens pre-existing 0644 files at startup.
_LOG_HANDLER_FACTORY = "punt_vox.log_handlers.PrivateRotatingFileHandler.from_config"

_STARTUP_ENV_KEYS: tuple[str, ...] = (
    "PATH",
    "XDG_RUNTIME_DIR",
    "PULSE_SERVER",
    "DBUS_SESSION_BUS_ADDRESS",
    "DISPLAY",
    "WAYLAND_DISPLAY",
    "HOME",
    "USER",
    "LANG",
)

_TOKEN_RE = re.compile(r"\?token=[^\s\"']+")


class _TokenRedactFilter(logging.Filter):
    """Strip auth tokens from access log messages."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(getattr(record, "msg", None), str):
            record.msg = _TOKEN_RE.sub("?token=REDACTED", record.msg)
        if record.args and isinstance(record.args, tuple):
            record.args = tuple(
                _TOKEN_RE.sub("?token=REDACTED", a) if isinstance(a, str) else a
                for a in record.args
            )
        return True


def _install_token_redact_filter() -> None:
    """Apply token redaction to uvicorn's access logger.

    Uvicorn sets the access logger level to match log_level (WARNING),
    but access entries are logged at INFO.  Override to INFO so access
    logs actually fire, with the redact filter stripping tokens.
    """
    uvicorn_access = logging.getLogger("uvicorn.access")
    uvicorn_access.setLevel(logging.INFO)
    uvicorn_access.addFilter(_TokenRedactFilter())


# ---------------------------------------------------------------------------
# DaemonConfig -- groups path dirs, key loading, logging, and auth
# ---------------------------------------------------------------------------


class DaemonConfig:
    """Encapsulate daemon filesystem paths, key loading, logging, and auth."""

    __slots__ = ("_auth_token", "_config_dir", "_log_dir", "_run_dir")

    _run_dir: Path
    _config_dir: Path
    _log_dir: Path
    _auth_token: str | None

    def __new__(cls, run_dir: Path, config_dir: Path, log_dir: Path) -> Self:
        self = super().__new__(cls)
        self._run_dir = run_dir
        self._config_dir = config_dir
        self._log_dir = log_dir
        self._auth_token = None
        return self

    # -- read-only properties ------------------------------------------------

    @property
    def run_dir(self) -> Path:
        """Return the run directory path."""
        return self._run_dir

    @property
    def config_dir(self) -> Path:
        """Return the config directory path."""
        return self._config_dir

    @property
    def log_dir(self) -> Path:
        """Return the log directory path."""
        return self._log_dir

    @property
    def auth_token(self) -> str | None:
        """Return the auth token, or None if not yet loaded."""
        return self._auth_token

    # -- instance methods (use self._*_dir) ----------------------------------

    def load_keys(self) -> frozenset[str]:
        """Load keys.env from config dir into os.environ.

        Return the names of variables that were loaded.
        """
        keys_file = self._config_dir / "keys.env"
        if not keys_file.exists():
            return frozenset()
        try:
            text = keys_file.read_text()
        except OSError as exc:
            logger.warning(
                "Could not read %s: %s -- daemon will use system TTS only",
                keys_file,
                exc,
            )
            return frozenset()
        loaded: set[str] = set()
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if "=" not in stripped:
                continue
            key, _, value = stripped.partition("=")
            key = key.strip()
            value = value.strip()
            if key in PROVIDER_KEY_NAMES and value and key not in os.environ:
                os.environ[key] = value
                loaded.add(key)
        return frozenset(loaded)

    def read_or_create_token(self) -> str:
        """Read auth token from run dir, or generate a new one.

        Stores the result in ``self._auth_token`` for later access.
        """
        token_file = self._run_dir / "serve.token"
        if token_file.exists():
            try:
                token = token_file.read_text().strip()
            except (PermissionError, OSError) as exc:
                msg = (
                    f"Cannot read auth token from {token_file}: {exc}. "
                    "Fix file permissions or remove the file."
                )
                raise SystemExit(msg) from exc
            if not token:
                msg = f"Auth token file {token_file} is empty. Remove it to regenerate."
                raise SystemExit(msg)
            token_file.chmod(0o600)
            logger.info("Loaded auth token from %s", token_file)
            self._auth_token = token
            return token

        token = secrets.token_urlsafe(32)
        # The parent run dir is guaranteed to exist at mode 0700 by
        # ``ensure_user_dirs`` at the top of ``main()``.
        fd = os.open(str(token_file), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.write(fd, token.encode())
        finally:
            os.close(fd)
        logger.info("Generated auth token at %s", token_file)
        self._auth_token = token
        return token

    def write_port_file(self, port: int) -> None:
        """Write the daemon port to the run directory."""
        port_file = self._run_dir / "serve.port"
        port_file.write_text(str(port))
        logger.info("Wrote port file: %s (port %d)", port_file, port)

    def remove_port_file(self) -> None:
        """Remove the port file from the run directory."""
        port_file = self._run_dir / "serve.port"
        try:
            port_file.unlink(missing_ok=True)
        except OSError:
            logger.warning("Could not remove %s", port_file)
        logger.info("Removed port file")

    def configure_logging(self) -> None:
        """Configure logging with a single private rotating-file handler.

        The daemon logs *once*, to the 0600 ``voxd.log`` -- no stderr handler.
        A parallel ``StreamHandler`` would have the service manager (launchd's
        ``StandardErrorPath``, systemd's journal) capture a second, unprotected
        copy of the same records, defeating the file's private permissions.

        The log directory is expected to already exist at mode 0700.
        """
        log_file = self._log_dir / "voxd.log"

        logging.config.dictConfig(
            {
                "version": 1,
                "disable_existing_loggers": False,
                "formatters": {
                    "standard": {
                        "format": _LOG_FORMAT,
                        "datefmt": _LOG_DATE_FORMAT,
                    },
                },
                "handlers": {
                    "file": {
                        "()": _LOG_HANDLER_FACTORY,
                        "filename": str(log_file),
                        "maxBytes": _LOG_MAX_BYTES,
                        "backupCount": _LOG_BACKUP_COUNT,
                        "encoding": "utf-8",
                        "formatter": "standard",
                        "level": "INFO",
                    },
                },
                "root": {
                    "level": "INFO",
                    "handlers": ["file"],
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
        self._warn_on_loose_logs()

    @staticmethod
    def _warn_on_loose_logs() -> None:
        """Emit one WARNING per handler, naming every log file it left un-tightened.

        Run *after* ``dictConfig`` so the record lands in the now-live file
        handler (durable and greppable, unlike discarded stderr) and cannot
        recurse into a mid-rollover tighten -- the handler is attached and idle.
        """
        for handler in logging.getLogger().handlers:
            if isinstance(handler, PrivateRotatingFileHandler) and (
                handler.tighten_failures
            ):
                loose = ", ".join(map(str, handler.tighten_failures))
                logger.warning("could not enforce 0600 on log file(s): %s", loose)

    def log_environment(self) -> None:
        """Log voxd's process identity and audio env vars at startup."""
        env = {k: os.environ.get(k, "<unset>") for k in _STARTUP_ENV_KEYS}
        getuid = cast("Callable[[], int] | None", getattr(os, "getuid", None))
        getgid = cast("Callable[[], int] | None", getattr(os, "getgid", None))
        uid: int | str = getuid() if getuid is not None else "<n/a>"
        gid: int | str = getgid() if getgid is not None else "<n/a>"
        logger.info(
            "voxd environment: pid=%d uid=%s gid=%s cwd=%s "
            "voxd_binary=%s voxd_module=%s env=%s",
            os.getpid(),
            uid,
            gid,
            Path.cwd(),
            sys.executable,
            __file__,
            env,
        )

    # -- class methods (no instance needed) ----------------------------------

    @classmethod
    def read_port_file(cls, run_dir: Path) -> int | None:
        """Read the daemon port from the port file.  Return None if missing."""
        port_file = run_dir / "serve.port"
        try:
            return int(port_file.read_text().strip())
        except (FileNotFoundError, ValueError, OSError):
            return None

    @classmethod
    def read_token_file(cls, run_dir: Path) -> str | None:
        """Read the daemon auth token.  Return None if missing."""
        token_file = run_dir / "serve.token"
        try:
            return token_file.read_text().strip()
        except (FileNotFoundError, OSError):
            return None
