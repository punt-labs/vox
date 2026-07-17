"""Tests for the per-user privacy guard (src/punt_vox/private_state.py)."""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING

from punt_vox.private_state import PrivateState

if TYPE_CHECKING:
    import pytest

_OPEN_FLAGS = os.O_WRONLY | os.O_APPEND | os.O_CREAT


def _mode(path: Path) -> int:
    """Return the world/group permission bits of *path* (0 means private)."""
    return path.stat().st_mode & 0o077


class TestEnsurePrivateTree:
    """Every ancestor the guard creates lands 0o700; the parent is re-tightened."""

    def test_creates_missing_ancestors_private(self, tmp_path: Path) -> None:
        target = tmp_path / "a" / "b" / "c" / "state.log"
        PrivateState(target).ensure_private_tree()
        for created in (tmp_path / "a", tmp_path / "a" / "b", target.parent):
            assert created.is_dir()
            assert _mode(created) == 0

    def test_tightens_loose_existing_parent(self, tmp_path: Path) -> None:
        """A parent a peer created group/other-readable is forced 0o700."""
        parent = tmp_path / "logs"
        parent.mkdir()
        parent.chmod(0o755)
        PrivateState(parent / "state.log").ensure_private_tree()
        assert _mode(parent) == 0

    def test_survives_peer_creating_ancestor_mid_walk(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A peer winning the race between ``exists()`` and ``mkdir`` is absorbed.

        ``_missing_ancestors`` is computed up front, so a directory it plans to
        create can be created by a peer before this process's own ``mkdir``
        runs. Without ``exist_ok=True`` that ``mkdir`` raises
        ``FileExistsError``, which ``record()`` swallows as ``OSError`` --
        silently dropping the trace even though the directory now exists and the
        append would succeed. ``exist_ok=True`` absorbs the loss; the following
        chmod still lands the peer-created dir at 0o700.
        """
        target = tmp_path / "a" / "b" / "state.log"
        real_mkdir = Path.mkdir

        def racing_mkdir(self: Path, *args: object, **kwargs: object) -> None:
            if not self.exists():  # a peer wins the race, with loose perms
                real_mkdir(self)
            real_mkdir(self, *args, **kwargs)  # type: ignore[arg-type]

        monkeypatch.setattr("punt_vox.private_state.Path.mkdir", racing_mkdir)
        PrivateState(target).ensure_private_tree()  # no FileExistsError
        for directory in (tmp_path / "a", target.parent):
            assert directory.is_dir()
            assert _mode(directory) == 0  # peer's loose dir was still tightened

    def test_second_ensure_is_a_noop(self, tmp_path: Path) -> None:
        """Calling ensure_private_tree twice must not raise on the second pass."""
        target = tmp_path / "a" / "b" / "state.log"
        guard = PrivateState(target)
        guard.ensure_private_tree()
        guard.ensure_private_tree()  # every ancestor now exists -- must not raise
        assert _mode(target.parent) == 0

    def test_swallows_chmod_denial(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A dir we can append to but can't chmod does not abort the tighten.

        The 0o700 hardening is defense-in-depth; a ``chmod`` we lack rights for
        (a dir another user owns in a shared setup) is logged and swallowed so
        the caller's write is never blocked by best-effort hardening.
        """
        parent = tmp_path / "logs"
        parent.mkdir()

        def deny_chmod(_self: object, _mode: int) -> None:
            raise PermissionError(1, "Operation not permitted")

        monkeypatch.setattr("punt_vox.private_state.Path.chmod", deny_chmod)
        PrivateState(parent / "state.log").ensure_private_tree()  # no exception


class TestOpenPrivate:
    """The opened fd is forced 0o600, even for a pre-existing loose file."""

    def test_creates_file_private_and_writable(self, tmp_path: Path) -> None:
        target = tmp_path / "state.log"
        fd = PrivateState(target).open_private(_OPEN_FLAGS)
        try:
            os.write(fd, b"line\n")
        finally:
            os.close(fd)
        assert _mode(target) == 0
        assert target.read_text(encoding="utf-8") == "line\n"

    def test_tightens_loose_existing_file(self, tmp_path: Path) -> None:
        target = tmp_path / "state.log"
        target.touch()
        target.chmod(0o644)
        os.close(PrivateState(target).open_private(_OPEN_FLAGS))
        assert _mode(target) == 0

    def test_swallows_fchmod_denial(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A raising ``fchmod`` still yields a usable fd -- privacy is best-effort."""
        target = tmp_path / "state.log"

        def deny_fchmod(_fd: int, _mode: int) -> None:
            raise PermissionError(1, "Operation not permitted")

        monkeypatch.setattr("punt_vox.private_state.os.fchmod", deny_fchmod)
        fd = PrivateState(target).open_private(_OPEN_FLAGS)
        try:
            assert os.write(fd, b"x\n") == 2
        finally:
            os.close(fd)


class TestNearestExistingAncestor:
    """The probe resolves the closest existing directory above an absent file."""

    def test_returns_immediate_parent_when_present(self, tmp_path: Path) -> None:
        guard = PrivateState(tmp_path / "state.log")
        assert guard.nearest_existing_ancestor() == tmp_path

    def test_walks_up_past_absent_dirs(self, tmp_path: Path) -> None:
        target = tmp_path / "x" / "y" / "state.log"
        assert PrivateState(target).nearest_existing_ancestor() == tmp_path
