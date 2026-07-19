"""Tests for punt_vox.voxd.config -- daemon paths, key loading, startup permissions."""
# pyright: reportPrivateUsage=false

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from punt_vox.log_handlers import PrivateRotatingFileHandler
from punt_vox.paths import ensure_user_dirs
from punt_vox.voxd.config import (
    DaemonConfig,
    _config_dir,
    _log_dir,
    _run_dir,
)

if TYPE_CHECKING:
    import pytest


def _load_keys(config_dir: Path) -> frozenset[str]:
    """Build a DaemonConfig and load keys -- replaces the deleted wrapper."""
    cfg = DaemonConfig(run_dir=_run_dir(), config_dir=config_dir, log_dir=_log_dir())
    return cfg.load_keys()


class TestVoxdPaths:
    """voxd must read/write state under ~/.punt-labs/vox/, not FHS paths."""

    def test_config_dir_is_user_state(self) -> None:
        assert _config_dir() == Path.home() / ".punt-labs" / "vox"

    def test_log_dir_is_user_state_logs(self) -> None:
        assert _log_dir() == Path.home() / ".punt-labs" / "vox" / "logs"

    def test_run_dir_is_user_state_run(self) -> None:
        assert _run_dir() == Path.home() / ".punt-labs" / "vox" / "run"

    def test_paths_do_not_leak_fhs_dirs(self) -> None:
        forbidden = ("/etc/vox", "/var/log/vox", "/var/run/vox", "/var/cache/vox")
        for helper in (_config_dir, _log_dir, _run_dir):
            resolved = str(helper())
            for bad in forbidden:
                assert bad not in resolved, (
                    f"{helper.__name__} returned forbidden FHS path {resolved}"
                )


