"""Verify private schemas and real instrumentation through swappable exporters."""
# pyright: reportPrivateUsage=false

from __future__ import annotations

import asyncio
import atexit
import json
import os
import sqlite3
import stat
import subprocess
import sys
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import typer
from mcp.server.fastmcp import FastMCP
from mcp.types import TextContent
from opentelemetry.sdk.metrics.export import MetricExporter
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from punt_observe.telemetry import EnumAttr, SchemaRegistry, TelemetryConfig
from typer.core import TyperGroup
from typer.exceptions import Exit
from typer.testing import CliRunner

from punt_vox import hooks, server, usage
from punt_vox.__main__ import app
from punt_vox.providers.local_play import EspeakDirectPlayer, SayDirectPlayer
from punt_vox.types import AudioProviderId
from punt_vox.types_synthesis import SynthesisSpec
from punt_vox.voxd.synthesis import SynthesisPipeline

if TYPE_CHECKING:
    from opentelemetry.sdk.trace import ReadableSpan
    from opentelemetry.sdk.trace.export import SpanExportResult


class AlternateExporter(InMemorySpanExporter):
    """Exercise another exporter implementation without changing instrumentation."""

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        """Capture the same validated spans behind an alternative exporter."""
        return super().export(spans)


class RaisingExporter(InMemorySpanExporter):
    """Simulate an unavailable export backend."""

    attempts: int = 0

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        """Fail without access to the operation's return path."""
        self.attempts += 1
        raise OSError("private exporter exception must never become an attribute")


