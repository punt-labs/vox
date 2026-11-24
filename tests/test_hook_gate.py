"""Behavioral tests for the per-repo enablement gate in the audio hooks.

Each audio session hook is silent unless the repo opted in with `vox enable`
(the `.punt-labs/vox/enabled` marker) AND the `vox` CLI is installed. These
tests drive the real shell scripts as subprocesses and assert the three gate
outcomes: marker present -> the hook reaches vox; marker absent -> silent
exit 0; vox absent -> silent exit 0. A stub `vox` on PATH records whether the
hook got past the gate, so no real synthesis runs.
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import tempfile
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

_HOOKS_DIR = Path(__file__).resolve().parent.parent / "plugin" / "hooks"

# hook script -> the hook_event_name it dispatches on. subagent.sh only calls
# vox for Subagent* events; the rest ignore the event but a realistic value is
# passed anyway.
_GATED_HOOKS = {
    "notify.sh": "Stop",
    "notify-permission.sh": "Notification",
    "acknowledge.sh": "UserPromptSubmit",
    "vibe-nudge.sh": "UserPromptSubmit",
    "pre-compact.sh": "PreCompact",
    "farewell.sh": "SessionEnd",
    "subagent.sh": "SubagentStop",
}


def _write_vox_stub(bin_dir: Path, sentinel: Path) -> None:
    """Install a fake `vox` in bin_dir that touches sentinel when invoked."""
    stub = bin_dir / "vox"
    stub.write_text(
        f'#!/usr/bin/env bash\nprintf invoked > "{sentinel}"\n',
        encoding="utf-8",
    )
    stub.chmod(stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _write_failing_git(bin_dir: Path) -> None:
    """Install a `git` stub on PATH that always exits non-zero.

    Simulates git being unavailable (or the cwd not being a repo) so the hook
    gate falls back to walking parent directories for the marker.
    """
    stub = bin_dir / "git"
    stub.write_text("#!/usr/bin/env bash\nexit 1\n", encoding="utf-8")
    stub.chmod(stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _path_without_vox() -> str:
    """The current PATH with every directory that contains a `vox` removed.

    Drops each whole directory, so a host that colocates `vox` with tools the
    hook needs (git, jq) loses those tools too -- a loud, if misdirecting,
    failure rather than a silent one. Only absolute entries are kept: relative
    entries ("", ".", "./x", "..") resolve against the hook's cwd, which
    could still resolve a `./vox`.
    """
    entries = os.environ.get("PATH", "").split(os.pathsep)
    return os.pathsep.join(
        d for d in entries if (p := Path(d)).is_absolute() and not (p / "vox").exists()
    )


def _run_hook(
    name: str,
    cwd: str,
    event: str,
    env: dict[str, str],
    *,
    launch_dir: Path | None = None,
) -> int:
    payload = json.dumps({"cwd": cwd, "hook_event_name": event})
    result = subprocess.run(
        ["bash", str(_HOOKS_DIR / name)],
        input=payload,
        text=True,
        capture_output=True,
        cwd=launch_dir,
        env=env,
        check=False,
        timeout=3,
    )
    return result.returncode


def _make_repo(parent: Path) -> Path:
    # A standalone git worktree so `git rev-parse` stops here rather than
    # walking up to the enclosing repo (pytest's tmp lives under the repo tree).
    repo = parent / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    return repo


def _mark_enabled(repo: Path) -> None:
    marker = repo / ".punt-labs" / "vox" / "enabled"
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("", encoding="utf-8")


def _wait_for_panel_spawn(sentinel: Path, *, timeout: float = 10.0) -> str:
    """Read a complete invocation from the backgrounded panel stub.

    The shell opens the sentinel for append before writing the record, so
    the file can exist before the newline-terminated invocation is visible.

    10s, not 2s: a 15-run isolated rerun of this poll (no concurrent
    load) still missed a 2s deadline once, so 2s is not a safe bound for
    OS scheduling of an already-disowned background process even at rest.
    The loop returns as soon as the record is ready.
    """
    deadline = time.monotonic() + timeout
    while True:
        try:
            invocations = sentinel.read_text(encoding="utf-8")
        except FileNotFoundError:
            invocations = ""
        if invocations.endswith("\n"):
            return invocations
        if time.monotonic() > deadline:
            msg = f"timed out waiting for a complete panel invocation: {invocations!r}"
            raise AssertionError(msg)
        time.sleep(0.02)


def _path_without_vox_panel() -> str:
    """The current PATH with every directory that contains `vox-panel` removed.

    Drops each whole directory, so a host that colocates `vox-panel` with
    tools the hook needs (git, jq) loses those tools too -- a loud, if
    misdirecting, failure rather than a silent one. Only absolute entries are
    kept: relative entries ("", ".", "./x", "..") resolve against the hook's
    cwd, which could still resolve a `./vox-panel`.
    """
    entries = os.environ.get("PATH", "").split(os.pathsep)
    return os.pathsep.join(
        d
        for d in entries
        if (p := Path(d)).is_absolute() and not (p / "vox-panel").exists()
    )


# Sentinels from the no-spawn assertions, re-checked at module teardown. The
# spawn is backgrounded, and this file's own polling evidence (see
# _wait_for_panel_spawn) is that a record can land after a 2-second window. The
# per-test window gives fast, attributed feedback; this sweep is the guard
# sized to the observed tail.
_NO_SPAWN_SENTINELS: list[Path] = []


@pytest.fixture(autouse=True, scope="module")
def sweep_no_spawn_sentinels() -> Iterator[None]:
    """Fail the module if a no-spawn test's vox-panel record landed late.

    Three seconds comfortably exceeds the observed landing tail (a 2-second
    deadline was missed once in 15 quiet runs), and every test that ran after
    the registering one has already added its own elapsed time on top. With
    no registered sentinels (a ``-k`` run of unrelated classes) there is
    nothing to wait for, so the sleep is skipped.
    """
    yield
    if not _NO_SPAWN_SENTINELS:
        return
    time.sleep(3.0)
    spawned = {
        s: s.read_text(encoding="utf-8") for s in _NO_SPAWN_SENTINELS if s.exists()
    }
    assert not spawned, f"session-start.sh spawned vox-panels late: {spawned}"


@pytest.fixture
def isolated_root() -> Iterator[Path]:
    """A directory tree outside the repo, immune to the parent-marker walk.

    `.envrc` pins `TMPDIR` to `<repo>/.tmp/`, so pytest's `tmp_path` fixture
    nests under the real vox repo. A test asserting "no marker anywhere up to
    real /" would otherwise cross this project's own committed
    `.punt-labs/vox/enabled` marker and fail. Building under `/tmp` directly
    (bypassing `TMPDIR`) keeps the ancestor chain genuinely marker-free.
    """
    root = Path(tempfile.mkdtemp(dir="/tmp"))
    try:
        yield root
    finally:
        shutil.rmtree(root, ignore_errors=True)


@pytest.mark.subprocess
class TestHookGate:
    @pytest.mark.parametrize("name", sorted(_GATED_HOOKS))
    def test_enabled_repo_reaches_vox(self, name: str, tmp_path: Path) -> None:
        repo = _make_repo(tmp_path)
        _mark_enabled(repo)
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        sentinel = tmp_path / "sentinel"
        _write_vox_stub(bin_dir, sentinel)
        env = dict(os.environ)
        env["PATH"] = f"{bin_dir}{os.pathsep}{os.environ['PATH']}"

        rc = _run_hook(name, str(repo), _GATED_HOOKS[name], env)

        assert rc == 0
        assert sentinel.exists(), f"{name} did not reach vox in an enabled repo"

    @pytest.mark.parametrize("name", sorted(_GATED_HOOKS))
    def test_disabled_repo_is_silent(self, name: str, tmp_path: Path) -> None:
        repo = _make_repo(tmp_path)  # no marker written
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        sentinel = tmp_path / "sentinel"
        _write_vox_stub(bin_dir, sentinel)
        env = dict(os.environ)
        env["PATH"] = f"{bin_dir}{os.pathsep}{os.environ['PATH']}"

        rc = _run_hook(name, str(repo), _GATED_HOOKS[name], env)

        assert rc == 0
        assert not sentinel.exists(), f"{name} fired in a non-enabled repo"

    @pytest.mark.parametrize("name", sorted(_GATED_HOOKS))
    def test_missing_vox_is_silent(self, name: str, tmp_path: Path) -> None:
        repo = _make_repo(tmp_path)
        _mark_enabled(repo)  # enabled, but vox is not installed
        env = dict(os.environ)
        env["PATH"] = _path_without_vox()

        rc = _run_hook(name, str(repo), _GATED_HOOKS[name], env)

        assert rc == 0

    def test_marker_resolved_by_walking_parents_when_git_unavailable(
        self, tmp_path: Path
    ) -> None:
        # git is stubbed to fail (unavailable / not a repo). From a subdir, the
        # gate must walk parents for the marker instead of stopping at cwd -- a
        # cwd-only fallback would wrongly suppress audio in an enabled repo.
        root = tmp_path / "tree"
        marker = root / ".punt-labs" / "vox" / "enabled"
        marker.parent.mkdir(parents=True)
        marker.write_text("", encoding="utf-8")
        subdir = root / "src" / "nested"
        subdir.mkdir(parents=True)
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        sentinel = tmp_path / "sentinel"
        _write_vox_stub(bin_dir, sentinel)
        _write_failing_git(bin_dir)
        env = dict(os.environ)
        env["PATH"] = f"{bin_dir}{os.pathsep}{os.environ['PATH']}"

        rc = _run_hook("notify.sh", str(subdir), "Stop", env)

        assert rc == 0
        assert sentinel.exists(), "gate did not walk parents to the marker"

    def test_git_unavailable_and_no_marker_is_silent(self, isolated_root: Path) -> None:
        # git stubbed to fail and no marker in any parent: the parent walk bottoms
        # out at "/" and the hook exits silently rather than firing. Built under
        # isolated_root (real /tmp), not tmp_path, so the walk can't cross this
        # project's own committed enabled marker on its way to "/".
        subdir = isolated_root / "tree" / "src" / "nested"
        subdir.mkdir(parents=True)
        bin_dir = isolated_root / "bin"
        bin_dir.mkdir()
        sentinel = isolated_root / "sentinel"
        _write_vox_stub(bin_dir, sentinel)
        _write_failing_git(bin_dir)
        env = dict(os.environ)
        env["PATH"] = f"{bin_dir}{os.pathsep}{os.environ['PATH']}"

        rc = _run_hook("notify.sh", str(subdir), "Stop", env)

        assert rc == 0
        assert not sentinel.exists(), "hook fired with no marker in any parent"

    @pytest.mark.parametrize("name", sorted(_GATED_HOOKS))
    @pytest.mark.parametrize(
        "cwd_kind",
        ["directory", "deleted", "symlink-in", "symlink-out", "symlink-out-deleted"],
    )
    def test_relative_cwd_without_git_respects_repo_marker(
        self, name: str, cwd_kind: str, isolated_root: Path
    ) -> None:
        launch_dir = isolated_root / "launch"
        relative_cwd = Path("nested dir") / "child"
        hook_dir = launch_dir / relative_cwd
        marker_root = isolated_root
        if cwd_kind.startswith("symlink-"):
            target = isolated_root / "target repo" / "src"
            target.mkdir(parents=True)
            hook_dir.parent.mkdir(parents=True)
            hook_dir.symlink_to(target, target_is_directory=True)
            marker_root = target.parent if cwd_kind == "symlink-in" else launch_dir
            if cwd_kind == "symlink-out-deleted":
                relative_cwd /= "deleted"
        else:
            hook_dir.mkdir(parents=True)
            if cwd_kind == "deleted":
                hook_dir.rmdir()
        bin_dir = isolated_root / "bin"
        bin_dir.mkdir()
        sentinel = isolated_root / "sentinel"
        _write_vox_stub(bin_dir, sentinel)
        _write_failing_git(bin_dir)
        env = dict(os.environ)
        env["PATH"] = f"{bin_dir}{os.pathsep}{os.environ['PATH']}"

        rc = _run_hook(
            name, str(relative_cwd), _GATED_HOOKS[name], env, launch_dir=launch_dir
        )

        assert rc == 0
        assert not sentinel.exists(), f"{name} fired with no marker in any parent"

        _mark_enabled(marker_root)
        rc = _run_hook(
            name, str(relative_cwd), _GATED_HOOKS[name], env, launch_dir=launch_dir
        )

        assert rc == 0
        assert sentinel.exists(), f"{name} did not reach vox with an enabled ancestor"

    def test_deleted_relative_cwd_respects_git_boundary(
        self, isolated_root: Path
    ) -> None:
        _mark_enabled(isolated_root)
        repo = _make_repo(isolated_root)
        bin_dir = isolated_root / "bin"
        bin_dir.mkdir()
        sentinel = isolated_root / "sentinel"
        _write_vox_stub(bin_dir, sentinel)
        env = dict(os.environ)
        env["PATH"] = f"{bin_dir}{os.pathsep}{os.environ['PATH']}"

        rc = _run_hook("notify.sh", "deleted", "Stop", env, launch_dir=repo)

        assert rc == 0
        assert not sentinel.exists(), "hook crossed an unenabled Git worktree"

    def test_deleted_relative_cwd_retries_git_in_symlink_target(
        self, isolated_root: Path
    ) -> None:
        logical = _make_repo(isolated_root)
        target_parent = isolated_root / "target"
        target_parent.mkdir()
        physical = _make_repo(target_parent)
        child = physical / "nested"
        child.mkdir()
        (logical / "link").symlink_to(child, target_is_directory=True)
        _mark_enabled(logical)
        bin_dir = isolated_root / "bin"
        bin_dir.mkdir()
        sentinel = isolated_root / "sentinel"
        _write_vox_stub(bin_dir, sentinel)
        env = dict(os.environ)
        env["PATH"] = f"{bin_dir}{os.pathsep}{os.environ['PATH']}"

        rc = _run_hook("notify.sh", "link/../deleted", "Stop", env, launch_dir=logical)

        assert rc == 0
        assert not sentinel.exists(), "hook used the symlink's worktree"

        _mark_enabled(physical)
        rc = _run_hook("notify.sh", "link/../deleted", "Stop", env, launch_dir=logical)

        assert rc == 0
        assert sentinel.exists(), "hook did not use the enabled worktree of the target"

    def test_marker_resolved_from_git_root_of_subdir(self, tmp_path: Path) -> None:
        # cwd is a nested subdir; the marker lives at the git worktree root.
        # The gate must resolve the root via `git rev-parse`, not just cwd.
        repo = _make_repo(tmp_path)
        _mark_enabled(repo)
        subdir = repo / "src" / "nested"
        subdir.mkdir(parents=True)
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        sentinel = tmp_path / "sentinel"
        _write_vox_stub(bin_dir, sentinel)
        env = dict(os.environ)
        env["PATH"] = f"{bin_dir}{os.pathsep}{os.environ['PATH']}"

        rc = _run_hook("notify.sh", str(subdir), "Stop", env)

        assert rc == 0
        assert sentinel.exists(), "gate did not resolve the marker from git root"


class TestPanelSpawnRecord:
    def test_waits_for_complete_invocation(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sentinel = tmp_path / "sentinel.log"
        record = "123 --session-pid 456\n"
        snapshots = iter(("", record.rstrip("\n"), record))

        def finish_next_write(_seconds: float) -> None:
            sentinel.write_text(next(snapshots), encoding="utf-8")

        monkeypatch.setattr(time, "sleep", finish_next_write)

        assert _wait_for_panel_spawn(sentinel) == record


@pytest.mark.subprocess
class TestPanelSpawn:
    """``session-start.sh``'s vox-panel spawn block: a different shape from the
    simple gated hooks above (it also deploys commands), so
    each test isolates ``HOME`` to a scratch directory rather than reusing
    ``_run_hook``'s plain env."""

    @pytest.fixture(autouse=True)
    def _reap_panel_stubs(self) -> Iterator[None]:
        """Kill any stub this test spawned once it finishes.

        Every stub in this class shares one pgrep pattern -- `$PPID` is this
        pytest process's own pid, fixed for the whole run -- so a slow
        3-second sleeper left over from one test would otherwise satisfy the
        *next* test's pgrep guard check and make it look like nothing spawned.

        Ordering invariant: `pgrep` runs BEFORE `pkill`. A stub that leaked
        from a no-spawn test but has not yet written its sentinel (the
        beyond-2s tail) is pkill-matchable, and killing it before the write
        would starve the module-teardown sweep of exactly the late leak it
        exists to catch. The process's existence is the failure evidence --
        no sentinel write needed -- so it is captured first, then reaped.
        The assertion applies only to tests that registered a no-spawn
        sentinel; the spawn-expecting tests leave a live 3-second sleeper
        by design.
        """
        registered_before = len(_NO_SPAWN_SENTINELS)
        yield
        pattern = f"vox-panel --session-pid {os.getpid()}"
        survivors = subprocess.run(
            ["pgrep", "-f", pattern],
            check=False,
            capture_output=True,
            text=True,
        )
        subprocess.run(
            ["pkill", "-f", pattern],
            check=False,
            capture_output=True,
        )
        if len(_NO_SPAWN_SENTINELS) > registered_before:
            assert survivors.returncode != 0, (
                "a vox-panel stub survived a no-spawn test (killed before it "
                f"could write its sentinel): pids {survivors.stdout.split()}"
            )

    @staticmethod
    def _write_vox_panel_stub(bin_dir: Path) -> None:
        """A `vox-panel` stub that records every invocation, then stays alive
        long enough for a second hook run's `pgrep` guard to find it."""
        stub = bin_dir / "vox-panel"
        stub.write_text(
            '#!/usr/bin/env bash\necho "$$ $*" >> "$VOX_PANEL_SENTINEL"\nsleep 3\n',
            encoding="utf-8",
        )
        stub.chmod(stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    @staticmethod
    def _base_env(tmp_path: Path) -> dict[str, str]:
        env = dict(os.environ)
        home = tmp_path / "home"
        home.mkdir()
        env["HOME"] = str(home)
        env["TMPDIR"] = str(tmp_path)
        return env

    @staticmethod
    def _panel_log(env: dict[str, str]) -> Path:
        """Where the hook logs this session's panel reasons.

        Under ``$HOME``, deliberately: a shared tmp directory is writable by
        every local user, so a predictable per-session filename there can be
        pre-empted by a symlink planted at that path before the hook runs.
        """
        home = Path(env["HOME"])
        return home / ".punt-labs" / "vox" / "logs" / f"vox-panel-{os.getpid()}.log"

    @staticmethod
    def _hermetic_path(bin_dir: Path) -> str:
        """``bin_dir`` first, then the current PATH stripped of every real
        vox-panel.

        Two layers: the recording stub shadows any real binary, and the real
        one is gone from the tail outright -- no invocation of
        ``session-start.sh`` under this PATH can reach the live Lux hub,
        whatever its cwd resolves to.
        """
        return os.pathsep.join(
            p for p in (str(bin_dir), _path_without_vox_panel()) if p
        )

    @staticmethod
    def _assert_no_panel_spawn(sentinel: Path, *, window: float = 2.0) -> None:
        """Assert no vox-panel invocation lands within *window* seconds.

        The spawn is backgrounded (``nohup ... & disown``), so its record can
        arrive after the hook process has already exited -- an instant check
        would pass before a slow leak lands, and by this file's own evidence
        (see ``_wait_for_panel_spawn``) even 2 seconds is not a safe bound. So the
        sentinel is also registered for the module-teardown sweep, which
        re-checks it long past the observed tail; this window gives fast,
        per-test attribution. The window is evidence, not the guarantee: the
        guarantee is structural (an unenabled scratch cwd and a PATH whose
        tail carries no real vox-panel).
        """
        _NO_SPAWN_SENTINELS.append(sentinel)
        deadline = time.monotonic() + window
        while time.monotonic() < deadline:
            assert not sentinel.exists(), (
                f"a vox-panel spawned: {sentinel.read_text(encoding='utf-8')!r}"
            )
            time.sleep(0.05)
        assert not sentinel.exists(), (
            f"a vox-panel spawned: {sentinel.read_text(encoding='utf-8')!r}"
        )

    @staticmethod
    def _run_session_start(cwd: Path, env: dict[str, str]) -> int:
        payload = json.dumps({"cwd": str(cwd)})
        result = subprocess.run(
            ["bash", str(_HOOKS_DIR / "session-start.sh")],
            input=payload,
            text=True,
            capture_output=True,
            # Never inherit pytest's cwd: the hook falls back to $PWD when the
            # payload cwd is unresolvable, and pytest runs from the enabled
            # vox repo -- the fallback would find the real marker.
            cwd=cwd,
            env=env,
            check=False,
        )
        return result.returncode

    def test_spawns_when_enabled_and_vox_panel_present(self, tmp_path: Path) -> None:
        repo = _make_repo(tmp_path)
        _mark_enabled(repo)
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        sentinel = tmp_path / "sentinel.log"
        self._write_vox_panel_stub(bin_dir)
        env = self._base_env(tmp_path)
        env["PATH"] = self._hermetic_path(bin_dir)
        env["VOX_PANEL_SENTINEL"] = str(sentinel)

        rc = self._run_session_start(repo, env)

        assert rc == 0
        assert f"--session-pid {os.getpid()}" in _wait_for_panel_spawn(sentinel)

    def test_pgrep_guard_skips_a_second_spawn(self, tmp_path: Path) -> None:
        repo = _make_repo(tmp_path)
        _mark_enabled(repo)
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        sentinel = tmp_path / "sentinel.log"
        self._write_vox_panel_stub(bin_dir)
        env = self._base_env(tmp_path)
        env["PATH"] = self._hermetic_path(bin_dir)
        env["VOX_PANEL_SENTINEL"] = str(sentinel)

        assert self._run_session_start(repo, env) == 0
        _wait_for_panel_spawn(sentinel)  # the first spawn is now alive (sleep 3)

        assert self._run_session_start(repo, env) == 0

        lines = sentinel.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 1, "the pgrep guard let a second panel spawn"
        log = self._panel_log(env)
        assert "already served" in log.read_text(encoding="utf-8")

    def test_spawns_when_cwd_is_not_a_git_working_tree(
        self, tmp_path: Path, isolated_root: Path
    ) -> None:
        """A session started outside any git working tree still gets a panel.

        `git rev-parse` exits non-zero there, and under `set -euo pipefail` a
        bare assignment from a failing command substitution aborts the whole
        script -- so every line below it, the spawn included, never ran.
        """
        _mark_enabled(isolated_root)
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        sentinel = tmp_path / "sentinel.log"
        self._write_vox_panel_stub(bin_dir)
        env = self._base_env(tmp_path)
        env["PATH"] = self._hermetic_path(bin_dir)
        env["VOX_PANEL_SENTINEL"] = str(sentinel)

        rc = self._run_session_start(isolated_root, env)

        assert rc == 0, "the hook aborted before reaching the panel spawn"
        assert f"--session-pid {os.getpid()}" in _wait_for_panel_spawn(sentinel)

    def test_malformed_stdin_does_not_abort_the_hook(
        self, tmp_path: Path, isolated_root: Path
    ) -> None:
        """Non-JSON stdin leaves `$_cwd` unresolvable, not the script dead.

        `jq` (and the jq-less `grep` fallback) exit non-zero on input with no
        `cwd`; under `set -e` that killed the hook before its command
        deployment ever ran.

        An unresolvable cwd falls back to $PWD. The subprocess therefore runs
        outside the enabled repo tree, with a recording vox-panel stub first on
        PATH, so it cannot start a real panel against the live hub.
        """
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        sentinel = tmp_path / "sentinel.log"
        self._write_vox_panel_stub(bin_dir)
        env = self._base_env(tmp_path)
        env["PATH"] = self._hermetic_path(bin_dir)
        env["VOX_PANEL_SENTINEL"] = str(sentinel)
        scratch = isolated_root / "unenabled"
        scratch.mkdir()
        result = subprocess.run(
            ["bash", str(_HOOKS_DIR / "session-start.sh")],
            input="not json at all",
            text=True,
            capture_output=True,
            cwd=scratch,
            env=env,
            check=False,
        )

        assert result.returncode == 0, "malformed stdin aborted the hook"
        self._assert_no_panel_spawn(sentinel)

    def test_unreadable_stdin_does_not_abort_the_hook(
        self, tmp_path: Path, isolated_root: Path
    ) -> None:
        """An unreadable stdin costs the session its panel, not just its cwd.

        `cat` exits non-zero when fd 0 cannot be read from, and under `set -e`
        the bare assignment on line 1 killed the hook upstream of every other
        fallback -- the cwd default, command deployment, and the panel spawn
        alike. A write-only fd reproduces it without the deadlock a *closed*
        fd 0 causes: bash would hand the command substitution's own pipe to
        `cat` as its stdin, and `cat` would wait forever on itself.

        Same isolation as the malformed-stdin test above: the empty cwd falls
        back to `$PWD`, so run from an unenabled scratch directory with a
        recording stub first on PATH and assert no panel spawns.
        """
        write_only = os.open(tmp_path / "sink", os.O_WRONLY | os.O_CREAT, 0o600)
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        sentinel = tmp_path / "sentinel.log"
        self._write_vox_panel_stub(bin_dir)
        env = self._base_env(tmp_path)
        env["PATH"] = self._hermetic_path(bin_dir)
        env["VOX_PANEL_SENTINEL"] = str(sentinel)
        scratch = isolated_root / "unenabled"
        scratch.mkdir()
        try:
            result = subprocess.run(
                ["bash", str(_HOOKS_DIR / "session-start.sh")],
                stdin=write_only,
                capture_output=True,
                text=True,
                cwd=scratch,
                env=env,
                check=False,
                timeout=30,
            )
        finally:
            os.close(write_only)

        assert result.returncode == 0, "unreadable stdin aborted the hook"
        self._assert_no_panel_spawn(sentinel)

    def test_relative_cwd_does_not_hang_the_parent_walk(
        self, tmp_path: Path, isolated_root: Path
    ) -> None:
        """A relative `cwd` in the payload must not spin the marker search.

        The walk up to the enablement marker stops at "/", which a relative
        path never reaches -- `dirname .` is `.` -- so an unnormalized
        relative cwd looped forever, hanging session startup outright rather
        than aborting it. The subprocess is run from a directory with no
        marker of its own, since a marker at `.` would end the walk on its
        first step and hide the defect.

        The directory lives under isolated_root, outside the enabled repo tree. With
        TMPDIR inside the repo, tmp_path would let git resolve the real root and
        start a real panel against the live hub.
        """
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        sentinel = tmp_path / "sentinel.log"
        self._write_vox_panel_stub(bin_dir)
        env = self._base_env(tmp_path)
        env["PATH"] = self._hermetic_path(bin_dir)
        env["VOX_PANEL_SENTINEL"] = str(sentinel)
        elsewhere = isolated_root / "elsewhere"
        elsewhere.mkdir()

        result = subprocess.run(
            ["bash", str(_HOOKS_DIR / "session-start.sh")],
            input=json.dumps({"cwd": "relative/nonexistent/path"}),
            text=True,
            capture_output=True,
            cwd=elsewhere,
            env=env,
            check=False,
            timeout=30,
        )

        assert result.returncode == 0
        self._assert_no_panel_spawn(sentinel)

    def test_logs_a_reason_when_vox_panel_is_missing(self, tmp_path: Path) -> None:
        repo = _make_repo(tmp_path)
        _mark_enabled(repo)
        env = self._base_env(tmp_path)
        env["PATH"] = _path_without_vox_panel()

        rc = self._run_session_start(repo, env)

        assert rc == 0
        log = self._panel_log(env)
        assert log.exists(), "no reason was logged for the missing vox-panel"
        assert "not found on PATH" in log.read_text(encoding="utf-8")

    @pytest.mark.parametrize(
        "lookup",
        [
            "path",
            "failure",
            "empty",
            "invalid",
            "multiline",
            "outside",
            "traversal",
            "nested",
            "missing",
        ],
    )
    def test_panel_uses_cli_log_dir_or_legacy_fallback(
        self, tmp_path: Path, lookup: str
    ) -> None:
        repo = _make_repo(tmp_path)
        _mark_enabled(repo)
        env = self._base_env(tmp_path)
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        path = [
            entry
            for entry in _path_without_vox_panel().split(os.pathsep)
            if entry and not (Path(entry) / "vox").exists()
        ]
        env["PATH"] = os.pathsep.join([str(bin_dir), *path])
        log_dir = Path(env["HOME"]) / "private state" / "vox-state" / "logs"
        env["CLI_LOG_DIR"] = str(log_dir)
        outside = tmp_path / "outside" / "vox-state" / "logs"
        env["OUTSIDE_LOG_DIR"] = str(outside)
        nested = log_dir.parent / "extra" / "logs"
        env["NESTED_LOG_DIR"] = str(nested)
        if lookup != "missing":
            vox = bin_dir / "vox"
            action = {
                "path": 'printf "%s\\n" "$CLI_LOG_DIR"',
                "failure": 'printf "%s\\n" "$CLI_LOG_DIR"; exit 1',
                "empty": ":",
                "invalid": 'printf "relative/path/logs\\n"',
                "multiline": 'printf "%s\\n%s\\n" "$CLI_LOG_DIR" "$CLI_LOG_DIR"',
                "outside": 'printf "%s\\n" "$OUTSIDE_LOG_DIR"',
                "traversal": 'printf "%s\\n" "$HOME/../outside/vox-state/logs"',
                "nested": 'printf "%s\\n" "$NESTED_LOG_DIR"',
            }[lookup]
            vox.write_text(
                f'#!/bin/bash\n[[ "$*" == "hook _log-dir" ]] || exit 1\n{action}\n',
                encoding="utf-8",
            )
            vox.chmod(0o700)

        assert self._run_session_start(repo, env) == 0
        legacy = self._panel_log(env)
        if lookup == "path":
            assert "not found on PATH" in (log_dir / legacy.name).read_text(
                encoding="utf-8"
            )
            assert not legacy.exists()
            for directory in (log_dir, log_dir.parent, log_dir.parent.parent):
                assert stat.S_IMODE(directory.stat().st_mode) == 0o700
        else:
            assert "not found on PATH" in legacy.read_text(encoding="utf-8")
            assert not log_dir.exists()
            assert not outside.exists()
            assert not nested.exists()
            assert not (repo / "relative").exists()

    def test_panel_log_avoids_the_world_writable_temp_dir(self, tmp_path: Path) -> None:
        """The panel log belongs under `$HOME`, in a 0700 directory.

        A shared temp directory is writable by every local user, so the
        predictable `vox-panel-<pid>.log` name could be pre-empted there by a
        symlink planted before the session started, and the hook's appends
        would land wherever that symlink pointed. Owning the directory removes
        the race outright -- no other user can create a path inside `$HOME`.
        """
        repo = _make_repo(tmp_path)
        _mark_enabled(repo)
        env = self._base_env(tmp_path)
        env["PATH"] = _path_without_vox_panel()

        assert self._run_session_start(repo, env) == 0

        log = self._panel_log(env)
        assert log.exists()
        assert stat.S_IMODE(log.parent.stat().st_mode) == 0o700
        shared = Path(env["TMPDIR"])
        assert not list(shared.glob("vox-panel-*.log")), (
            "the hook still writes a predictable name into the shared temp dir"
        )

    def test_unwritable_home_costs_the_log_not_the_hook(self, tmp_path: Path) -> None:
        """A log path that cannot be created must not abort session startup.

        An unwritable HOME must not abort command deployment or panel spawning: a
        failed synchronous log redirect otherwise exits under set -e.
        """
        repo = _make_repo(tmp_path)
        _mark_enabled(repo)
        env = self._base_env(tmp_path)
        env["PATH"] = _path_without_vox_panel()
        home = Path(env["HOME"])
        home.chmod(0o500)
        try:
            rc = self._run_session_start(repo, env)
        finally:
            home.chmod(0o700)

        assert rc == 0, "an unwritable log path aborted the hook"


@pytest.mark.subprocess
class TestManifestRefusal:
    """``session-start.sh`` refuses to guess dev-vs-prod it cannot read.

    The manifest decides command cleanup and deployment. If it cannot be read,
    the hook must report the problem, touch nothing, and exit zero rather than
    assume a production plugin.
    """

    @staticmethod
    def _plugin_copy(tmp_path: Path) -> Path:
        """Copy the real surface so its manifest can be broken safely."""
        dest = tmp_path / "plugin"
        shutil.copytree(_HOOKS_DIR.parent, dest)
        return dest

    @staticmethod
    def _run(plugin: Path, cwd: Path, home: Path) -> subprocess.CompletedProcess[str]:
        home.mkdir(parents=True, exist_ok=True)
        env = dict(os.environ)
        env["HOME"] = str(home)
        env["TMPDIR"] = str(home)
        # The payload cwd is an unenabled scratch repo, so the panel block is
        # already gated off -- but no session-start.sh run in this file may be
        # *capable* of reaching the live Lux hub: strip the real vox-panel
        # from PATH and never inherit pytest's cwd (the enabled repo).
        env["PATH"] = _path_without_vox_panel()
        return subprocess.run(
            ["bash", str(plugin / "hooks" / "session-start.sh")],
            input=json.dumps({"cwd": str(cwd)}),
            text=True,
            capture_output=True,
            cwd=home,
            env=env,
            check=False,
        )

    def test_intact_manifest_still_provisions(self, tmp_path: Path) -> None:
        # The control. Without it, "nothing was written" below could just mean
        # the fixture is inert rather than that the guard fired.
        plugin = self._plugin_copy(tmp_path)
        result = self._run(plugin, _make_repo(tmp_path), tmp_path / "home")
        assert result.returncode == 0, result.stderr
        assert "cannot tell dev from prod" not in result.stdout

    def test_absent_manifest_provisions_nothing(self, tmp_path: Path) -> None:
        plugin = self._plugin_copy(tmp_path)
        (plugin / ".claude-plugin" / "plugin.json").unlink()
        home = tmp_path / "home"
        result = self._run(plugin, _make_repo(tmp_path), home)

        assert result.returncode == 0, result.stderr
        assert not (home / ".claude").exists(), "provisioned against an unread manifest"
        assert "No plugin manifest" in result.stdout
        assert "skipped command deployment" in result.stdout

    def test_unreadable_manifest_provisions_nothing(self, tmp_path: Path) -> None:
        if os.geteuid() == 0:
            pytest.skip("root bypasses the mode bits, so chmod 000 stays readable")

        plugin = self._plugin_copy(tmp_path)
        manifest = plugin / ".claude-plugin" / "plugin.json"
        manifest.chmod(0o000)
        home = tmp_path / "home"
        try:
            result = self._run(plugin, _make_repo(tmp_path), home)
        finally:
            manifest.chmod(0o644)

        assert result.returncode == 0, result.stderr
        assert not (home / ".claude").exists(), "provisioned against an unread manifest"
        # grep exits 2 on a read error and 1 on a genuine no-match; collapsing
        # the two is what made an unreadable manifest look like a prod one.
        assert "grep exit 2" in result.stdout
        assert "Permission denied" in result.stderr
