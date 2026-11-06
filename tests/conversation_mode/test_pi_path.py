"""Tests for :class:`PiPath`: pi's own tool-path normalization, mirrored."""

from __future__ import annotations

from pathlib import Path

import pytest

from punt_vox.voxd.conversation_mode.pi_path import PiPath


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("@.env.local", ".env.local"),
        ("~", str(Path.home())),
        ("~/.ssh/id_rsa", str(Path.home() / ".ssh" / "id_rsa")),
        ("file:///etc/my%20file", "/etc/my file"),
        ("a\u00a0b", "a b"),
        ("~user/x", "~user/x"),
        ("plain/path", "plain/path"),
    ],
)
def test_normalize_matches_pi(raw: str, expected: str) -> None:
    assert PiPath.normalize(raw) == expected


def test_a_relative_path_resolves_against_the_cwd(tmp_path: Path) -> None:
    assert PiPath.resolve("a/../b.txt", tmp_path) == tmp_path / "b.txt"


def test_an_absolute_path_ignores_the_cwd(tmp_path: Path) -> None:
    assert PiPath.resolve("/etc/hosts", tmp_path) == Path("/etc/hosts")
