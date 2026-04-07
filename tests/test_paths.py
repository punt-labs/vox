"""Tests for punt_vox.paths -- shared path resolution for user state.

voxd and service both need the same view of where per-user state lives.
These tests pin the contract: everything under ``~/.punt-labs/vox/``, no
per-OS splits, no FHS system paths.
"""
# pyright: reportPrivateUsage=false

from __future__ import annotations

import os
import stat
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

if TYPE_CHECKING:
    import pytest

from punt_vox.paths import (
    cache_dir,
    config_dir,
    ensure_user_dirs,
    installing_user,
    keys_env_file,
    log_dir,
    run_dir,
    user_state_dir,
    user_state_dir_for,
)

# ---------------------------------------------------------------------------
# Basic path layout
# ---------------------------------------------------------------------------


def test_user_state_dir_under_home() -> None:
    """user_state_dir is always ~/.punt-labs/vox/ -- no per-OS split."""
    assert user_state_dir() == Path.home() / ".punt-labs" / "vox"


def test_config_dir_is_state_dir() -> None:
    """keys.env lives directly in the state dir, not a subdir."""
    assert config_dir() == user_state_dir()


def test_log_dir_under_state() -> None:
    assert log_dir() == user_state_dir() / "logs"


def test_run_dir_under_state() -> None:
    assert run_dir() == user_state_dir() / "run"


def test_cache_dir_under_state() -> None:
    assert cache_dir() == user_state_dir() / "cache"


def test_keys_env_file_in_config_dir() -> None:
    assert keys_env_file() == config_dir() / "keys.env"


def test_no_fhs_paths_leak_into_helpers() -> None:
    """None of the helpers may return /etc, /var, or brew prefix paths."""
    forbidden_prefixes = ("/etc/", "/var/", "/opt/homebrew/etc", "/usr/local/etc")
    for helper in (user_state_dir, config_dir, log_dir, run_dir, cache_dir):
        resolved = str(helper())
        for prefix in forbidden_prefixes:
            assert not resolved.startswith(prefix), (
                f"{helper.__name__} returned forbidden path {resolved}"
            )


# ---------------------------------------------------------------------------
# user_state_dir_for(user) -- cross-user resolution for sudo installs
# ---------------------------------------------------------------------------


@patch("punt_vox.paths.pwd.getpwnam")
def test_user_state_dir_for_reads_home_from_pwd(mock_getpwnam: MagicMock) -> None:
    """The target user's home dir drives state_dir, not the current user."""
    mock_pw = MagicMock()
    mock_pw.pw_dir = "/home/jfreeman"
    mock_getpwnam.return_value = mock_pw
    assert user_state_dir_for("jfreeman") == Path("/home/jfreeman/.punt-labs/vox")
    mock_getpwnam.assert_called_once_with("jfreeman")


@patch("punt_vox.paths.pwd.getpwnam", side_effect=KeyError("no such user"))
def test_user_state_dir_for_unknown_user_falls_back_to_home(
    _mock_getpwnam: MagicMock,
) -> None:
    """Unknown users fall back to current Path.home() -- don't crash."""
    result = user_state_dir_for("ghost")
    assert result == Path.home() / ".punt-labs" / "vox"


# ---------------------------------------------------------------------------
# installing_user
# ---------------------------------------------------------------------------


def test_installing_user_uses_sudo_user_when_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SUDO_USER takes precedence over the current process user."""
    monkeypatch.setenv("SUDO_USER", "jfreeman")
    assert installing_user() == "jfreeman"


def test_installing_user_falls_back_to_current_user(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without SUDO_USER, returns the process user."""
    monkeypatch.delenv("SUDO_USER", raising=False)
    assert installing_user()  # non-empty string


# ---------------------------------------------------------------------------
# ensure_user_dirs -- directory creation + mode permissions
# ---------------------------------------------------------------------------


def test_ensure_user_dirs_creates_all_subdirs(tmp_path: Path) -> None:
    """All four subdirs are created under the target state dir."""
    state = tmp_path / "state"
    ensure_user_dirs(state)
    assert state.is_dir()
    assert (state / "logs").is_dir()
    assert (state / "run").is_dir()
    assert (state / "cache").is_dir()


def test_ensure_user_dirs_sets_all_subdirs_mode_0700(tmp_path: Path) -> None:
    """Every state subdir is mode 0700 — same policy as ``~/.ssh``.

    Every directory holds private per-user data: keys, spoken-text
    logs, auth token, cached audio. Tighten all of them, not just run.
    """
    state = tmp_path / "state"
    ensure_user_dirs(state)
    for name in ("", "logs", "run", "cache"):
        target = state / name if name else state
        mode = stat.S_IMODE(os.stat(target).st_mode)
        assert mode == 0o700, f"{target} mode is {oct(mode)}, expected 0o700"


def test_ensure_user_dirs_is_idempotent(tmp_path: Path) -> None:
    """Running twice does not crash and does not lower permissions."""
    state = tmp_path / "state"
    ensure_user_dirs(state)
    ensure_user_dirs(state)
    assert (state / "run").is_dir()
    for name in ("", "logs", "run", "cache"):
        target = state / name if name else state
        mode = stat.S_IMODE(os.stat(target).st_mode)
        assert mode == 0o700
