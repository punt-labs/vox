"""Tests for punt_vox.output_formatter."""

from __future__ import annotations

import json

from punt_vox.output_formatter import OutputFormatter


class TestOutputFormatter:
    def test_emit_json_mode(self, capsys: object) -> None:
        """JSON mode emits the payload as JSON, ignoring the text."""
        import _pytest.capture

        assert isinstance(capsys, _pytest.capture.CaptureFixture)
        fmt = OutputFormatter(json_output=True)
        fmt.emit({"key": "val"}, "human text")
        captured = capsys.readouterr()
        assert json.loads(captured.out) == {"key": "val"}

    def test_emit_text_mode(self, capsys: object) -> None:
        """Default mode emits the human-readable text."""
        import _pytest.capture

        assert isinstance(capsys, _pytest.capture.CaptureFixture)
        fmt = OutputFormatter()
        fmt.emit({"key": "val"}, "human text")
        captured = capsys.readouterr()
        assert "human text" in captured.out

    def test_emit_quiet_mode(self, capsys: object) -> None:
        """Quiet mode suppresses all output."""
        import _pytest.capture

        assert isinstance(capsys, _pytest.capture.CaptureFixture)
        fmt = OutputFormatter(quiet=True)
        fmt.emit({"key": "val"}, "human text")
        captured = capsys.readouterr()
        assert captured.out == ""

    def test_set_json(self, capsys: object) -> None:
        """set_json switches to JSON mode after construction."""
        import _pytest.capture

        assert isinstance(capsys, _pytest.capture.CaptureFixture)
        fmt = OutputFormatter()
        fmt.set_json(value=True)
        fmt.emit({"a": 1}, "text")
        captured = capsys.readouterr()
        assert json.loads(captured.out) == {"a": 1}

    def test_set_quiet(self, capsys: object) -> None:
        """set_quiet suppresses output after construction."""
        import _pytest.capture

        assert isinstance(capsys, _pytest.capture.CaptureFixture)
        fmt = OutputFormatter()
        fmt.set_quiet(value=True)
        fmt.emit({"a": 1}, "text")
        captured = capsys.readouterr()
        assert captured.out == ""

    def test_json_takes_precedence_over_quiet(self, capsys: object) -> None:
        """When both JSON and quiet are set, JSON wins."""
        import _pytest.capture

        assert isinstance(capsys, _pytest.capture.CaptureFixture)
        fmt = OutputFormatter(json_output=True, quiet=True)
        fmt.emit({"x": 1}, "text")
        captured = capsys.readouterr()
        assert json.loads(captured.out) == {"x": 1}

    def test_error_json_wraps_under_error_key(self, capsys: object) -> None:
        """Under --json, error() emits a structured {"error": ...} on stdout."""
        import _pytest.capture

        assert isinstance(capsys, _pytest.capture.CaptureFixture)
        fmt = OutputFormatter(json_output=True)
        fmt.error("nope", "Error: nope")
        captured = capsys.readouterr()
        assert json.loads(captured.out) == {"error": "nope"}
        assert captured.err == ""

    def test_error_human_goes_to_stderr(self, capsys: object) -> None:
        """Without --json, error() writes the human text to stderr, not stdout."""
        import _pytest.capture

        assert isinstance(capsys, _pytest.capture.CaptureFixture)
        fmt = OutputFormatter()
        fmt.error("nope", "Error: nope")
        captured = capsys.readouterr()
        assert captured.out == ""
        assert "Error: nope" in captured.err

    def test_error_json_ignores_quiet(self, capsys: object) -> None:
        """A --json error object must surface even when quiet is set."""
        import _pytest.capture

        assert isinstance(capsys, _pytest.capture.CaptureFixture)
        fmt = OutputFormatter(json_output=True, quiet=True)
        fmt.error({"code": "no_daemon"}, "Error: no daemon")
        captured = capsys.readouterr()
        assert json.loads(captured.out) == {"error": {"code": "no_daemon"}}
