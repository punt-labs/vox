"""Own versioned usage schemas and a lazy, failure-isolated process observer."""

from __future__ import annotations

import atexit
import json
import os
import threading
from collections.abc import Callable, Generator, Iterable
from contextlib import AbstractContextManager, contextmanager, nullcontext, suppress
from contextvars import ContextVar
from enum import StrEnum
from importlib import import_module
from typing import TYPE_CHECKING, Literal, Self, cast

from typer.exceptions import Exit

from punt_vox.types import AudioProviderId

if TYPE_CHECKING:
    from punt_observe.telemetry import SignalSchema, Telemetry, TelemetryConfig

Surface = Literal["mcp", "hook", "cli", "daemon"]
_SIGNALS = {
    "mcp": ("vox.mcp.tool", "tool"),
    "hook": ("vox.hook.dispatch", "event"),
    "cli": ("vox.cli.command", "command"),
    "daemon": ("vox.daemon.synthesize", "provider"),
}
_REGISTRIES: dict[str, Callable[[], Iterable[str]]] = {}
_failed: ContextVar[bool] = ContextVar("vox_usage_failed", default=False)


class McpTool(StrEnum):
    """Name the registered mic tools without importing the MCP server in hooks."""

    unmute = "unmute"
    vibe = "vibe"
    notify = "notify"
    speak = "speak"
    status = "status"
    rec = "rec"
    music = "music"
    enablement = "enablement"
    model = "model"
    provider = "provider"
    voice = "voice"


class Usage:
    """Open one private telemetry provider only when a process emits a signal."""

    _config: TelemetryConfig | None
    _telemetry: Telemetry | None
    _choices: dict[str, tuple[str, ...]]
    _lock: threading.Lock

    def __new__(cls, config: TelemetryConfig | None = None) -> Self:
        """Create an unopened observer with optional shared configuration."""
        self = super().__new__(cls)
        self._config = config
        self._telemetry = None
        self._choices = {}
        self._lock = threading.Lock()
        return self

    def schemas(self) -> tuple[SignalSchema, ...]:
        """Declare the four version-one signals from active process registries."""
        registries: dict[str, Callable[[], Iterable[str]]] = {
            **_REGISTRIES,
            "mcp": lambda: tuple(map(str, McpTool)),
            "daemon": lambda: tuple(map(str, AudioProviderId)),
        }
        self._choices = {
            surface: self._enum_values(values())
            for surface, values in registries.items()
        }
        return tuple(self._schema(surface) for surface in _SIGNALS)

    @staticmethod
    def _enum_values(values: Iterable[str]) -> tuple[str, ...]:
        return tuple(sorted({"other", *(v for v in values if v and v[0].isalpha())}))

    def _schema(self, surface: str) -> SignalSchema:
        shared = import_module("punt_observe.telemetry")
        name, attribute = _SIGNALS[surface]
        attrs = {
            attribute: shared.EnumAttr(self._choices.get(surface, ("other",))),
            "outcome": shared.EnumAttr(("ok", "error")),
        }
        if surface == "daemon":
            attrs.update(cached=shared.BoolAttr(), text_length=shared.LengthAttr())
        return cast("SignalSchema", shared.SignalSchema(name, 1, "span", attrs))

    def _open(self) -> Telemetry:
        shared = import_module("punt_observe.telemetry")

        with self._lock:
            if self._telemetry is None:
                self._telemetry = shared.open_telemetry(
                    "vox", self.schemas(), self._config or shared.TelemetryConfig()
                )
                atexit.register(self.shutdown)
        return cast("Telemetry", self._telemetry)

    def span(
        self,
        surface: Surface,
        value: str,
        *,
        text: str | None = None,
        cached: bool | None = None,
    ) -> AbstractContextManager[None]:
        """Observe an operation using only registered names and derived lengths."""
        with suppress(Exception):
            if self._disabled():
                return nullcontext()
            telemetry = self._open()
            name, attribute = _SIGNALS[surface]
            selected = value if value in self._choices.get(surface, ()) else "other"
            attrs: dict[str, object] = {attribute: selected}
            if text is not None:
                attrs["text_length"] = telemetry.length(text)
            if cached is not None:
                attrs["cached"] = cached
            return telemetry.span(name, 1, **attrs)
        return nullcontext()

    def _disabled(self) -> bool:
        return (self._config is not None and not self._config.enabled) or any(
            os.environ.get(key, "").strip().lower() in {"off", "0", "false", "no"}
            for key in ("PUNT_TELEMETRY", "VOX_TELEMETRY")
        )

    def shutdown(self) -> None:
        """Bound process-exit flushing to 200 ms, including hook and CLI exits."""
        with suppress(Exception):
            if self._telemetry is not None:
                self._telemetry.shutdown(timeout_s=0.2)

    @staticmethod
    def register(surface: Surface, choices: Callable[[], Iterable[str]]) -> None:
        """Bind an entry point's live registry before its first observation."""
        _REGISTRIES[surface] = choices

    @staticmethod
    @contextmanager
    def command_span(command: str) -> Generator[None]:
        """Observe CLI failures while preserving successful Click and system exits."""
        successful_exit: Exit | SystemExit | None = None
        with nullcontext() if command == "hook" else _usage.span("cli", command):
            try:
                yield
            except (Exit, SystemExit) as exc:
                if not Usage._successful_exit(exc):
                    raise
                successful_exit = exc
        if successful_exit is not None:
            raise successful_exit

    @staticmethod
    def _successful_exit(exc: Exit | SystemExit) -> bool:
        code = exc.exit_code if isinstance(exc, Exit) else exc.code
        return code in (None, 0)

    @staticmethod
    def _error_result(result: object) -> bool:
        if isinstance(result, dict):
            return "error" in result
        if isinstance(result, list | tuple):
            return any(
                Usage._error_result(part) for part in cast("Iterable[object]", result)
            )
        return Usage._text_error(result)

    @staticmethod
    def _text_error(result: object) -> bool:
        if getattr(result, "type", None) == "text":
            with suppress(ValueError, TypeError):
                return Usage._error_result(json.loads(getattr(result, "text", "")))
        return False

    @staticmethod
    def mcp_result[T](result: T) -> T:
        """Mark handled JSON errors without changing the MCP response."""
        with suppress(Exception):
            if Usage._error_result(result):
                _failed.set(True)
        return result

    @staticmethod
    @contextmanager
    def _outcome(context: AbstractContextManager[None]) -> Generator[None]:
        handled_error = RuntimeError()
        token = _failed.set(False)
        try:
            with context:
                yield
                if _failed.get():
                    raise handled_error
        except RuntimeError as exc:
            if exc is not handled_error:
                raise
        finally:
            _failed.reset(token)

    @staticmethod
    def observe(
        surface: Surface,
        value: str,
        *,
        text: str | None = None,
        cached: bool | None = None,
    ) -> AbstractContextManager[None]:
        """Observe through the lazy process owner without exporter imports."""
        return Usage._outcome(_usage.span(surface, value, text=text, cached=cached))


_usage = Usage()
register = Usage.register
command_span = Usage.command_span
mcp_result = Usage.mcp_result
span = Usage.observe
