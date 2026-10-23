"""Tests for the value checks :class:`KeysEnvWriter` applies before writing a key."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from punt_vox.keys import HEADER_CARRIED_KEY_NAMES, PROVIDER_KEY_NAMES, parse_keys_env
from punt_vox.service.keys_env import KeysEnvWriter

_LOGGER = "punt_vox.service.keys_env"
_HEADER_KEYS = sorted(HEADER_CARRIED_KEY_NAMES)
_SETTING_KEYS = sorted(PROVIDER_KEY_NAMES - HEADER_CARRIED_KEY_NAMES)
# Every character ``str.splitlines`` breaks on: each one could end a keys.env line.
_LINE_BREAKS = (
    "\n",
    "\r",
    "\x0b",
    "\x0c",
    "\x1c",
    "\x1d",
    "\x1e",
    "\x85",
    "\u2028",
    "\u2029",
)


@pytest.fixture()
def writer() -> KeysEnvWriter:
    return KeysEnvWriter()


def _messages(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [r.getMessage() for r in caplog.records]


def test_four_header_carried_keys_are_checked() -> None:
    """The strict set is the two API keys plus the two AWS header values."""
    assert _HEADER_KEYS == [
        "AWS_ACCESS_KEY_ID",
        "AWS_SESSION_TOKEN",
        "ELEVENLABS_API_KEY",
        "OPENAI_API_KEY",
    ]


def test_line_break_list_matches_splitlines() -> None:
    """The parametrized breaks are exactly the ones ``str.splitlines`` honours."""
    honoured = {chr(c) for c in range(0x110000) if len(f"a{chr(c)}b".splitlines()) > 1}
    assert honoured == set(_LINE_BREAKS)


@pytest.mark.parametrize("key", _HEADER_KEYS)
def test_header_carried_refusal_is_logged_without_the_value(
    writer: KeysEnvWriter,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    key: str,
) -> None:
    """Each header-carried key refuses a space, logs it by name, never the value."""
    keys_path = tmp_path / "keys.env"
    with caplog.at_level(logging.WARNING, logger=_LOGGER):
        writer.write({key: "abc def"}, keys_path)
    assert key not in keys_path.read_text()
    messages = _messages(caplog)
    assert any(key in m and "refused" in m for m in messages)
    assert all("abc" not in m and "def" not in m for m in messages)


@pytest.mark.parametrize(
    ("bad_value", "char_class", "position"),
    [
        pytest.param("sk-abc def", "a space", 7, id="inner-space"),
        pytest.param("sk-ab\tc", "a non-printable character", 6, id="inner-tab"),
        pytest.param("sk-a\rbc", "a line break", 5, id="inner-cr"),
        pytest.param("\u201csk-abc\u201d", "a non-ASCII character", 1, id="smart"),
        pytest.param("sk-\u00e9abc", "a non-ASCII character", 4, id="non-ascii"),
        pytest.param("sk-abc\x7fx", "a non-printable character", 7, id="delete"),
    ],
)
def test_header_refusal_names_class_and_position_never_the_character(
    writer: KeysEnvWriter,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    bad_value: str,
    char_class: str,
    position: int,
) -> None:
    """A refusal says which class of character, where, and that the old value stays."""
    keys_path = tmp_path / "keys.env"
    keys_path.write_text("ELEVENLABS_API_KEY=sk-previous\n")
    with caplog.at_level(logging.WARNING, logger=_LOGGER):
        writer.write({"ELEVENLABS_API_KEY": bad_value}, keys_path)
    assert "ELEVENLABS_API_KEY=sk-previous" in keys_path.read_text()
    (message,) = _messages(caplog)
    assert f"{char_class} at position {position}" in message
    assert "kept the stored value" in message
    assert "sk-abc" not in message
    # The prose has spaces of its own; every other offending character is absent.
    assert bad_value[position - 1] not in message.replace(" ", "")


def test_refused_key_with_nothing_stored_says_its_provider_has_no_credential(
    writer: KeysEnvWriter, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """With no stored value to fall back on, the refusal says so."""
    keys_path = tmp_path / "keys.env"
    with caplog.at_level(logging.WARNING, logger=_LOGGER):
        writer.write({"OPENAI_API_KEY": "sk abc"}, keys_path)
    (message,) = _messages(caplog)
    assert "OPENAI_API_KEY" in message
    assert "its provider has no credential" in message
    assert "kept" not in message


@pytest.mark.parametrize("key", _SETTING_KEYS)
@pytest.mark.parametrize(
    "brk", [pytest.param(b, id=f"U+{ord(b):04X}") for b in _LINE_BREAKS]
)
def test_setting_refuses_every_line_break(
    writer: KeysEnvWriter,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    key: str,
    brk: str,
) -> None:
    """No Unicode line break smuggles a second ``key=value`` line into keys.env."""
    keys_path = tmp_path / "keys.env"
    with caplog.at_level(logging.WARNING, logger=_LOGGER):
        writer.write({key: f"x{brk}OPENAI_API_KEY=injected"}, keys_path)
    content = keys_path.read_text(encoding="utf-8")
    assert "injected" not in content
    assert parse_keys_env(content) == {}
    messages = _messages(caplog)
    assert any(key in m and "a line break at position 2" in m for m in messages)
    assert all("injected" not in m for m in messages)


@pytest.mark.parametrize("key", _SETTING_KEYS)
def test_setting_keeps_an_interior_space(
    writer: KeysEnvWriter,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    key: str,
) -> None:
    """A value that never goes in a header may hold a space."""
    keys_path = tmp_path / "keys.env"
    with caplog.at_level(logging.WARNING, logger=_LOGGER):
        writer.write({key: "my value"}, keys_path)
    assert f"{key}=my value" in keys_path.read_text()
    assert caplog.records == []


@pytest.mark.parametrize("key", sorted(PROVIDER_KEY_NAMES))
@pytest.mark.parametrize(
    "blank",
    [
        pytest.param(" ", id="space"),
        pytest.param("\t", id="tab"),
        pytest.param(" \t \r\n", id="mixed"),
        pytest.param("\u3000", id="ideographic-space"),
    ],
)
def test_whitespace_only_value_keeps_the_stored_value(
    writer: KeysEnvWriter,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    key: str,
    blank: str,
) -> None:
    """Whitespace alone is an empty value: keep-and-warn, never a silent wipe."""
    keys_path = tmp_path / "keys.env"
    keys_path.write_text(f"{key}=stored1\n")
    with caplog.at_level(logging.WARNING, logger=_LOGGER):
        writer.write({key: blank}, keys_path)
    assert f"{key}=stored1" in keys_path.read_text()
    (message,) = _messages(caplog)
    assert key in message
    assert "kept the stored value" in message


@pytest.mark.parametrize("key", sorted(PROVIDER_KEY_NAMES))
@pytest.mark.parametrize(
    "padded",
    [
        pytest.param("sk-abc ", id="trailing-space"),
        pytest.param("\tsk-abc", id="leading-tab"),
        pytest.param("sk-abc\r", id="trailing-cr"),
        pytest.param(" sk-abc\n", id="both-ends"),
    ],
)
def test_surrounding_whitespace_is_stripped_before_validation(
    writer: KeysEnvWriter,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    key: str,
    padded: str,
) -> None:
    """``'sk-abc '`` is written as ``sk-abc``, as both readers would read it."""
    keys_path = tmp_path / "keys.env"
    with caplog.at_level(logging.WARNING, logger=_LOGGER):
        writer.write({key: padded}, keys_path)
    assert parse_keys_env(keys_path.read_text()) == {key: "sk-abc"}
    assert f"{key}=sk-abc\n" in keys_path.read_text()
    assert caplog.records == []


def test_interior_characters_are_still_checked_after_stripping(
    writer: KeysEnvWriter, tmp_path: Path
) -> None:
    """Stripping trims the ends only; an interior space still fails the header check."""
    keys_path = tmp_path / "keys.env"
    writer.write({"OPENAI_API_KEY": " sk abc "}, keys_path)
    assert "OPENAI_API_KEY" not in keys_path.read_text()
