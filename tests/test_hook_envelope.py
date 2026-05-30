"""Tests for the shared hook envelope in src/punt_vox/hook_envelope.py."""

from __future__ import annotations

from pathlib import Path

from punt_vox.hook_envelope import HookEnvelope


class TestHookEnvelope:
    def test_cwd_of_present(self) -> None:
        assert HookEnvelope.cwd_of({"cwd": "/a/b"}) == Path("/a/b")

    def test_cwd_of_absent_is_none(self) -> None:
        assert HookEnvelope.cwd_of({}) is None

    def test_cwd_of_empty_string_is_none(self) -> None:
        assert HookEnvelope.cwd_of({"cwd": ""}) is None

    def test_cwd_of_non_string_is_none(self) -> None:
        assert HookEnvelope.cwd_of({"cwd": 123}) is None

    def test_parse_carries_cwd(self) -> None:
        assert HookEnvelope.parse({"cwd": "/a/b"}).cwd == Path("/a/b")

    def test_parse_absent_cwd_is_none(self) -> None:
        assert HookEnvelope.parse({}).cwd is None
