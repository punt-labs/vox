"""Tests for :class:`Ripgrep`: find rg the way the pi-side guard finds it."""

from __future__ import annotations

from pathlib import Path

import pytest

from punt_vox.voxd.conversation_mode.call_agent_errors import CallAgentSpawnError
from punt_vox.voxd.conversation_mode.ripgrep import Ripgrep


def _executable(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\n")
    path.chmod(0o755)
    return path


def test_pi_s_own_rg_comes_first(tmp_path: Path) -> None:
    own = _executable(tmp_path / "agent" / "bin" / "rg")
    _executable(tmp_path / "bin" / "rg")
    env = {
        "PI_CODING_AGENT_DIR": str(tmp_path / "agent"),
        "PATH": str(tmp_path / "bin"),
    }
    assert Ripgrep.locate(env) == own


def test_the_default_agent_dir_is_under_home(tmp_path: Path) -> None:
    own = _executable(tmp_path / ".pi" / "agent" / "bin" / "rg")
    assert Ripgrep.locate({"HOME": str(tmp_path), "PATH": ""}) == own


def test_rg_on_path_is_found(tmp_path: Path) -> None:
    on_path = _executable(tmp_path / "bin" / "rg")
    env = {"HOME": str(tmp_path), "PATH": str(tmp_path / "bin")}
    assert Ripgrep.locate(env) == on_path


def test_no_rg_anywhere_refuses_with_how_to_install_it(tmp_path: Path) -> None:
    env = {"HOME": str(tmp_path), "PATH": str(tmp_path / "empty")}
    with pytest.raises(CallAgentSpawnError, match=r"ripgrep.*install"):
        Ripgrep.locate(env)
