from subprocess import CompletedProcess

import pytest

from punt_vox.voxd.workers.tmux_probe import TmuxProbe


@pytest.mark.parametrize(
    ("stdout", "stderr", "alive"),
    [
        ("0\n", "", True),
        ("1\n", "", False),
        ("", "can't find session: vox-worker", False),
        ("", "no server running on /local/socket", False),
        ("", "error connecting to /local/socket (No such file or directory)", False),
        ("", "server exited unexpectedly\n", False),
        ("", "error connecting to /local/socket (Permission denied)", None),
        ("", "", None),
    ],
)
def test_tmux_probe_distinguishes_absence_from_unknown(
    stdout: str, stderr: str, alive: bool | None
) -> None:
    result = CompletedProcess([], int(bool(stderr)), stdout, stderr)
    if alive is None:
        with pytest.raises(RuntimeError, match="liveness probe"):
            TmuxProbe.pane_alive(result)
    else:
        assert TmuxProbe.pane_alive(result) is alive


def test_a_server_that_exits_as_the_client_connects_means_the_session_is_gone() -> None:
    """tmux shuts down after its last session ends; has-session can lose that race."""
    result = CompletedProcess([], 1, "", "server exited unexpectedly\n")
    assert TmuxProbe.missing(result) is True


def test_other_unrecognised_stderr_still_raises() -> None:
    result = CompletedProcess([], 1, "", "server crashed: out of memory")
    with pytest.raises(RuntimeError, match="liveness probe"):
        TmuxProbe.missing(result)
