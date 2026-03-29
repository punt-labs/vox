"""ASGI HTTP + WebSocket server for the vox daemon.

Exposes three endpoints:
- ``/mcp`` — MCP-over-WebSocket for mcp-proxy (per-session)
- ``/hook`` — Hook relay for mcp-proxy ``--hook`` mode
- ``/health`` — JSON health check

Lifecycle:
    1. ``vox serve`` starts uvicorn with the Starlette app
    2. Writes port to ``~/.punt-labs/vox/serve.port``
    3. Accepts MCP and hook connections on ``localhost:<port>``
    4. Cleans up port file on shutdown
"""

from __future__ import annotations

import asyncio
import hmac
import json
import logging
import os
import platform
import re
import secrets
import subprocess
import time
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from socket import socket
from typing import TYPE_CHECKING

import uvicorn
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route, WebSocketRoute

from punt_vox.config import (
    _config_path_override,  # pyright: ignore[reportPrivateUsage]
    read_config,
)
from punt_vox.hooks import (
    handle_notification,
    handle_post_bash,
    handle_pre_compact,
    handle_session_end,
    handle_stop,
    handle_subagent_start,
    handle_subagent_stop,
    handle_user_prompt_submit,
)
from punt_vox.keys import keys_file_path, load_keys_env
from punt_vox.logging_config import VOX_DATA_DIR, configure_logging

if TYPE_CHECKING:
    from starlette.requests import Request
    from starlette.websockets import WebSocket

logger = logging.getLogger(__name__)

DEFAULT_PORT = 8421

# Strip control characters from user-supplied log values (CWE-117).
_CONTROL_CHAR_RE = re.compile(r"[\x00-\x1f\x7f]")

# Audio deduplication window: skip identical audio within this many seconds.
_DEDUP_WINDOW_SECONDS = 5.0


# ---------------------------------------------------------------------------
# Daemon context
# ---------------------------------------------------------------------------


@dataclass
class SessionInfo:
    """Per-session state tracked by the daemon."""

    session_key: str
    config_path: Path
    connected_at: float = field(default_factory=time.monotonic)


class DaemonContext:
    """Shared mutable state for the daemon process."""

    def __init__(self, *, auth_token: str | None = None) -> None:
        self.start_time: float = time.monotonic()
        self.sessions: dict[str, SessionInfo] = {}
        self._dedup: dict[str, float] = {}
        self.auth_token: str | None = auth_token

    def should_play(self, cache_key: str) -> bool:
        """Return True if this audio should play (dedup check).

        Prevents duplicate audio when multiple sessions receive the
        same notification (e.g. biff wall).
        """
        now = time.monotonic()
        last = self._dedup.get(cache_key)
        if last is not None and (now - last) < _DEDUP_WINDOW_SECONDS:
            return False
        self._dedup[cache_key] = now
        # Prune old entries
        cutoff = now - _DEDUP_WINDOW_SECONDS * 2
        self._dedup = {k: v for k, v in self._dedup.items() if v > cutoff}
        return True

    def register_session(self, session_key: str, config_path: Path) -> SessionInfo:
        """Register a new MCP session."""
        info = SessionInfo(session_key=session_key, config_path=config_path)
        self.sessions[session_key] = info
        logger.info("Session registered: key=%s config=%s", session_key, config_path)
        return info

    def remove_session(self, session_key: str) -> None:
        """Remove a session from the registry."""
        self.sessions.pop(session_key, None)
        logger.info("Session removed: key=%s", session_key)


# ---------------------------------------------------------------------------
# CWD resolution from PID
# ---------------------------------------------------------------------------


