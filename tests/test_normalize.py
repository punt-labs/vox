"""Tests for punt_vox.normalize — programmer string normalization."""

from __future__ import annotations

import pytest

from punt_vox.normalize import normalize_for_speech

# ---------------------------------------------------------------------------
# snake_case splitting
# ---------------------------------------------------------------------------


class TestSnakeCase:
    def test_simple_snake_case(self) -> None:
        assert normalize_for_speech("hello_world") == "hello world"

    def test_eof_received(self) -> None:
        assert normalize_for_speech("eof_received") == "EOF received"

    def test_multiple_underscores(self) -> None:
        assert normalize_for_speech("on_data_received") == "on data received"

    def test_leading_underscore(self) -> None:
        # Leading underscore → empty first part, skipped
        assert normalize_for_speech("_private") == "private"

    def test_trailing_underscore(self) -> None:
        assert normalize_for_speech("value_") == "value"

    def test_all_caps_snake(self) -> None:
        assert normalize_for_speech("MAX_RETRY_COUNT") == "MAX RETRY COUNT"


# ---------------------------------------------------------------------------
# camelCase / PascalCase splitting
# ---------------------------------------------------------------------------


class TestCamelCase:
    def test_simple_camel(self) -> None:
        assert normalize_for_speech("fileName") == "file name"

    def test_pascal_case(self) -> None:
        assert normalize_for_speech("FileName") == "file name"

    def test_multiple_humps(self) -> None:
        assert normalize_for_speech("onDataReceived") == "on data received"

    def test_acronym_prefix(self) -> None:
        # HTMLParser → HTML Parser → HTML parser
        assert normalize_for_speech("HTMLParser") == "HTML parser"

    def test_acronym_suffix(self) -> None:
        assert normalize_for_speech("parseHTML") == "parse HTML"


# ---------------------------------------------------------------------------
# Abbreviation expansion
# ---------------------------------------------------------------------------


class TestAbbreviations:
    def test_stderr(self) -> None:
        assert normalize_for_speech("stderr") == "standard error"

    def test_stdout(self) -> None:
        assert normalize_for_speech("stdout") == "standard output"

    def test_stdin(self) -> None:
        assert normalize_for_speech("stdin") == "standard input"

    def test_eof_standalone(self) -> None:
        assert normalize_for_speech("eof") == "EOF"

    def test_case_insensitive(self) -> None:
        assert normalize_for_speech("STDERR") == "standard error"

    def test_abbreviation_in_sentence(self) -> None:
        assert (
            normalize_for_speech("Check stderr for errors")
            == "Check standard error for errors"
        )

    def test_abbreviation_with_punctuation(self) -> None:
        assert normalize_for_speech("(stderr)") == "(standard error)"

    def test_repo(self) -> None:
        assert normalize_for_speech("repo") == "repository"

    def test_config(self) -> None:
        assert normalize_for_speech("config") == "configuration"

    def test_lol(self) -> None:
        assert normalize_for_speech("lol") == "laughing out loud"

    def test_rofl(self) -> None:
        assert normalize_for_speech("ROFL") == "rolling on the floor laughing"

    def test_lmao(self) -> None:
        assert normalize_for_speech("lmao") == "laughing my ass off"

    def test_smh(self) -> None:
        assert normalize_for_speech("smh") == "shaking my head"

    def test_omg(self) -> None:
        assert normalize_for_speech("OMG") == "oh my god"


# ---------------------------------------------------------------------------
# File paths — should be left alone
# ---------------------------------------------------------------------------


class TestFilePaths:
    def test_absolute_path(self) -> None:
        assert normalize_for_speech("/usr/local/bin") == "/usr/local/bin"

    def test_home_path(self) -> None:
        assert normalize_for_speech("~/Documents") == "~/Documents"

    def test_relative_path(self) -> None:
        assert normalize_for_speech("./src/main.py") == "./src/main.py"

    def test_path_in_sentence(self) -> None:
        result = normalize_for_speech("Check /usr/local/bin for binaries")
        assert "/usr/local/bin" in result


# ---------------------------------------------------------------------------
# Mixed text
# ---------------------------------------------------------------------------


class TestMixedText:
    def test_natural_english_unchanged(self) -> None:
        text = "This is a normal sentence."
        assert normalize_for_speech(text) == text

    def test_mixed_programmer_and_english(self) -> None:
        result = normalize_for_speech("The eof_received handler uses stderr")
        assert result == "The EOF received handler uses standard error"

    def test_empty_string(self) -> None:
        assert normalize_for_speech("") == ""

    def test_whitespace_only(self) -> None:
        assert normalize_for_speech("   ") == ""

    def test_single_word(self) -> None:
        assert normalize_for_speech("hello") == "hello"

    def test_preserves_numbers(self) -> None:
        assert normalize_for_speech("port 8080") == "port 8080"


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------


class TestIdempotency:
    @pytest.mark.parametrize(
        "text",
        [
            "eof_received",
            "fileName",
            "stderr",
            "The eof_received handler uses stderr",
            "normal sentence",
            "/usr/local/bin",
        ],
    )
    def test_idempotent(self, text: str) -> None:
        once = normalize_for_speech(text)
        twice = normalize_for_speech(once)
        assert once == twice


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_single_char(self) -> None:
        assert normalize_for_speech("x") == "x"

    def test_all_caps_short(self) -> None:
        # Two-letter caps — treated as abbreviation if known, else kept
        assert normalize_for_speech("IO") == "IO"

    def test_number_in_identifier(self) -> None:
        assert normalize_for_speech("value2key") == "value 2 key"

    def test_snake_with_abbreviation_parts(self) -> None:
        assert normalize_for_speech("stdin_reader") == "standard input reader"

    def test_punctuation_preserved(self) -> None:
        assert normalize_for_speech("(fileName),") == "(file name),"

    def test_expression_tags_untouched(self) -> None:
        # Vibe tags like [warm] should pass through — apply_vibe handles them
        result = normalize_for_speech("[warm] Hello world")
        assert "[warm]" in result
