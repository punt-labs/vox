"""Tests for the descriptor-pinned directory (``PinnedDirectory``)."""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from punt_vox.pinned_directory import PinnedDirectory


def test_opened_creates_a_missing_directory(tmp_path: Path) -> None:
    target = tmp_path / "a" / ".claude"
    with PinnedDirectory.opened(target) as directory:
        assert directory.read_text("settings.json") == ""
    assert target.is_dir()


def test_opened_refuses_a_symlinked_directory(tmp_path: Path) -> None:
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real)
    with pytest.raises(ValueError, match="symlinked"), PinnedDirectory.opened(link):
        pass


def test_replace_text_round_trips_and_leaves_no_temp(tmp_path: Path) -> None:
    with PinnedDirectory.opened(tmp_path) as directory:
        directory.replace_text("settings.json", "{}\n")
        assert directory.read_text("settings.json") == "{}\n"
    assert [p.name for p in tmp_path.iterdir()] == ["settings.json"]


def test_replace_text_gives_a_new_file_0644(tmp_path: Path) -> None:
    with PinnedDirectory.opened(tmp_path) as directory:
        directory.replace_text("settings.json", "{}")
    assert stat.S_IMODE((tmp_path / "settings.json").stat().st_mode) == 0o644


def test_replace_text_keeps_an_existing_mode(tmp_path: Path) -> None:
    target = tmp_path / "settings.json"
    target.write_text("{}", encoding="utf-8")
    target.chmod(0o600)
    with PinnedDirectory.opened(tmp_path) as directory:
        directory.replace_text("settings.json", '{"a": 1}')
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert target.read_text(encoding="utf-8") == '{"a": 1}'


def test_read_text_refuses_a_symlinked_member(tmp_path: Path) -> None:
    victim = tmp_path / "victim.json"
    victim.write_text("secret", encoding="utf-8")
    pinned = tmp_path / "pinned"
    pinned.mkdir()
    (pinned / "settings.json").symlink_to(victim)
    with (
        PinnedDirectory.opened(pinned) as directory,
        pytest.raises(ValueError, match="symlinked"),
    ):
        directory.read_text("settings.json")


def test_replace_text_refuses_a_symlinked_member(tmp_path: Path) -> None:
    victim = tmp_path / "victim.json"
    victim.write_text("{}", encoding="utf-8")
    pinned = tmp_path / "pinned"
    pinned.mkdir()
    (pinned / "settings.json").symlink_to(victim)
    with (
        PinnedDirectory.opened(pinned) as directory,
        pytest.raises(ValueError, match="symlinked"),
    ):
        directory.replace_text("settings.json", "pwned")
    assert victim.read_text(encoding="utf-8") == "{}"
    assert sorted(p.name for p in pinned.iterdir()) == ["settings.json"]


def test_read_text_refuses_a_fifo_without_hanging(tmp_path: Path) -> None:
    os.mkfifo(tmp_path / "settings.json")
    with (
        PinnedDirectory.opened(tmp_path) as directory,
        pytest.raises(ValueError, match="not a regular file"),
    ):
        directory.read_text("settings.json")


def test_replace_text_removes_the_temp_when_the_rename_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def failing_replace(*_args: object, **_kwargs: object) -> None:
        raise OSError("rename failed")

    monkeypatch.setattr("punt_vox.pinned_directory.os.replace", failing_replace)
    with (
        PinnedDirectory.opened(tmp_path) as directory,
        pytest.raises(OSError, match="rename failed"),
    ):
        directory.replace_text("settings.json", "{}")
    assert list(tmp_path.iterdir()) == []