@pytest.fixture(autouse=True)
def telemetry_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Explicit telemetry tests opt in only inside an isolated home."""
    monkeypatch.setenv("HOME", str(tmp_path))
    for key in (
        "PUNT_TELEMETRY",
        "VOX_TELEMETRY",
        "PUNT_TELEMETRY_EXPORTER",
        "PUNT_TELEMETRY_OTLP_ENDPOINT",
    ):
        monkeypatch.delenv(key, raising=False)


@pytest.fixture(params=[InMemorySpanExporter, AlternateExporter])
def captured(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> Iterator[InMemorySpanExporter]:
    """Swap only shared configuration; every surface uses its normal facade."""
    exporter = request.param()
    observer = usage.Usage(
        TelemetryConfig(
            exporter="otlp",
            otlp_endpoint="https://unused.invalid",
            otlp_consent=True,
            exporter_factory=lambda _: (exporter, MagicMock(spec=MetricExporter)),
        )
    )
    monkeypatch.setattr(usage, "_usage", observer)
    yield exporter
    observer.shutdown()
    atexit.unregister(observer.shutdown)


def _spans(exporter: InMemorySpanExporter, name: str) -> list[ReadableSpan]:
    usage._usage.shutdown()
    return [span for span in exporter.get_finished_spans() if span.name == name]


def test_cli_signal(captured: InMemorySpanExporter) -> None:
    result = CliRunner().invoke(app, ["version"])
    assert result.exit_code == 0, result.output
    [span] = _spans(captured, "vox.cli.command")
    assert span.attributes == {
        "command": "version",
        "outcome": "ok",
        "punt.schema_version": 1,
    }


def test_cli_error_signal(captured: InMemorySpanExporter) -> None:
    result = CliRunner().invoke(app, ["private-user-command-do-not-export"])
    assert result.exit_code == 2
    [span] = _spans(captured, "vox.cli.command")
    assert span.attributes == {
        "command": "other",
        "outcome": "error",
        "punt.schema_version": 1,
    }
    assert not span.events


def test_cli_help_signal(captured: InMemorySpanExporter) -> None:
    result = CliRunner().invoke(app, ["say", "--help"])
    assert result.exit_code == 0, result.output
    [span] = _spans(captured, "vox.cli.command")
    assert span.attributes is not None
    assert span.attributes["command"] == "say"
    assert span.attributes["outcome"] == "ok"


@pytest.mark.parametrize(
    ("error", "outcome"),
    [
        (Exit(0), "ok"),
        (SystemExit(0), "ok"),
        (SystemExit(None), "ok"),
        (Exit(2), "error"),
        (SystemExit(1), "error"),
        (SystemExit("private failure"), "error"),
        (RuntimeError("private failure"), "error"),
    ],
)
def test_cli_exit_outcome(
    captured: InMemorySpanExporter, error: BaseException, outcome: str
) -> None:
    with pytest.raises(BaseException) as raised, usage.command_span("other"):
        raise error
    assert raised.value is error
    [span] = _spans(captured, "vox.cli.command")
    assert span.attributes is not None
    assert span.attributes["outcome"] == outcome
    assert not span.events
    assert span.status.description is None


def test_hook_signal(captured: InMemorySpanExporter, tmp_path: Path) -> None:
    with (
        patch.object(hooks, "find_config_dir", return_value=tmp_path),
        patch.object(hooks, "handle_stop", return_value=None),
    ):
        result = CliRunner().invoke(hooks.hook_app, ["stop"], input="{}")
    assert result.exit_code == 0, result.output
    [span] = _spans(captured, "vox.hook.dispatch")
    assert span.attributes == {
        "event": "stop",
        "outcome": "ok",
        "punt.schema_version": 1,
    }


def test_hook_error_signal(captured: InMemorySpanExporter, tmp_path: Path) -> None:
    error = RuntimeError("private hook failure")
    with (
        patch.object(hooks, "find_config_dir", return_value=tmp_path),
        patch.object(hooks, "handle_stop", side_effect=error),
    ):
        result = CliRunner().invoke(hooks.hook_app, ["stop"], input="{}")
    assert result.exception is error
    [span] = _spans(captured, "vox.hook.dispatch")
    assert span.attributes is not None
    assert span.attributes["outcome"] == "error"
    assert not span.events
    assert span.status.description is None


@pytest.mark.asyncio
@pytest.mark.parametrize("failed", [False, True])
async def test_mcp_signal(captured: InMemorySpanExporter, failed: bool) -> None:
    error = ValueError("private operation failure")
    operation = AsyncMock(return_value={"result": "unchanged"})
    if failed:
        operation.side_effect = error
    with patch.object(FastMCP, "call_tool", operation):
        if failed:
            with pytest.raises(ValueError) as raised:
                await server.mcp.call_tool("status", {})
            assert raised.value is error
        else:
            assert await server.mcp.call_tool("status", {}) == {"result": "unchanged"}
    [span] = _spans(captured, "vox.mcp.tool")
    assert span.attributes == {
        "tool": "status",
        "outcome": "error" if failed else "ok",
        "punt.schema_version": 1,
    }
    assert not span.events
    assert span.status.description is None


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["say", "espeak"])
@pytest.mark.parametrize("text", ["", "private local speech"])
@pytest.mark.parametrize("failed", [False, True])
@pytest.mark.parametrize("exit_code", [None, 0, 1])
async def test_direct_speech_signal(
    captured: InMemorySpanExporter,
    provider: str,
    text: str,
    failed: bool,
    exit_code: int | None,
) -> None:
    pipeline = SynthesisPipeline(playback_mutex=asyncio.Lock())
    error = OSError("private direct playback failure")
    operation = AsyncMock(return_value=exit_code, side_effect=error if failed else None)
    with patch.object(SynthesisPipeline, "_run_direct_play", operation):
        result = await pipeline.try_direct_play(
            text, SynthesisSpec(provider=provider), record_result=MagicMock()
        )
    assert result is error if failed else result == exit_code
    [span] = _spans(captured, "vox.daemon.synthesize")
    assert span.attributes is not None
    assert span.attributes["provider"] == provider
    assert span.attributes["cached"] is False
    assert span.attributes["outcome"] == (
        "error" if failed or exit_code not in (None, 0) else "ok"
    )
    assert span.attributes["text_length"] == usage._usage._open().length(text)
    assert not span.events
    assert span.status.description is None


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["say", "espeak"])
@pytest.mark.parametrize(
    "error",
    [
        None,
        FileNotFoundError("private binary"),
        subprocess.TimeoutExpired("private", 60),
    ],
)
async def test_direct_subprocess_error_signal(
    captured: InMemorySpanExporter, provider: str, error: Exception | None
) -> None:
    voices = MagicMock()
    voices.default_key = "local"
    voices.resolve.return_value.name = "local"
    player = (
        SayDirectPlayer(voices=voices)
        if provider == "say"
        else EspeakDirectPlayer(binary="espeak-ng", voices=voices)
    )
    operation = MagicMock(
        return_value=subprocess.CompletedProcess([], 5, b"", b"private stderr"),
        side_effect=error,
    )
    record_result = MagicMock()
    pipeline = SynthesisPipeline(playback_mutex=asyncio.Lock())
    with (
        patch("punt_vox.voxd.synthesis.get_provider", return_value=player),
        patch("punt_vox.providers.local_play.subprocess.run", operation),
    ):
        result = await pipeline.try_direct_play(
            "private speech",
            SynthesisSpec(provider=provider),
            record_result=record_result,
        )
    assert result == (1 if error else 5)
    assert record_result.call_args.kwargs["rc"] == result
    operation.assert_called_once()
    [span] = _spans(captured, "vox.daemon.synthesize")
    assert span.attributes is not None
    assert span.attributes["provider"] == provider
    assert span.attributes["cached"] is False
    assert span.attributes["outcome"] == "error"
    assert not span.events
    assert span.status.description is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("reply", "outcome"),
    [
        ([TextContent(type="text", text='{"error":"private failure"}')], "error"),
        ({"error": "private failure"}, "error"),
        (([], {"error": "private failure"}), "error"),
        ([TextContent(type="text", text="not json")], "ok"),
        ([TextContent(type="text", text='{"status":"ok"}')], "ok"),
        ([], "ok"),
    ],
)
async def test_mcp_handled_error_signal(
    captured: InMemorySpanExporter, reply: object, outcome: str
) -> None:
    with patch.object(FastMCP, "call_tool", AsyncMock(return_value=reply)):
        assert await server.mcp.call_tool("status", {}) is reply
    [span] = _spans(captured, "vox.mcp.tool")
    assert span.attributes is not None
    assert span.attributes["outcome"] == outcome
    assert not span.events
    assert span.status.description is None


@pytest.mark.asyncio
async def test_mcp_actual_error_payload(captured: InMemorySpanExporter) -> None:
    response = await server.mcp.call_tool("speak", {"mode": "invalid"})
    assert response
    [span] = _spans(captured, "vox.mcp.tool")
    assert span.attributes is not None
    assert span.attributes["outcome"] == "error"


@pytest.mark.asyncio
async def test_synthesis_signals(
    captured: InMemorySpanExporter, tmp_path: Path
) -> None:
    source = tmp_path / "source.mp3"
    source.write_bytes(b"audio")
    pipeline = SynthesisPipeline(playback_mutex=asyncio.Lock())
    with (
        patch("punt_vox.cache.CACHE_DIR", tmp_path / "cache"),
        patch("punt_vox.voxd.synthesis.get_provider"),
        patch(
            "punt_vox.voxd.synthesis.TempFileSynthesis.run",
            AsyncMock(return_value=source),
        ),
    ):
        first = await pipeline.synthesize_to_file(
            "private words", SynthesisSpec(provider="espeak")
        )
        second = await pipeline.synthesize_to_file(
            "private words", SynthesisSpec(provider="espeak")
        )
    assert not first.cached
    assert second.cached
    spans = _spans(captured, "vox.daemon.synthesize")
    assert [span.attributes for span in spans] == [
        {
            "provider": "espeak",
            "text_length": 13,
            "cached": cached,
            "outcome": "ok",
            "punt.schema_version": 1,
        }
        for cached in (False, True)
    ]


@pytest.mark.asyncio
async def test_synthesis_failure(
    captured: InMemorySpanExporter, tmp_path: Path
) -> None:
    pipeline = SynthesisPipeline(playback_mutex=asyncio.Lock())
    error = ValueError("private provider error")
    with (
        patch("punt_vox.cache.CACHE_DIR", tmp_path / "cache"),
        patch("punt_vox.voxd.synthesis.get_provider", side_effect=error),
        pytest.raises(ValueError) as raised,
    ):
        await pipeline.synthesize_to_file(
            "", SynthesisSpec(provider="unregistered-secret")
        )
    assert raised.value is error
    [span] = _spans(captured, "vox.daemon.synthesize")
    assert span.attributes == {
        "provider": "other",
        "text_length": 0,
        "cached": False,
        "outcome": "error",
        "punt.schema_version": 1,
    }
    assert not span.events


@pytest.mark.parametrize("surface", ["mcp", "hook", "cli", "daemon"])
def test_unknown_values_map_to_other(
    captured: InMemorySpanExporter, surface: usage.Surface
) -> None:
    with usage.span(surface, "private user text"):
        pass
    usage._usage.shutdown()
    [span] = captured.get_finished_spans()
    assert span.attributes is not None
    assert "other" in span.attributes.values()
    assert "private user text" not in json.dumps(dict(span.attributes))


def test_registry_enums_are_derived() -> None:
    command = typer.main.get_command(app)
    assert isinstance(command, TyperGroup)
    usage.register("cli", lambda: command.commands)
    registry = SchemaRegistry(usage.Usage().schemas())
    expected = {
        ("vox.cli.command", "command"): set(command.commands),
        ("vox.hook.dispatch", "event"): {
            c.name
            for c in hooks.hook_app.registered_commands
            if c.name and not c.name.startswith("_")
        },
        ("vox.mcp.tool", "tool"): {
            tool.name for tool in server.mcp._tool_manager.list_tools()
        },
        ("vox.daemon.synthesize", "provider"): set(map(str, AudioProviderId)),
    }
    for (name, attr), values in expected.items():
        schema = registry.get(name, 1)
        assert schema is not None
        spec = schema.attributes[attr]
        assert isinstance(spec, EnumAttr)
        assert set(spec.values) == values | {"other"}


@pytest.mark.parametrize(
    "version, attrs",
    [
        (2, {"tool": "status"}),
        (1, {"unexpected": "private content"}),
        (1, {"tool": 123}),
        (1, {"tool": "private content"}),
        (1, {"outcome": "private error"}),
    ],
)
def test_schema_rejection(
    captured: InMemorySpanExporter, version: int, attrs: dict[str, object]
) -> None:
    telemetry = usage._usage._open()
    with telemetry.span("vox.mcp.tool", version, **attrs):
        pass
    assert _spans(captured, "vox.mcp.tool") == []


def test_empty_schema_attributes(captured: InMemorySpanExporter) -> None:
    with usage._usage._open().span("vox.mcp.tool", 1):
        pass
    [span] = _spans(captured, "vox.mcp.tool")
    assert span.attributes == {"outcome": "ok", "punt.schema_version": 1}


@pytest.mark.parametrize("switch", ["PUNT_TELEMETRY", "VOX_TELEMETRY"])
def test_off_creates_no_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, switch: str
) -> None:
    monkeypatch.setenv(switch, "off")
    exporter = MagicMock()
    observer = usage.Usage(TelemetryConfig(exporter_factory=exporter))
    with observer.span("cli", "version"):
        pass
    observer.shutdown()
    exporter.assert_not_called()
    assert observer._telemetry is None
    assert not (tmp_path / ".punt-labs").exists()


def test_unsafe_store_preserves_operation(tmp_path: Path) -> None:
    database = tmp_path / ".punt-labs" / "vox" / "telemetry.db"
    database.mkdir(parents=True)
    observer = usage.Usage()
    with observer.span("cli", "version"):
        result = 42
    observer.shutdown()
    assert result == 42


def test_shutdown_bound() -> None:
    observer = usage.Usage()
    telemetry = MagicMock()
    observer._telemetry = telemetry
    observer.shutdown()
    telemetry.shutdown.assert_called_once_with(timeout_s=0.2)
    telemetry.shutdown.side_effect = OSError("private shutdown failure")
    observer.shutdown()


def test_private_sqlite(tmp_path: Path) -> None:
    observer = usage.Usage()
    with observer.span("daemon", "espeak", text="private text", cached=False):
        pass
    observer.shutdown()
    database = tmp_path / ".punt-labs" / "vox" / "telemetry.db"
    assert stat.S_IMODE(database.stat().st_mode) == 0o600
    assert stat.S_IMODE(database.parent.stat().st_mode) == 0o700
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT count(*) FROM spans").fetchone() == (1,)
    assert b"private text" not in database.read_bytes()


@pytest.mark.asyncio
async def test_export_failure_preserves_tool_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    exporter = RaisingExporter()
    observer = usage.Usage(
        TelemetryConfig(
            exporter="otlp",
            otlp_endpoint="https://unused.invalid",
            otlp_consent=True,
            exporter_factory=lambda _: (exporter, MagicMock(spec=MetricExporter)),
        )
    )
    monkeypatch.setattr(usage, "_usage", observer)
    with patch.object(FastMCP, "call_tool", AsyncMock(return_value={"ok": True})):
        assert await server.mcp.call_tool("status", {}) == {"ok": True}
    observer.shutdown()
    assert exporter.attempts == 1


def test_initialization_failure_preserves_result() -> None:
    observer = usage.Usage()
    with (
        patch(
            "punt_observe.telemetry.open_telemetry",
            side_effect=OSError("private error"),
        ),
        observer.span("cli", "version"),
    ):
        result = 42
    assert result == 42


def test_facade_import_is_lazy(tmp_path: Path) -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys; import punt_vox.usage; "
                "assert not any(n.startswith('opentelemetry') for n in sys.modules)"
            ),
        ],
        check=False,
        capture_output=True,
        env={**os.environ, "HOME": str(tmp_path)},
    )
    assert result.returncode == 0, result.stderr.decode()


@pytest.mark.parametrize("disabled", [False, True])
def test_inactive_hook_keeps_telemetry_unimported(
    tmp_path: Path, disabled: bool
) -> None:
    script = """
import os
import runpy
import sys
read_fd, write_fd = os.pipe()
os.write(write_fd, b'{"cwd":"/nonexistent/pl1197-inactive-hook"}')
os.close(write_fd)
sys.stdin = os.fdopen(read_fd)
sys.argv = ['vox', 'hook', 'stop']
try:
    runpy.run_module('punt_vox', run_name='__main__')
except SystemExit as exc:
    assert exc.code in (None, 0)
assert 'punt_observe.telemetry' not in sys.modules
assert not any(name.startswith('opentelemetry') for name in sys.modules)
"""
    env = {**os.environ, "HOME": str(tmp_path)}
    if disabled:
        env["PUNT_TELEMETRY"] = "off"
    result = subprocess.run(
        [sys.executable, "-c", script],
        check=False,
        capture_output=True,
        env=env,
        cwd=tmp_path,
    )
    assert result.returncode == 0, result.stderr.decode()
    assert not list(tmp_path.rglob("telemetry.db"))
