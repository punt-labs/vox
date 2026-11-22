"""Only worker launch is moved outside the daemon service's cgroup."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from punt_vox.voxd.workers.tmux import TmuxWorkerProvider


def test_service_worker_launch_uses_separate_user_scope(tmp_path: Path) -> None:
    with (
        patch.object(Path, "read_text", return_value="0::/system.slice/voxd.service\n"),
        patch("sys.platform", "linux"),
        patch(
            "subprocess.run", return_value=subprocess.CompletedProcess([], 0, "", "")
        ) as run,
    ):
        TmuxWorkerProvider._run("new-session", "-d", "-s", "worker")
    argv = run.call_args.args[0]
    assert argv[:5] == ["systemd-run", "--user", "--scope", "--quiet", "--"]
    assert argv[5:7] == ["tmux", "-L"]
    assert "XDG_RUNTIME_DIR" in run.call_args.kwargs["env"]


def test_non_service_launch_and_status_do_not_need_user_manager() -> None:
    for command, cgroup in (
        ("new-session", "0::/user.slice/session.scope\n"),
        ("has-session", "0::/system.slice/voxd.service\n"),
    ):
        with (
            patch.object(Path, "read_text", return_value=cgroup),
            patch("sys.platform", "linux"),
            patch(
                "subprocess.run",
                return_value=subprocess.CompletedProcess([], 0, "", ""),
            ) as run,
        ):
            TmuxWorkerProvider._run(command)
        assert run.call_args.args[0][0] == "tmux"


@pytest.mark.parametrize("supports_expansion", [False, True])
def test_scope_preserves_literal_arguments_on_old_and_new_systemd(
    supports_expansion: bool,
) -> None:
    help_text = (
        "--expand-environment=BOOL" if supports_expansion else "legacy systemd-run"
    )
    with (
        patch.object(Path, "read_text", return_value="0::/system.slice/voxd.service\n"),
        patch("sys.platform", "linux"),
        patch(
            "subprocess.run",
            side_effect=[
                subprocess.CompletedProcess([], 0, help_text, ""),
                subprocess.CompletedProcess([], 0, "", ""),
            ],
        ) as run,
    ):
        TmuxWorkerProvider._run("new-session", "--", "echo", "$$")
    argv = run.call_args.args[0]
    assert ("--expand-environment=no" in argv) is supports_expansion
    assert argv[-1] == "$$"
