"""Tests for the JSON wire-boundary accessor."""

from __future__ import annotations

import pytest

from punt_vox.voxd.programs.wire import JsonObject


class TestParse:
    def test_parses_an_object(self) -> None:
        obj = JsonObject.parse('{"a": 1}', "doc")
        assert obj.require_int("a") == 1

    def test_rejects_a_non_object(self) -> None:
        with pytest.raises(ValueError, match="must be a JSON object"):
            JsonObject.parse("[1, 2]", "doc")

    def test_coerce_rejects_a_scalar(self) -> None:
        with pytest.raises(ValueError, match="must be a JSON object"):
            JsonObject.coerce(7, "doc")


class TestRequire:
    def test_require_str(self) -> None:
        assert JsonObject.parse('{"n": "x"}', "d").require_str("n") == "x"

    def test_require_str_wrong_type(self) -> None:
        with pytest.raises(ValueError, match="must be a string"):
            JsonObject.parse('{"n": 1}', "d").require_str("n")

    def test_require_int(self) -> None:
        assert JsonObject.parse('{"n": 3}', "d").require_int("n") == 3

    def test_require_int_rejects_bool(self) -> None:
        with pytest.raises(ValueError, match="must be an integer"):
            JsonObject.parse('{"n": true}', "d").require_int("n")

    def test_require_int_wrong_type(self) -> None:
        with pytest.raises(ValueError, match="must be an integer"):
            JsonObject.parse('{"n": "x"}', "d").require_int("n")

    def test_require_missing_field(self) -> None:
        with pytest.raises(ValueError, match="missing required field 'n'"):
            JsonObject.parse("{}", "d").require_str("n")

    def test_require_object(self) -> None:
        nested = JsonObject.parse('{"o": {"k": "v"}}', "d").require_object("o")
        assert nested.require_str("k") == "v"

    def test_require_object_wrong_type(self) -> None:
        with pytest.raises(ValueError, match="must be a JSON object"):
            JsonObject.parse('{"o": 1}', "d").require_object("o")

    def test_require_list(self) -> None:
        assert JsonObject.parse('{"xs": [1, 2]}', "d").require_list("xs") == (1, 2)

    def test_require_list_wrong_type(self) -> None:
        with pytest.raises(ValueError, match="must be a list"):
            JsonObject.parse('{"xs": 1}', "d").require_list("xs")


class TestOptional:
    def test_opt_int_present(self) -> None:
        assert JsonObject.parse('{"n": 5}', "d").opt_int("n") == 5

    def test_opt_int_absent(self) -> None:
        assert JsonObject.parse("{}", "d").opt_int("n") is None

    def test_opt_int_present_but_wrong_raises(self) -> None:
        with pytest.raises(ValueError, match="must be an integer"):
            JsonObject.parse('{"n": "x"}', "d").opt_int("n")

    def test_opt_str_present(self) -> None:
        assert JsonObject.parse('{"n": "x"}', "d").opt_str("n") == "x"

    def test_opt_str_absent(self) -> None:
        assert JsonObject.parse("{}", "d").opt_str("n") is None