def resolve_cwd_from_pid(pid: int) -> Path | None:
    """Resolve the current working directory of a process by PID.

    Uses ``lsof`` on macOS and ``/proc/<pid>/cwd`` on Linux.
    Returns None if resolution fails.
    """
    system = platform.system()
    if system == "Linux":
        link = Path(f"/proc/{pid}/cwd")
        try:
            target = link.readlink()
            if target.is_dir():
                return target
        except OSError:
            logger.debug("Failed to readlink /proc/%d/cwd", pid, exc_info=True)
        return None

    if system == "Darwin":
        try:
            result = subprocess.run(
                ["lsof", "-a", "-p", str(pid), "-d", "cwd", "-Fn"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            for line in result.stdout.splitlines():
                if line.startswith("n"):
                    return Path(line[1:])
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            logger.debug(
                "Failed to resolve CWD for PID %d via lsof", pid, exc_info=True
            )

    return None


def _resolve_config_from_session_key(session_key: str) -> Path | None:
    """Resolve .vox/config.md from a session key (PID).

    Looks up the PID's cwd and checks for .vox/config.md there,
    falling back to git common dir for worktree support.
    """
    try:
        pid = int(session_key)
    except (ValueError, TypeError):
        logger.debug("Non-numeric session key: %s", session_key)
        return None

    cwd = resolve_cwd_from_pid(pid)
    if cwd is None:
        return None

    config = cwd / ".vox" / "config.md"
    if config.exists():
        return config

    # Try git common dir for worktree support
    try:
        result = subprocess.run(
            ["git", "-C", str(cwd), "rev-parse", "--git-common-dir"],
            capture_output=True,
            text=True,
            check=True,
            timeout=5,
        )
        git_common = result.stdout.strip()
        if git_common:
            # git rev-parse may return a relative path (e.g. ".git");
            # resolve relative to the session's cwd, not daemon's.
            repo_root = (cwd / Path(git_common)).resolve().parent
            config = repo_root / ".vox" / "config.md"
            if config.exists():
                return config
    except (
        subprocess.CalledProcessError,
        FileNotFoundError,
        subprocess.TimeoutExpired,
    ):
        pass

    return None


# ---------------------------------------------------------------------------
# Hook dispatch
# ---------------------------------------------------------------------------


def _dispatch_hook(
    event: str,
    params: dict[str, object],
    config_path: Path,
) -> dict[str, object] | None:
    """Dispatch a hook event to the appropriate handler.

    Returns a JSON-RPC result dict for sync hooks, or None for async.
    """
    config = read_config(config_path)

    if event == "Stop":
        return handle_stop(params, config)

    if event == "PostToolUse":
        handle_post_bash(params, config_path)
        return None

    if event == "Notification":
        handle_notification(params, config)
        return None

    if event == "PreCompact":
        handle_pre_compact(config)
        return None

    if event == "UserPromptSubmit":
        handle_user_prompt_submit(config)
        return None

    if event == "SubagentStart":
        handle_subagent_start(config)
        return None

    if event == "SubagentStop":
        handle_subagent_stop(config)
        return None

    if event == "SessionEnd":
        handle_session_end(config, config_path)
        return None

    logger.warning("Unknown hook event: %s", event)
    return None


# ---------------------------------------------------------------------------
# Auth helper
# ---------------------------------------------------------------------------


def _check_auth(websocket: WebSocket, ctx: DaemonContext) -> bool:
    """Verify the auth token from query params.  Returns True if valid."""
    if ctx.auth_token is None:
        return True  # No auth configured (tests)
    token = websocket.query_params.get("token", "")
    return hmac.compare_digest(token, ctx.auth_token)


# ---------------------------------------------------------------------------
# WebSocket routes
# ---------------------------------------------------------------------------


async def _mcp_websocket_route(websocket: WebSocket) -> None:
    """MCP JSON-RPC over WebSocket for mcp-proxy."""
    from mcp.server.websocket import websocket_server

    from punt_vox.server import run_mcp_session

    ctx: DaemonContext = websocket.app.state.ctx

    if not _check_auth(websocket, ctx):
        await websocket.close(code=1008)
        return

    raw_key = websocket.query_params.get("session_key", "unknown")
    session_key = _CONTROL_CHAR_RE.sub("", raw_key)[:64]
    logger.info("MCP WebSocket connected: session_key=%s", session_key)

    # Resolve config path from session PID
    config_path = _resolve_config_from_session_key(session_key)
    if config_path is not None:
        ctx.register_session(session_key, config_path)
        token = _config_path_override.set(config_path)
    else:
        token = None
        logger.warning(
            "Could not resolve config for session_key=%s, using default",
            session_key,
        )

    try:
        async with websocket_server(
            websocket.scope, websocket.receive, websocket.send
        ) as (read_stream, write_stream):
            await run_mcp_session(read_stream, write_stream)
    except Exception:
        logger.exception("MCP WebSocket error: session_key=%s", session_key)
    finally:
        if token is not None:
            _config_path_override.reset(token)
        ctx.remove_session(session_key)
        logger.info("MCP WebSocket disconnected: session_key=%s", session_key)


async def _hook_websocket_route(websocket: WebSocket) -> None:
    """Hook relay endpoint for mcp-proxy ``--hook`` mode.

    Receives JSON-RPC messages, dispatches to hook handlers, and
    returns results for sync hooks.
    """
    ctx: DaemonContext = websocket.app.state.ctx

    if not _check_auth(websocket, ctx):
        await websocket.close(code=1008)
        return

    await websocket.accept()

    msg_id: object = None
    try:
        raw = await websocket.receive_text()
        msg = json.loads(raw)

        method: str = msg.get("method", "")
        msg_id = msg.get("id")
        raw_params: object = msg.get("params", {})
        params: dict[str, object] = {}
        if isinstance(raw_params, dict):
            for k, v in raw_params.items():  # pyright: ignore[reportUnknownVariableType]
                params[str(k)] = v  # pyright: ignore[reportUnknownArgumentType]

        # Extract event name from method: "hook/Stop" → "Stop"
        event = method.removeprefix("hook/") if method.startswith("hook/") else method

        # Resolve config path from query params.
        # Hook scripts pass config_dir (the repo root where .vox/config.md lives).
        # MCP sessions pass session_key (PID) for registry lookup.
        raw_config_dir = websocket.query_params.get("config_dir", "")
        raw_key = websocket.query_params.get("session_key", "")
        session_key = _CONTROL_CHAR_RE.sub("", raw_key)[:64]

        config_path: Path | None = None
        if raw_config_dir:
            # Canonicalize to prevent path traversal and symlink abuse.
            try:
                canon = Path(raw_config_dir).resolve(strict=True)
            except OSError:
                canon = None
            if canon is not None and canon.is_dir():
                candidate = canon / ".vox" / "config.md"
                if candidate.exists() and not candidate.is_symlink():
                    config_path = candidate
                else:
                    logger.debug(
                        "Hook config_dir=%s: config not found or symlink",
                        raw_config_dir,
                    )
            else:
                logger.debug(
                    "Hook config_dir=%s: not a valid directory",
                    raw_config_dir,
                )
        elif session_key and session_key in ctx.sessions:
            config_path = ctx.sessions[session_key].config_path
        elif session_key:
            config_path = _resolve_config_from_session_key(session_key)

        if config_path is None:
            logger.debug(
                "Hook %s: no config resolved (config_dir=%r, session_key=%r)",
                event,
                raw_config_dir,
                session_key,
            )
            # No config = vox not enabled for this project
            if msg_id is not None:
                response: dict[str, object] = {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "result": None,
                }
                await websocket.send_text(json.dumps(response))
            return

        if not config_path.exists():
            logger.debug(
                "Hook %s skipped: config not found at %s",
                event,
                config_path,
            )
            if msg_id is not None:
                response = {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "result": None,
                }
                await websocket.send_text(json.dumps(response))
            return

        # Audio dedup for notifications — checked on the event loop
        # thread (single-threaded, no race) before dispatching to
        # a worker thread.
        if event == "Notification":
            notification_type = params.get("notification_type", "")
            dedup_key = f"notification:{notification_type}"
            if not ctx.should_play(dedup_key):
                logger.debug("Notification dedup: skipping %s", dedup_key)
                if msg_id is not None:
                    response = {
                        "jsonrpc": "2.0",
                        "id": msg_id,
                        "result": None,
                    }
                    await websocket.send_text(json.dumps(response))
                return

        # Set ContextVar for this hook dispatch
        token = _config_path_override.set(config_path)
        try:
            result = await asyncio.to_thread(_dispatch_hook, event, params, config_path)
        finally:
            _config_path_override.reset(token)

        # Sync hook (has id): send response
        if msg_id is not None:
            response = {"jsonrpc": "2.0", "id": msg_id, "result": result}
            await websocket.send_text(json.dumps(response))

    except Exception:
        logger.exception("Hook WebSocket error")
        # Send JSON-RPC error response for sync hooks so mcp-proxy
        # doesn't hang waiting for a response that never arrives.
        try:
            if msg_id is not None:
                error_resp: dict[str, object] = {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "error": {"code": -32603, "message": "Internal error"},
                }
                await websocket.send_text(json.dumps(error_resp))
        except Exception:
            pass  # WebSocket may already be closed
    finally:
        await websocket.close()


# ---------------------------------------------------------------------------
# HTTP routes
# ---------------------------------------------------------------------------


async def _health_route(request: Request) -> JSONResponse:
    """Health check endpoint."""
    ctx: DaemonContext = request.app.state.ctx
    uptime = time.monotonic() - ctx.start_time
    return JSONResponse(
        {
            "status": "ok",
            "uptime_seconds": round(uptime, 1),
            "active_sessions": len(ctx.sessions),
        }
    )


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------


def build_app(
    ctx: DaemonContext | None = None,
    *,
    lifespan: Callable[[Starlette], AbstractAsyncContextManager[None]] | None = None,
) -> Starlette:
    """Build the Starlette ASGI application.

    Exposed as a factory so tests can construct the app without starting
    uvicorn.
    """
    if ctx is None:
        ctx = DaemonContext()

    routes: list[Route | WebSocketRoute] = [
        Route("/health", _health_route, methods=["GET"]),
        WebSocketRoute("/mcp", _mcp_websocket_route),
        WebSocketRoute("/hook", _hook_websocket_route),
    ]

    app = Starlette(routes=routes, lifespan=lifespan)
    app.state.ctx = ctx
    return app


# ---------------------------------------------------------------------------
# Port file helpers
# ---------------------------------------------------------------------------

_STATE_DIR = VOX_DATA_DIR
_PORT_FILE = _STATE_DIR / "serve.port"
_TOKEN_FILE = _STATE_DIR / "serve.token"


def _write_port_file(port: int) -> None:
    _PORT_FILE.parent.mkdir(parents=True, exist_ok=True)
    _PORT_FILE.write_text(str(port))
    logger.info("Wrote port file: %s (port %d)", _PORT_FILE, port)


def _remove_port_file() -> None:
    try:
        _PORT_FILE.unlink(missing_ok=True)
    except OSError:
        logger.warning("Could not remove %s", _PORT_FILE)
    logger.info("Removed port file")


def read_port_file() -> int | None:
    """Read the daemon port from the port file. Returns None if missing."""
    try:
        return int(_PORT_FILE.read_text().strip())
    except (FileNotFoundError, ValueError, OSError):
        return None


def read_token_file() -> str | None:
    """Read the daemon auth token. Returns None if missing."""
    try:
        return _TOKEN_FILE.read_text().strip()
    except (FileNotFoundError, OSError):
        return None


# ---------------------------------------------------------------------------
# Server entry point
# ---------------------------------------------------------------------------


def serve(
    port: int = DEFAULT_PORT,
    *,
    host: str = "127.0.0.1",
) -> None:
    """Start the daemon HTTP + WebSocket server.  Blocks until shutdown."""
    keys_path = keys_file_path()
    loaded_keys = load_keys_env()
    configure_logging(stderr_level="INFO")
    if loaded_keys:
        logger.info("Loaded provider keys from %s: %s", keys_path, sorted(loaded_keys))
    elif keys_path.exists():
        logger.info(
            "No new provider keys loaded from %s"
            " (keys may already be set in the environment)",
            keys_path,
        )
    else:
        logger.info(
            "No provider keys file at %s — run 'vox daemon install' to configure",
            keys_path,
        )
    logger.info("Starting vox daemon on %s:%d", host, port)

    if _TOKEN_FILE.exists():
        try:
            auth_token = _TOKEN_FILE.read_text().strip()
        except (PermissionError, OSError) as exc:
            msg = f"Cannot read auth token from {_TOKEN_FILE}: {exc}"
            raise SystemExit(msg) from exc
        if not auth_token:
            msg = (
                f"Auth token file {_TOKEN_FILE} is empty. "
                "Re-run 'vox daemon install' to generate a new token."
            )
            raise SystemExit(msg)
        # Enforce secure permissions on token file.
        _TOKEN_FILE.chmod(0o600)
        logger.info("Loaded auth token from %s", _TOKEN_FILE)
    else:
        auth_token = secrets.token_urlsafe(32)
        _TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd = os.open(str(_TOKEN_FILE), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.write(fd, auth_token.encode())
        finally:
            os.close(fd)
        logger.info("Generated and wrote auth token to %s", _TOKEN_FILE)
    ctx = DaemonContext(auth_token=auth_token)

    @asynccontextmanager
    async def lifespan(_app: Starlette) -> AsyncIterator[None]:
        yield
        _remove_port_file()

    app = build_app(ctx, lifespan=lifespan)

    config = uvicorn.Config(
        app,
        host=host,
        port=port,
        log_config=None,
        log_level="warning",
        access_log=False,
    )
    server = uvicorn.Server(config)

    # Write port file after bind
    original_startup = server.startup

    async def _startup_with_port_file(
        sockets: list[socket] | None = None,
    ) -> None:
        await original_startup(sockets=sockets)
        if server.servers and server.servers[0].sockets:
            actual_port = server.servers[0].sockets[0].getsockname()[1]
            _write_port_file(actual_port)
            logger.info("Vox daemon listening on http://%s:%d", host, actual_port)
        else:
            logger.error("Server started but no bound sockets; shutting down")
            server.should_exit = True

    server.startup = _startup_with_port_file  # type: ignore[method-assign]

    server.run()
    logger.info("Daemon stopped")
