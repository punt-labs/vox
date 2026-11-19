"""Own versioned usage schemas and a lazy, failure-isolated process observer."""

from __future__ import annotations

import atexit
import os
import threading
from collections.abc import Callable, Iterable
from contextlib import AbstractContextManager, nullcontext, suppress
from enum import StrEnum
from importlib import import_module
from typing import TYPE_CHECKING, Literal, Self, cast

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
        shared = import_module("punt_observe.telemetry")

        registries: dict[str, Callable[[], Iterable[str]]] = {
            **_REGISTRIES,
            "mcp": lambda: tuple(map(str, McpTool)),
            "daemon": lambda: tuple(map(str, AudioProviderId)),
        }
        self._choices = {
            surface: tuple(
                sorted({"other", *(v for v in values() if v and v[0].isalpha())})
            )
            for surface, values in registries.items()
        }
        schemas: list[SignalSchema] = []
        for surface, (name, attribute) in _SIGNALS.items():
            attrs = {
                attribute: shared.EnumAttr(self._choices.get(surface, ("other",))),
                "outcome": shared.EnumAttr(("ok", "error")),
            }
            if surface == "daemon":
                attrs.update(cached=shared.BoolAttr(), text_length=shared.LengthAttr())
            schemas.append(shared.SignalSchema(name, 1, "span", attrs))
        return tuple(schemas)

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
    def command_span(command: str) -> AbstractContextManager[None]:
        """Leave internal hooks to their dedicated signal."""
        return nullcontext() if command == "hook" else _usage.span("cli", command)

    @staticmethod
    def observe(
        surface: Surface,
        value: str,
        *,
        text: str | None = None,
        cached: bool | None = None,
    ) -> AbstractContextManager[None]:
        """Observe through the lazy process owner without exporter imports."""
        return _usage.span(surface, value, text=text, cached=cached)


_usage = Usage()
register = Usage.register
command_span = Usage.command_span
span = Usage.observe
