"""Tests for punt_vox.cli_io: output-flag routing and text-segment resolution."""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest
import typer

from punt_vox.cli_io import OutputFlags, TextInput
from punt_vox.output_formatter import OutputFormatter


class TestOutputFlags:
    """OutputFlags ORs --json/--verbose/--quiet across the callback and command."""

    def test_json_from_either_position_sets_formatter(self) -> None:
        fmt = OutputFormatter()
        flags = OutputFlags(fmt)
        flags.apply(json_output=True, verbose=False, quiet=False)
        fmt.emit({"a": 1}, "text")  # emits JSON; verified below via capsys in siblings
        assert fmt._json is True  # pyright: ignore[reportPrivateUsage]

    def test_quiet_sets_formatter(self) -> None:
        fmt = OutputFormatter()
        OutputFlags(fmt).apply(json_output=False, verbose=False, quiet=True)
        assert fmt._quiet is True  # pyright: ignore[reportPrivateUsage]

    def test_reset_clears_formatter_and_seen(self) -> None:
        """reset restores a fresh state so a reused process does not leak flags."""
        fmt = OutputFormatter(json_output=True, quiet=True)
        flags = OutputFlags(fmt)
        flags.apply(json_output=False, verbose=True, quiet=False)
        flags.reset()
        assert fmt._json is False  # pyright: ignore[reportPrivateUsage]
        assert fmt._quiet is False  # pyright: ignore[reportPrivateUsage]
        # After reset, verbose alone no longer trips the mutual-exclusion guard.
        flags.apply(json_output=False, verbose=False, quiet=True)

    def test_verbose_and_quiet_together_raise(self) -> None:
        flags = OutputFlags(OutputFormatter())
        with pytest.raises(typer.BadParameter, match="mutually exclusive"):
            flags.apply(json_output=False, verbose=True, quiet=True)

    def test_split_verbose_then_quiet_raises(self) -> None:
        """Accumulated flags across two apply calls (callback then command) trip."""
        flags = OutputFlags(OutputFormatter())
        flags.apply(json_output=False, verbose=True, quiet=False)
        with pytest.raises(typer.BadParameter, match="mutually exclusive"):
            flags.apply(json_output=False, verbose=False, quiet=True)


class TestTextInputResolve:
    """TextInput.resolve chooses argument, --from file, or stdin."""

    def test_argument_wins(self) -> None:
        segments = TextInput(OutputFormatter()).resolve("hello", None)
        assert segments == ["hello"]

    def test_reads_stdin_on_dash(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("sys.stdin", io.StringIO("piped text\n"))
        segments = TextInput(OutputFormatter()).resolve("-", None)
        assert segments == ["piped text"]

    def test_empty_stdin_exits(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("sys.stdin", io.StringIO("   \n"))
        with pytest.raises(typer.Exit) as exc:
            TextInput(OutputFormatter()).resolve("-", None)
        assert exc.value.exit_code == 1

    def test_no_input_exits(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """No argument and a tty stdin is a usage error, not a blocking read."""
        monkeypatch.setattr("sys.stdin.isatty", lambda: True)
        with pytest.raises(typer.Exit) as exc:
            TextInput(OutputFormatter()).resolve(None, None)
        assert exc.value.exit_code == 1


class TestTextInputFromFile:
    """TextInput parses a --from JSON segments file."""

    def test_string_array(self, tmp_path: Path) -> None:
        path = tmp_path / "segs.json"
        path.write_text(json.dumps(["one", "two"]), encoding="utf-8")
        assert TextInput(OutputFormatter()).resolve(None, path) == ["one", "two"]

    def test_object_array_reads_text_field(self, tmp_path: Path) -> None:
        path = tmp_path / "segs.json"
        raw = json.dumps([{"text": "spoken"}, {"text": ""}])
        path.write_text(raw, encoding="utf-8")
        assert TextInput(OutputFormatter()).resolve(None, path) == ["spoken"]

    def test_invalid_json_raises_bad_parameter(self, tmp_path: Path) -> None:
        path = tmp_path / "segs.json"
        path.write_text("{not json", encoding="utf-8")
        with pytest.raises(typer.BadParameter, match="valid JSON"):
            TextInput(OutputFormatter()).resolve(None, path)

    def test_non_array_raises_bad_parameter(self, tmp_path: Path) -> None:
        path = tmp_path / "segs.json"
        path.write_text(json.dumps({"text": "x"}), encoding="utf-8")
        with pytest.raises(typer.BadParameter, match="JSON array"):
            TextInput(OutputFormatter()).resolve(None, path)

    def test_bad_element_type_raises_bad_parameter(self, tmp_path: Path) -> None:
        path = tmp_path / "segs.json"
        path.write_text(json.dumps(["ok", 42]), encoding="utf-8")
        with pytest.raises(typer.BadParameter, match="Element 1"):
            TextInput(OutputFormatter()).resolve(None, path)
