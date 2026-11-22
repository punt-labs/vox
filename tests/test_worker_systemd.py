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
    run.assert_called_once()
    assert argv[:6] == [
        "systemd-run",
        "--user",
        "--scope",
        "--quiet",
        "--expand-environment=no",
        "--",
    ]
    assert argv[6:8] == ["tmux", "-L"]
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


def test_scope_uses_one_literal_invocation_without_capability_probes() -> None:
    with (
        patch.object(Path, "read_text", return_value="0::/system.slice/voxd.service\n"),
        patch("sys.platform", "linux"),
        patch(
            "subprocess.run",
            return_value=subprocess.CompletedProcess([], 0, "", ""),
        ) as run,
    ):
        TmuxWorkerProvider._run("new-session", "--", "echo", "$$")
    argv = run.call_args.args[0]
    run.assert_called_once()
    assert "--expand-environment=no" in argv[: argv.index("--")]
    assert argv[-1] == "$$"


@pytest.mark.parametrize(
    "failure",
    [
        subprocess.CalledProcessError(
            1, ["systemd-run"], stderr="Unrecognized option must-not-leak"
        ),
        subprocess.TimeoutExpired(["systemd-run"], 15),
    ],
)
def test_unsupported_scope_fails_clearly_without_fallback(
    failure: subprocess.SubprocessError,
) -> None:
    with (
        patch.object(Path, "read_text", return_value="0::/system.slice/voxd.service\n"),
        patch("sys.platform", "linux"),
        patch("subprocess.run", side_effect=failure) as run,
        pytest.raises(RuntimeError, match="systemd 254\\+") as caught,
    ):
        TmuxWorkerProvider._run("new-session", "-d", "-s", "worker")
    run.assert_called_once()
    assert "must-not-leak" not in str(caught.value)