class TestLoadKeys:
    """DaemonConfig.load_keys must read from the per-user state dir."""

    def test_loads_keys_from_config_dir(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Keys in keys.env are copied into os.environ."""
        keys_file = tmp_path / "keys.env"
        keys_file.write_text(
            "# header\n"
            "ELEVENLABS_API_KEY=sk-eleven-test\n"
            "OPENAI_API_KEY=sk-openai-test\n"
        )
        monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)

        loaded = _load_keys(tmp_path)

        assert "ELEVENLABS_API_KEY" in loaded
        assert "OPENAI_API_KEY" in loaded
        import os as _os

        assert _os.environ["ELEVENLABS_API_KEY"] == "sk-eleven-test"
        assert _os.environ["OPENAI_API_KEY"] == "sk-openai-test"

    def test_missing_keys_file_returns_empty(self, tmp_path: Path) -> None:
        """No keys.env file means no loaded keys — not a crash."""
        loaded = _load_keys(tmp_path)
        assert loaded == frozenset()

    def test_existing_env_not_overwritten(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Keys already in os.environ are preserved (env wins over file)."""
        keys_file = tmp_path / "keys.env"
        keys_file.write_text("ELEVENLABS_API_KEY=from-file\n")
        monkeypatch.setenv("ELEVENLABS_API_KEY", "from-env")

        loaded = _load_keys(tmp_path)

        assert "ELEVENLABS_API_KEY" not in loaded
        import os as _os

        assert _os.environ["ELEVENLABS_API_KEY"] == "from-env"

    def test_ignores_unknown_keys(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Only known provider keys are loaded — random env vars are ignored."""
        keys_file = tmp_path / "keys.env"
        keys_file.write_text("HACKER_BACKDOOR=root\nELEVENLABS_API_KEY=sk-real\n")
        monkeypatch.delenv("HACKER_BACKDOOR", raising=False)
        monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)

        loaded = _load_keys(tmp_path)

        assert "HACKER_BACKDOOR" not in loaded
        assert "ELEVENLABS_API_KEY" in loaded
        import os as _os

        assert "HACKER_BACKDOOR" not in _os.environ


class TestVoxdStartupEnforces0700:
    """voxd.main() must tighten existing state dirs to mode 0700.

    Copilot finding 3048101870 on PR #162: the existing helpers used
    ``Path.mkdir(..., exist_ok=True)`` which respects the process
    umask (``0022`` on most shells -> directories created as ``0755``).
    ``paths.ensure_user_dirs()`` creates-or-chmods each subdir with an
    explicit ``0o700`` so pre-existing directories with looser
    permissions are tightened on the next startup.
    """

    def test_ensure_user_dirs_tightens_preexisting_logs_dir(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A pre-existing 0755 logs dir is chmod'd to 0700."""
        import stat as _stat

        fake_home = tmp_path / "home" / "user"
        state_root = fake_home / ".punt-labs" / "vox"
        logs = state_root / "logs"
        logs.mkdir(parents=True)
        # Pre-create with loose umask-style permissions. This is what
        # an older voxd left behind before the 0700 contract.
        logs.chmod(0o755)
        state_root.chmod(0o755)
        assert _stat.S_IMODE(logs.stat().st_mode) == 0o755

        monkeypatch.setenv("HOME", str(fake_home))

        # The no-arg form resolves the current user's state dir.
        ensure_user_dirs()

        # Every subdir and the root are now 0700.
        for target in (state_root, logs, state_root / "run", state_root / "cache"):
            mode = _stat.S_IMODE(target.stat().st_mode)
            assert mode == 0o700, (
                f"{target} mode is {oct(mode)} after ensure_user_dirs(); expected 0o700"
            )

    def test_ensure_user_dirs_creates_all_subdirs_when_missing(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Fresh ``$HOME`` with no state dir: ensure_user_dirs creates it."""
        import stat as _stat

        fake_home = tmp_path / "home" / "fresh"
        fake_home.mkdir(parents=True)
        monkeypatch.setenv("HOME", str(fake_home))

        ensure_user_dirs()

        state_root = fake_home / ".punt-labs" / "vox"
        assert state_root.is_dir()
        for name in ("logs", "run", "cache"):
            d = state_root / name
            assert d.is_dir()
            mode = _stat.S_IMODE(d.stat().st_mode)
            assert mode == 0o700


class TestVoxdPathHelpersArePure:
    """``_log_dir``, ``_run_dir``, ``_config_dir`` must be side-effect free.

    Closes Copilot 3047999704 (mode 0755 leak from `_log_dir`) and
    Cursor Bugbot 3048161272 (helper is side-effectful, inconsistent
    with sibling pure-path helpers). Once ``voxd.main()`` calls
    ``paths.ensure_user_dirs()`` at startup, the helpers no longer
    need to create or chmod anything -- they are pure path views.
    """

    def test_log_dir_is_pure(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """``_log_dir()`` must not create or modify the directory.

        Calling it twice on a tmp HOME with no pre-existing state dir
        must return the correct path on both calls and leave the
        filesystem untouched.
        """
        fake_home = tmp_path / "home" / "user"
        fake_home.mkdir(parents=True)
        monkeypatch.setenv("HOME", str(fake_home))

        expected = fake_home / ".punt-labs" / "vox" / "logs"
        assert not expected.exists()

        result_1 = _log_dir()
        result_2 = _log_dir()

        assert result_1 == expected
        assert result_2 == expected
        # The helper must not have created the directory.
        assert not expected.exists(), (
            f"_log_dir() created {expected} as a side effect -- "
            "helper should be pure path resolution"
        )

    def test_run_dir_is_pure(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """``_run_dir()`` must not create or modify the directory."""
        fake_home = tmp_path / "home" / "user"
        fake_home.mkdir(parents=True)
        monkeypatch.setenv("HOME", str(fake_home))

        expected = fake_home / ".punt-labs" / "vox" / "run"
        assert not expected.exists()

        result = _run_dir()

        assert result == expected
        assert not expected.exists()

    def test_config_dir_is_pure(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """``_config_dir()`` must not create or modify the directory."""
        fake_home = tmp_path / "home" / "user"
        fake_home.mkdir(parents=True)
        monkeypatch.setenv("HOME", str(fake_home))

        expected = fake_home / ".punt-labs" / "vox"
        # The state root parent does not exist yet.
        assert not expected.exists()

        result = _config_dir()

        assert result == expected
        assert not expected.exists()


class TestConfigureLogging:
    """configure_logging installs one private file handler and no stderr sink.

    The daemon's private ``voxd.log`` is only private if nothing tees the same
    records to stderr -- a stray ``StreamHandler`` would have launchd's
    ``StandardErrorPath`` or the systemd journal capture an unprotected copy.
    """

    def test_single_private_file_handler_no_stderr(self, tmp_path: Path) -> None:
        root = logging.getLogger()
        saved_handlers = root.handlers[:]
        saved_level = root.level
        cfg = DaemonConfig(run_dir=tmp_path, config_dir=tmp_path, log_dir=tmp_path)
        try:
            cfg.configure_logging()

            assert len(root.handlers) == 1
            assert isinstance(root.handlers[0], PrivateRotatingFileHandler)
            # A FileHandler *is* a StreamHandler subclass, so guard specifically
            # against a bare stderr/stdout StreamHandler that is not file-backed.
            bare_stream_handlers = [
                h
                for h in root.handlers
                if isinstance(h, logging.StreamHandler)
                and not isinstance(h, logging.FileHandler)
            ]
            assert bare_stream_handlers == []
        finally:
            for handler in root.handlers[:]:
                handler.close()
            root.handlers[:] = saved_handlers
            root.setLevel(saved_level)

    def test_untightenable_log_is_warned_in_voxd_log(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A file the daemon cannot chmod surfaces as a WARNING in voxd.log.

        The daemon has no stderr sink, so an un-tightenable log that vanished
        silently would leave nothing to grep. The post-configure warning lands
        in the now-live ``voxd.log`` itself, naming the still-loose path.
        """
        (tmp_path / "voxd.log").write_text("existing\n")

        def _deny_chmod(self: Path, mode: int) -> None:
            raise PermissionError(f"cannot chmod {self}")

        monkeypatch.setattr(Path, "chmod", _deny_chmod)
        root = logging.getLogger()
        saved_handlers = root.handlers[:]
        saved_level = root.level
        cfg = DaemonConfig(run_dir=tmp_path, config_dir=tmp_path, log_dir=tmp_path)
        try:
            cfg.configure_logging()

            contents = (tmp_path / "voxd.log").read_text()
            assert "could not enforce 0600 on log file(s)" in contents
            assert "voxd.log" in contents
        finally:
            for handler in root.handlers[:]:
                handler.close()
            root.handlers[:] = saved_handlers
            root.setLevel(saved_level)
