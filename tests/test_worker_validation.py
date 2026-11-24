"""Worker boundary failures must be explicit, bounded and non-destructive."""

from __future__ import annotations

import json
import logging
import os
import subprocess
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
from test_workers import TrivialProfile

from punt_vox.config import ConfigStore
from punt_vox.voxd.workers.command import WorkerCommand
from punt_vox.voxd.workers.policy import WorkerRepoPolicy
from punt_vox.voxd.workers.records import WorkerRecords
from punt_vox.voxd.workers.tmux import TmuxWorkerProvider
from punt_vox.voxd.workers.tmux_probe import TmuxProbe
from punt_vox.voxd.workers.trust import ClaudeTrust


@pytest.mark.parametrize("raw", ["42", "['/repo', None]"])
def test_invalid_allowlists_fail_before_git(tmp_path: Path, raw: str) -> None:
    config = Mock(spec=ConfigStore)
    config.read_field.return_value = raw
    with (
        patch("punt_vox.voxd.workers.policy.subprocess.run") as git,
        pytest.raises(ValueError, match="list of repository paths"),
    ):
        WorkerRepoPolicy(config).resolve(str(tmp_path))
    git.assert_not_called()


@pytest.mark.parametrize("broken", ["root", "projects", "project"])
def test_malformed_trust_maps_are_retained(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, broken: str
) -> None:
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    worktree = tmp_path / "worktree"
    data = (
        []
        if broken == "root"
        else {"projects": [] if broken == "projects" else {str(worktree): []}}
    )
    path = tmp_path / ".claude.json"
    contents = json.dumps(data)
    path.write_text(contents)
    with pytest.raises(ValueError, match="must be"):
        ClaudeTrust().update(worktree, accepted=True)
    assert path.read_text() == contents
    assert not (tmp_path / ".claude.json.lock").exists()


def test_trust_cleanup_retains_other_project_settings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    worktree = tmp_path / "worktree"
    path = tmp_path / ".claude.json"
    path.write_text(
        json.dumps(
            {
                "projects": {
                    str(worktree): {"theme": "dark", "hasTrustDialogAccepted": True}
                }
            }
        )
    )
    ClaudeTrust().update(worktree, accepted=False)
    assert json.loads(path.read_text()) == {
        "projects": {str(worktree): {"theme": "dark"}}
    }


def test_strict_record_reader_refuses_corruption_without_removing_it(
    tmp_path: Path,
) -> None:
    records = WorkerRecords(tmp_path / "workers")
    path = tmp_path / "workers" / "broken.json"
    path.write_text("{")
    with pytest.raises(ValueError):
        records.load()
    assert path.read_text() == "{"


def test_unknown_cgroup_fails_launch_closed() -> None:
    with (
        patch("punt_vox.voxd.workers.command.sys.platform", "linux"),
        patch.object(Path, "read_text", side_effect=OSError("proc unavailable")),
        pytest.raises(RuntimeError, match="Cannot identify"),
    ):
        WorkerCommand("new-session").run(check=True)


def test_stop_tolerates_a_pane_process_disappearing() -> None:
    with (
        patch.object(
            TmuxWorkerProvider,
            "_run",
            side_effect=[
                subprocess.CompletedProcess([], 0),
                subprocess.CompletedProcess([], 0, stdout="123"),
                subprocess.CompletedProcess([], 0),
            ],
        ) as run,
        patch("punt_vox.voxd.workers.tmux.os.getpgid", side_effect=ProcessLookupError),
        patch.object(TmuxProbe, "wait_for_exit") as finish,
    ):
        TmuxWorkerProvider().stop("worker")
    assert run.call_args.args == ("kill-session", "-t", "=worker")
    finish.assert_not_called()


def test_stop_refuses_its_own_process_group() -> None:
    with (
        patch("punt_vox.voxd.workers.tmux_probe.os.getpgrp", return_value=42),
        patch("punt_vox.voxd.workers.tmux_probe.os.killpg") as kill,
        pytest.raises(RuntimeError, match="own process group"),
    ):
        TmuxProbe.wait_for_exit(42)
    kill.assert_not_called()


def test_stop_deadline_is_bounded() -> None:
    with (
        patch("punt_vox.voxd.workers.tmux_probe.os.getpgrp", return_value=1),
        patch("punt_vox.voxd.workers.tmux_probe.os.killpg"),
        patch("punt_vox.voxd.workers.tmux_probe.time.monotonic", side_effect=[0, 4]),
        pytest.raises(RuntimeError, match="did not exit"),
    ):
        TmuxProbe.wait_for_exit(42)


def test_transport_forwards_the_selected_profiles_progress() -> None:
    with patch.object(
        TrivialProfile, "progress", return_value={"detail": "ready"}
    ) as progress:
        assert TmuxWorkerProvider().progress("identity", TrivialProfile()) == {
            "detail": "ready"
        }
    progress.assert_called_once_with("identity")


