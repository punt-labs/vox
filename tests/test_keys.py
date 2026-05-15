"""Tests for punt_vox.keys — provider key management."""

from __future__ import annotations

from punt_vox.keys import (
    format_keys_env,
    parse_keys_env,
)

# ---------------------------------------------------------------------------
# parse_keys_env
# ---------------------------------------------------------------------------


class TestParseKeysEnv:
    def test_basic(self) -> None:
        text = "FOO=bar\nBAZ=qux"
        assert parse_keys_env(text) == {"FOO": "bar", "BAZ": "qux"}

    def test_comments_skipped(self) -> None:
        text = "# comment\nFOO=bar\n# another comment"
        assert parse_keys_env(text) == {"FOO": "bar"}

    def test_blank_lines_skipped(self) -> None:
        text = "\n\nFOO=bar\n\n"
        assert parse_keys_env(text) == {"FOO": "bar"}

    def test_value_with_equals(self) -> None:
        text = "KEY=val=ue=with=equals"
        assert parse_keys_env(text) == {"KEY": "val=ue=with=equals"}

    def test_malformed_no_equals(self) -> None:
        text = "NOEQUALS\nFOO=bar"
        assert parse_keys_env(text) == {"FOO": "bar"}

    def test_whitespace_stripped(self) -> None:
        text = "  FOO  =  bar  "
        assert parse_keys_env(text) == {"FOO": "bar"}

    def test_empty_key_skipped(self) -> None:
        text = "=value"
        assert parse_keys_env(text) == {}

    def test_empty_string(self) -> None:
        assert parse_keys_env("") == {}


# ---------------------------------------------------------------------------
# format_keys_env
# ---------------------------------------------------------------------------


class TestFormatKeysEnv:
    def test_sorted_output(self) -> None:
        result = format_keys_env({"ZZZ": "last", "AAA": "first"})
        lines = result.strip().splitlines()
        # Skip header comment lines
        data_lines = [line for line in lines if not line.startswith("#") and line]
        assert data_lines == ["AAA=first", "ZZZ=last"]

    def test_header_present(self) -> None:
        result = format_keys_env({"FOO": "bar"})
        assert result.startswith("# vox provider keys")

    def test_trailing_newline(self) -> None:
        result = format_keys_env({"FOO": "bar"})
        assert result.endswith("\n")

    def test_empty_values_omitted(self) -> None:
        result = format_keys_env({"FOO": "bar", "EMPTY": ""})
        assert "EMPTY" not in result
        assert "FOO=bar" in result
