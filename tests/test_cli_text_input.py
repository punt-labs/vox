"""Tests for CLI text-input resolution."""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest
import typer
from click import unstyle
from typer.testing import CliRunner

from punt_vox.__main__ import app
from punt_vox.cli_text_input import TextInput
from punt_vox.output_formatter import OutputFormatter


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

    def test_empty_stdin_error_is_structured_under_json(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Under --json, the empty-stdin failure emits a JSON error object."""
        monkeypatch.setattr("sys.stdin", io.StringIO(""))
        with pytest.raises(typer.Exit):
            TextInput(OutputFormatter(json_output=True)).resolve("-", None)
        assert json.loads(capsys.readouterr().out) == {"error": "no text on stdin"}


class TestSpeechStdinEncoding:
    """Reject bad pipe bytes before resolving synthesis settings."""

    @pytest.mark.parametrize(
        "command",
        [["say", "--once", "10", "--provider", "espeak", "-"], ["rec", "new"]],
    )
    @pytest.mark.parametrize("charset", ["utf-8", "latin-1"])
    @pytest.mark.parametrize("flags", [[], ["--json"]])
    def test_invalid_speech_stdin(
        self,
        monkeypatch: pytest.MonkeyPatch,
        command: list[str],
        charset: str,
        flags: list[str],
    ) -> None:
        def fail_on_synthesis_settings() -> None:
            pytest.fail("invalid stdin reached synthesis settings")

        monkeypatch.setattr(
            "punt_vox.cli_synthesis.SessionSpec.for_repo", fail_on_synthesis_settings
        )
        result = CliRunner(charset=charset).invoke(
            app, [*flags, *command], input=b"left\xffright"
        )

        assert result.exit_code == 1
        message = "speech stdin must be valid UTF-8"
        if flags:
            assert json.loads(result.stdout) == {"error": message}
            assert result.stderr == ""
        else:
            assert result.stdout == ""
            assert unstyle(result.stderr).strip() == f"Error: {message}."


class TestTextInputFromFile:
    """TextInput parses a --from JSON segments file."""

    @pytest.mark.parametrize("flags", [[], ["--json"]])
    @pytest.mark.parametrize(
        "kind", ["directory", "permission", "missing", "read_error"]
    )
    def test_os_error_is_usage_error(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        flags: list[str],
        kind: str,
    ) -> None:
        monkeypatch.chdir(tmp_path)
        path = Path("input.json")
        if kind == "directory":
            path.mkdir()
            message = "Invalid value: Cannot read --from file: Is a directory."
        elif kind == "missing":
            message = f"Invalid value for '--from': path '{path}' does not exist."
        else:
            path.write_text('["hello"]', encoding="utf-8")
            error = (
                PermissionError(13, "Permission denied")
                if kind == "permission"
                else OSError(5, "Input/output error")
            )

            read_text = Path.read_text

            def fail_read(
                target: Path,
                encoding: str | None = None,
                errors: str | None = None,
                newline: str | None = None,
            ) -> str:
                if target == path:
                    raise error
                return read_text(
                    target, encoding=encoding, errors=errors, newline=newline
                )

            monkeypatch.setattr(Path, "read_text", fail_read)
            message = f"Invalid value: Cannot read --from file: {error.strerror}."
        result = CliRunner().invoke(
            app,
            [*flags, "say", "--from", str(path)],
            terminal_width=240,
        )
        assert result.exit_code == 2
        if flags:
            assert json.loads(result.stdout) == {"error": message}
            assert result.stderr == ""
        else:
            assert result.stdout == ""
            assert message in unstyle(result.stderr)
            assert "Traceback" not in result.stderr

    @pytest.mark.parametrize("flags", [[], ["--json"]])
    def test_non_utf8_is_usage_error(self, tmp_path: Path, flags: list[str]) -> None:
        path = tmp_path / "bad.txt"
        path.write_bytes(b"hi \xff\xfe\n")
        result = CliRunner().invoke(app, [*flags, "say", "--from", str(path)])
        assert result.exit_code == 2
        message = "Invalid value: --from file is not UTF-8 (byte 3); save it as UTF-8."
        if flags:
            assert json.loads(result.stdout) == {"error": message}
            assert result.stderr == ""
        else:
            assert result.stdout == ""
            assert message in unstyle(result.stderr)
            assert "Traceback" not in result.stderr

    @pytest.mark.parametrize("flags", [[], ["--json"]])
    @pytest.mark.parametrize("command", [["say"], ["rec", "new"]])
    @pytest.mark.parametrize("raw", ["[]", '["", {"text": ""}]', '["  ", "\\n"]'])
    def test_no_text_is_usage_error(
        self, tmp_path: Path, flags: list[str], command: list[str], raw: str
    ) -> None:
        """A file with no text fails like blank stdin instead of exiting 0."""
        path = tmp_path / "segs.json"
        path.write_text(raw, encoding="utf-8")
        result = CliRunner().invoke(app, [*flags, *command, "--from", str(path)])
        assert result.exit_code == 2
        message = "Invalid value: --from file has no text to speak."
        if flags:
            assert json.loads(result.stdout) == {"error": message}
            assert result.stderr == ""
        else:
            assert result.stdout == ""
            assert message in unstyle(result.stderr)

    @pytest.mark.parametrize("flags", [[], ["--json"]])
    @pytest.mark.parametrize("command", [["say"], ["rec", "new"]])
    def test_unencodable_segment_is_usage_error(
        self, tmp_path: Path, flags: list[str], command: list[str]
    ) -> None:
        """A lone surrogate fails the whole file before any segment is sent."""
        path = tmp_path / "segs.json"
        path.write_text('["before", "\\ud800", "after"]', encoding="utf-8")
        result = CliRunner().invoke(app, [*flags, *command, "--from", str(path)])
        assert result.exit_code == 2
        message = (
            "Invalid value: Element 1 text is not valid UTF-8: "
            "unpaired surrogate U+D800 at character 0."
        )
        if flags:
            assert json.loads(result.stdout) == {"error": message}
            assert result.stderr == ""
        else:
            assert result.stdout == ""
            stderr = " ".join(unstyle(result.stderr).replace("│", " ").split())
            assert message in stderr

    def test_string_array(self, tmp_path: Path) -> None:
        path = tmp_path / "segs.json"
        path.write_text(json.dumps(["one", "two"]), encoding="utf-8")
        assert TextInput(OutputFormatter()).resolve(None, path) == ["one", "two"]

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            (["héllo", {"text": "世界"}], ["héllo", "世界"]),
            ([{"text": ""}, "last"], ["last"]),
            (["  ", {"text": "\n"}, "spoken"], ["spoken"]),
        ],
    )
    def test_segment_values(
        self, tmp_path: Path, raw: list[object], expected: list[str]
    ) -> None:
        path = tmp_path / "segs.json"
        path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
        assert TextInput(OutputFormatter()).resolve(None, path) == expected

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

    def test_invalid_json_crlf_reports_line_and_column_only(
        self, tmp_path: Path
    ) -> None:
        path = tmp_path / "segs.json"
        path.write_bytes(b'[\r\n  "ok",\r\n  nope\r\n]\r\n')
        with pytest.raises(typer.BadParameter, match=r"line 3 column 3$"):
            TextInput(OutputFormatter()).resolve(None, path)

    @pytest.mark.parametrize("command", [["say"], ["rec", "new"]])
    def test_invalid_json_precedes_provider_resolution(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        hermetic_config: Path,
        command: list[str],
    ) -> None:
        (hermetic_config / "vox.md").unlink()
        monkeypatch.chdir(tmp_path)
        path = Path("broken.json")
        path.write_text("{not json", encoding="utf-8")

        result = CliRunner().invoke(
            app, [*command, "--from", str(path)], env={"COLUMNS": "42"}
        )

        stderr = " ".join(unstyle(result.stderr).replace("│", " ").split())
        assert result.exit_code == 2, stderr
        assert "broken.json" in stderr
        assert "invalid JSON" in stderr
        assert "line 1 column 2" in stderr

    @pytest.mark.parametrize(
        "item",
        [{"text": False}, {"text": 0}, {"text": [1, 2]}, {"text": None}, {}],
        ids=["false", "zero", "array", "null", "missing"],
    )
    def test_object_requires_string_text(
        self, tmp_path: Path, item: dict[str, object]
    ) -> None:
        path = tmp_path / "segs.json"
        path.write_text(json.dumps(["valid", item]), encoding="utf-8")
        with pytest.raises(typer.BadParameter, match=r"Element 1.*string text"):
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