def _git_toplevel(toplevel: Path) -> Mock:
    return Mock(stdout=f"{toplevel}\n")


def test_allow_stores_the_resolved_top_level(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(repo, target_is_directory=True)
    monkeypatch.setenv("HOME", str(tmp_path))
    policy = WorkerRepoPolicy(ConfigStore(tmp_path / "config"))
    with patch(
        "punt_vox.voxd.workers.policy.subprocess.run",
        return_value=_git_toplevel(repo.resolve()),
    ):
        assert policy.allow("~/alias") == (str(repo.resolve()),)
        assert policy.allow(str(repo)) == (str(repo.resolve()),)
    assert policy.repos() == (str(repo.resolve()),)


def test_allow_refuses_a_subdirectory_and_a_missing_path(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    child = repo / "child"
    child.mkdir(parents=True)
    policy = WorkerRepoPolicy(ConfigStore(tmp_path / "config"))
    with (
        patch(
            "punt_vox.voxd.workers.policy.subprocess.run",
            return_value=_git_toplevel(repo.resolve()),
        ),
        pytest.raises(ValueError, match="not a git repository top level"),
    ):
        policy.allow(str(child))
    with pytest.raises(ValueError, match="not a directory"):
        policy.allow(str(tmp_path / "missing"))
    assert policy.repos() == ()


def test_allow_refuses_a_directory_outside_git(tmp_path: Path) -> None:
    plain = tmp_path / "plain"
    plain.mkdir()
    policy = WorkerRepoPolicy(ConfigStore(tmp_path / "config"))
    with (
        patch(
            "punt_vox.voxd.workers.policy.subprocess.run",
            side_effect=subprocess.CalledProcessError(128, ["git"]),
        ),
        pytest.raises(ValueError, match="not a git repository top level"),
    ):
        policy.allow(str(plain))
    assert policy.repos() == ()


def test_disallow_removes_by_resolved_path_even_when_gone(tmp_path: Path) -> None:
    config = ConfigStore(tmp_path / "config")
    gone = tmp_path / "gone"
    kept = tmp_path / "kept"
    config.write_field("worker_repos", repr([str(gone), str(kept)]))
    policy = WorkerRepoPolicy(config)
    assert policy.disallow(str(tmp_path / "x" / ".." / "gone")) == (str(kept),)
    assert policy.repos() == (str(kept),)
    with pytest.raises(ValueError, match="not allowlisted"):
        policy.disallow(str(gone))


@pytest.mark.parametrize(
    ("entry", "reason"),
    [
        ("   ", "blank"),
        ("", "blank"),
        ("relative/repo", "not absolute"),
        ("/", "the filesystem root"),
        ("/../", "the filesystem root"),
    ],
)
def test_bad_allowlist_entries_are_rejected_by_name(
    tmp_path: Path, entry: str, reason: str
) -> None:
    """A blank, relative or root entry never becomes a repository."""
    config = ConfigStore(tmp_path / "config")
    config.write_field("worker_repos", repr([entry]))
    policy = WorkerRepoPolicy(config)
    with pytest.raises(ValueError, match=f"worker_repos entry {reason}: {entry!r}"):
        policy.repos()
    with pytest.raises(ValueError, match=f"worker_repos entry {reason}: {entry!r}"):
        policy.resolve(str(tmp_path))


@pytest.mark.parametrize(
    ("entry", "reason"),
    [("   ", "blank"), ("relative/repo", "not absolute"), ("/", "the filesystem root")],
)
def test_allow_and_disallow_refuse_bad_entries_before_git(
    tmp_path: Path, entry: str, reason: str
) -> None:
    policy = WorkerRepoPolicy(ConfigStore(tmp_path / "config"))
    with (
        patch("punt_vox.voxd.workers.policy.subprocess.run") as git,
        pytest.raises(ValueError, match=f"worker_repos entry {reason}: {entry!r}"),
    ):
        policy.allow(entry)
    with pytest.raises(ValueError, match=f"worker_repos entry {reason}: {entry!r}"):
        policy.disallow(entry)
    git.assert_not_called()
    assert policy.repos() == ()


def test_allow_round_trips_a_path_with_an_apostrophe(tmp_path: Path) -> None:
    """repr() would switch to double quotes, which the frontmatter refuses."""
    repo = tmp_path / "Jim's repo"
    repo.mkdir()
    config = ConfigStore(tmp_path / "config")
    policy = WorkerRepoPolicy(config)
    with patch(
        "punt_vox.voxd.workers.policy.subprocess.run",
        return_value=_git_toplevel(repo.resolve()),
    ):
        assert policy.allow(str(repo)) == (str(repo.resolve()),)
    stored = config.read_field("worker_repos")
    assert stored is not None
    assert '"' not in stored
    assert WorkerRepoPolicy(config).repos() == (str(repo.resolve()),)
    assert policy.disallow(str(repo)) == ()


def test_allow_refuses_a_path_with_a_double_quote_by_name(tmp_path: Path) -> None:
    repo = tmp_path / 'say "hi"'
    repo.mkdir()
    policy = WorkerRepoPolicy(ConfigStore(tmp_path / "config"))
    with (
        patch("punt_vox.voxd.workers.policy.subprocess.run") as git,
        pytest.raises(
            ValueError, match=f"worker_repos entry has a double quote: {str(repo)!r}"
        ),
    ):
        policy.allow(str(repo))
    git.assert_not_called()
    assert policy.repos() == ()


_FENCED = '---\nprovider: "elevenlabs"\nvoice: "fin"\nlog_level: "debug"\n---\n'


def _global_config(tmp_path: Path) -> tuple[ConfigStore, Path]:
    config = ConfigStore(tmp_path / "config")
    (tmp_path / "config").mkdir()
    return config, tmp_path / "config" / "vox.md"


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads a mode-000 file")
def test_an_unreadable_allowlist_file_raises_instead_of_reading_as_empty(
    tmp_path: Path,
) -> None:
    """H2: a policy file that will not open is a fault, never an empty policy."""
    config, path = _global_config(tmp_path)
    path.write_text(_FENCED + "", encoding="utf-8")
    config.write_field("worker_repos", repr([str(tmp_path)]))
    path.chmod(0)
    policy = WorkerRepoPolicy(config)
    try:
        with pytest.raises(PermissionError):
            policy.repos()
        with pytest.raises(PermissionError):
            policy.disallow(str(tmp_path))
        with (
            patch(
                "punt_vox.voxd.workers.policy.subprocess.run",
                return_value=_git_toplevel(tmp_path.resolve()),
            ),
            pytest.raises(PermissionError),
        ):
            policy.allow(str(tmp_path))
    finally:
        path.chmod(0o600)


def test_a_non_utf8_allowlist_file_raises_instead_of_reading_as_empty(
    tmp_path: Path,
) -> None:
    config, path = _global_config(tmp_path)
    path.write_bytes(b"---\nworker_repos: \"['/repo']\"\n\xff\n---\n")
    policy = WorkerRepoPolicy(config)
    with pytest.raises(UnicodeDecodeError):
        policy.repos()
    with pytest.raises(UnicodeDecodeError):
        policy.disallow("/repo")


def test_allow_refuses_a_file_without_a_closing_fence_and_leaves_it_intact(
    tmp_path: Path,
) -> None:
    """H3: never rebuild vox.md from worker_repos alone, losing every other field."""
    config, path = _global_config(tmp_path)
    broken = '---\nprovider: "elevenlabs"\nvoice: "fin"\nlog_level: "debug"\n'
    path.write_bytes(broken.encode())
    repo = tmp_path / "repo"
    repo.mkdir()
    policy = WorkerRepoPolicy(config)
    with (
        patch(
            "punt_vox.voxd.workers.policy.subprocess.run",
            return_value=_git_toplevel(repo.resolve()),
        ),
        pytest.raises(ValueError, match="no closing ---"),
    ):
        policy.allow(str(repo))
    assert path.read_bytes() == broken.encode()


def test_allow_edits_in_place_and_keeps_the_other_fields(tmp_path: Path) -> None:
    config, path = _global_config(tmp_path)
    path.write_text(_FENCED, encoding="utf-8")
    repo = tmp_path / "repo"
    repo.mkdir()
    policy = WorkerRepoPolicy(config)
    with patch(
        "punt_vox.voxd.workers.policy.subprocess.run",
        return_value=_git_toplevel(repo.resolve()),
    ):
        policy.allow(str(repo))
    assert config.read_field("voice") == "fin"
    assert config.read_field("provider") == "elevenlabs"
    assert config.read_field("worker_repos") == f"['{repo.resolve()}']"


@pytest.mark.parametrize(
    ("failure", "message"),
    [
        (FileNotFoundError(2, "No such file", "git"), "git is not installed"),
        (subprocess.TimeoutExpired(["git"], 5), "git timed out"),
        (
            subprocess.CalledProcessError(128, ["git"], stderr="fatal: not a git repo"),
            "not a git repository top level",
        ),
    ],
)
def test_git_failures_are_told_apart_and_stderr_is_logged(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    failure: Exception,
    message: str,
) -> None:
    """M6: a missing git binary, a hang and a real non-repo are different."""
    repo = tmp_path / "repo"
    repo.mkdir()
    policy = WorkerRepoPolicy(ConfigStore(tmp_path / "config"))
    with (
        patch("punt_vox.voxd.workers.policy.subprocess.run", side_effect=failure),
        caplog.at_level(logging.WARNING, logger="punt_vox.voxd.workers.policy"),
        pytest.raises(ValueError, match=message),
    ):
        policy.allow(str(repo))
    if isinstance(failure, subprocess.CalledProcessError):
        assert any("fatal: not a git repo" in r.getMessage() for r in caplog.records)
