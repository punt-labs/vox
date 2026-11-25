"""Doctor checks that ask the running daemon about its microphone and allowlist."""

from __future__ import annotations

import sys
from unittest.mock import patch

import pytest

from punt_vox.client_errors import (
    VoxdConnectionError,
    VoxdProtocolError,
    VoxdRefusedError,
)
from punt_vox.daemon_control import DaemonControl
from punt_vox.doctor_call_daemon import MicCaptureCheck, WorkerReposCheck
from punt_vox.types_mic import MicProbeReport


def _reply(peak: int, samples: int = 16_000, overflows: int = 0) -> dict[str, object]:
    return MicProbeReport(
        device="MacBook Pro Microphone",
        sample_rate_hz=16_000,
        peak=peak,
        samples=samples,
        overflows=overflows,
    ).to_wire()


def test_mic_check_passes_when_the_daemon_hears_signal() -> None:
    with patch.object(DaemonControl, "request", return_value=_reply(812)) as rpc:
        result = MicCaptureCheck().run()

    rpc.assert_awaited_once()
    assert rpc.call_args.args == ("probe",)
    assert result.status_kind == "pass"
    assert not result.required
    assert result.message.startswith("Microphone (voxd):")
    assert "MacBook Pro Microphone" in result.message
    assert "16000 Hz" in result.message
    assert "peak 812" in result.message
    assert "overflow" not in result.message


def test_mic_check_passes_but_shows_dropped_audio_when_callbacks_overflowed() -> None:
    """Overflowed callbacks dropped audio; the row says so and still passes."""
    with patch.object(DaemonControl, "request", return_value=_reply(812, overflows=3)):
        result = MicCaptureCheck().run()

    assert result.status_kind == "pass"
    assert "3 overflows, audio dropped" in result.message


def test_mic_check_warns_and_names_the_grant_when_every_sample_is_zero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """On macOS, silence from an opened device is the missing privacy grant."""
    monkeypatch.setattr(sys, "platform", "darwin")
    with patch.object(DaemonControl, "request", return_value=_reply(0)):
        result = MicCaptureCheck().run()

    assert result.status_kind == "warn"
    assert not result.required
    assert "MacBook Pro Microphone" in result.message
    assert "silence" in result.message
    assert "Privacy & Security" in result.message
    assert "Microphone" in result.message
    assert "terminal" in result.message


def test_mic_check_names_the_audio_server_for_silence_off_macos(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Off macOS there is no privacy grant; the advice names the audio server."""
    monkeypatch.setattr(sys, "platform", "linux")
    with patch.object(DaemonControl, "request", return_value=_reply(0)):
        result = MicCaptureCheck().run()

    assert result.status_kind == "warn"
    assert "the daemon captured silence" in result.message
    assert "audio server's permissions for voxd's user" in result.message
    assert "Privacy & Security" not in result.message


def test_mic_check_warns_without_grant_advice_when_no_audio_arrived() -> None:
    """Zero samples is the device delivering nothing; the grant is not the fix."""
    with patch.object(DaemonControl, "request", return_value=_reply(0, samples=0)):
        result = MicCaptureCheck().run()

    assert result.status_kind == "warn"
    assert not result.required
    assert "MacBook Pro Microphone" in result.message
    assert "delivered no audio in one second" in result.message
    assert "silence" not in result.message
    assert "Privacy & Security" not in result.message


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (VoxdRefusedError("refused"), "daemon not running"),
        (VoxdConnectionError("timed out"), "timed out"),
        (
            VoxdProtocolError("Error opening RawInputStream: no default input"),
            "no default input",
        ),
    ],
)
def test_mic_check_warns_when_the_daemon_cannot_answer(
    error: Exception, expected: str
) -> None:
    with patch.object(DaemonControl, "request", side_effect=error):
        result = MicCaptureCheck().run()

    assert result.status_kind == "warn"
    assert not result.required
    assert result.message.startswith("Microphone (voxd):")
    assert expected in result.message


def test_mic_check_never_captures_in_the_doctor_process(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The check's only I/O is the daemon request: no capture code is even imported."""
    monkeypatch.delitem(sys.modules, "sounddevice", raising=False)
    monkeypatch.delitem(sys.modules, "punt_vox.voxd.mic_probe", raising=False)
    with patch.object(DaemonControl, "request", return_value=_reply(5)) as rpc:
        MicCaptureCheck().run()

    rpc.assert_awaited_once()
    assert "sounddevice" not in sys.modules
    assert "punt_vox.voxd.mic_probe" not in sys.modules


def test_mic_check_names_the_restart_when_the_daemon_predates_the_probe() -> None:
    """A daemon serving older code answers unknown message type; restart it."""
    error = VoxdProtocolError("unknown message type: mic_probe")
    with patch.object(DaemonControl, "request", side_effect=error):
        result = MicCaptureCheck().run()

    assert result.status_kind == "warn"
    assert result.message.startswith("Microphone (voxd):")
    assert "vox daemon restart" in result.message


def test_repos_check_names_the_restart_when_the_daemon_predates_the_rpc() -> None:
    error = VoxdProtocolError("unknown message type: worker_repos")
    with patch.object(DaemonControl, "request", side_effect=error):
        result = WorkerReposCheck().run()

    assert result.status_kind == "warn"
    assert "vox daemon restart" in result.message


def test_repos_check_reports_the_count_and_last_path_segments() -> None:
    repos = ["/Users/me/Coding/punt-labs/vox-demo", "/srv/lux"]
    with patch.object(DaemonControl, "request", return_value=repos) as rpc:
        result = WorkerReposCheck().run()

    assert rpc.call_args.args == ("repos",)
    assert result.status_kind == "pass"
    assert not result.required
    assert result.message == "Worker repositories: 2 allowlisted (vox-demo, lux)"


def test_repos_check_warns_when_nothing_is_allowlisted() -> None:
    with patch.object(DaemonControl, "request", return_value=[]):
        result = WorkerReposCheck().run()

    assert result.status_kind == "warn"
    assert not result.required
    assert result.message == (
        "Worker repositories: worker_repos is empty: no repository is allowlisted;"
        " run vox worker allow <path>"
    )


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (VoxdRefusedError("refused"), "daemon not running"),
        (VoxdConnectionError("timed out"), "timed out"),
        (VoxdProtocolError("Worker startup recovery failed"), "recovery failed"),
    ],
)
def test_repos_check_warns_when_the_daemon_cannot_answer(
    error: Exception, expected: str
) -> None:
    with patch.object(DaemonControl, "request", side_effect=error):
        result = WorkerReposCheck().run()

    assert result.status_kind == "warn"
    assert result.message.startswith("Worker repositories:")
    assert expected in result.message


def test_mic_check_warns_on_a_reply_that_is_not_a_report() -> None:
    """A daemon answer without the report's fields is a warning, not a crash."""
    with patch.object(DaemonControl, "request", return_value={"device": "mic"}):
        result = MicCaptureCheck().run()

    assert result.status_kind == "warn"
    assert result.message == (
        "Microphone (voxd): mic probe reply is missing a report field"
    )


@pytest.mark.parametrize("reply", ["not a list", ["/srv/vox", 7]])
def test_repos_check_warns_on_a_reply_that_is_not_a_list_of_paths(
    reply: object,
) -> None:
    with patch.object(DaemonControl, "request", return_value=reply):
        result = WorkerReposCheck().run()

    assert result.status_kind == "warn"
    assert result.message == (
        "Worker repositories: worker_repos reply is not a list of paths"
    )
