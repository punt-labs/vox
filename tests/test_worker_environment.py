"""Private environment transfer and strict bootstrap decoding."""

from __future__ import annotations

import io
import json
import signal
import socket
import stat
from unittest.mock import patch
from uuid import uuid4

import pytest

from punt_vox.voxd.workers.bootstrap import WorkerBootstrap
from punt_vox.voxd.workers.environment import WorkerEnvironment
from punt_vox.voxd.workers.reaper import WorkerReaper


def test_environment_socket_is_private_single_use_and_removed() -> None:
    broker = WorkerEnvironment()
    address = broker.address
    try:
        assert stat.S_IMODE(address.parent.stat().st_mode) == 0o700
        with socket.socket(socket.AF_UNIX) as client:
            client.connect(str(address))
            broker.send({"KEY": "fake-value"})
            assert json.loads(client.recv(1024)) == {"KEY": "fake-value"}
    finally:
        broker.close()
    assert not address.parent.exists()


def test_startup_reaps_only_its_private_broker() -> None:
    namespace = uuid4().hex[:16]
    with patch("os.getpid", return_value=894321):
        abandoned = WorkerEnvironment(namespace)
        shared = WorkerEnvironment(namespace)
    with patch("os.getpid", return_value=0):
        invalid_owner = WorkerEnvironment(namespace)
    live = WorkerEnvironment(namespace)
    other = WorkerEnvironment()
    try:
        shared.address.parent.chmod(0o755)

        def signal_owner(pid: int, signal_number: int) -> None:
            assert signal_number == 0
            assert pid > 0
            if pid == 894321:
                raise ProcessLookupError

        with patch("os.kill", side_effect=signal_owner):
            WorkerReaper.reap_brokers(namespace)
        assert not abandoned.address.parent.exists()
        assert shared.address.is_socket()
        assert invalid_owner.address.is_socket()
        assert live.address.is_socket()
        assert other.address.is_socket()
    finally:
        abandoned.close()
        shared.close()
        invalid_owner.close()
        live.close()
        other.close()


def test_broker_reaper_does_not_delete_a_regular_file_in_place_of_socket() -> None:
    namespace = uuid4().hex[:16]
    with patch("os.getpid", return_value=894321):
        broker = WorkerEnvironment(namespace)
    address = broker.address
    try:
        address.unlink()
        address.write_text("not a socket")

        with patch("os.kill", side_effect=ProcessLookupError):
            WorkerReaper.reap_brokers(namespace)

        assert address.read_text() == "not a socket"
    finally:
        broker.close()


@pytest.mark.parametrize("payload", ["[]", '{"KEY": 1}'])
def test_bootstrap_rejects_non_string_environments(payload: str) -> None:
    with patch("socket.socket") as factory:
        connection = factory.return_value.__enter__.return_value
        connection.makefile.return_value.__enter__.return_value = io.StringIO(payload)
        with pytest.raises(ValueError, match="Worker environment must"):
            WorkerBootstrap.run("private-socket", ["harness"])


@pytest.mark.parametrize("interrupted", [False, True])
def test_bootstrap_credentials_are_environment_not_arguments(interrupted: bool) -> None:
    with (
        patch("socket.socket") as factory,
        patch("subprocess.Popen") as execute,
        patch("os.getpgrp", return_value=123),
        patch("os.getpid", return_value=123),
        patch("os.killpg") as finish,
    ):
        connection = factory.return_value.__enter__.return_value
        connection.makefile.return_value.__enter__.return_value = io.StringIO(
            '{"KEY": "fake-private-value"}'
        )
        execute.return_value.wait.side_effect = (
            [KeyboardInterrupt, 0] if interrupted else [0]
        )
        WorkerBootstrap.run("private-socket", ["harness", "task"])
        execute.assert_called_once_with(
            ["harness", "task"], env={"KEY": "fake-private-value"}
        )
        finish.assert_called_once_with(123, signal.SIGKILL)


def test_bootstrap_cannot_signal_its_callers_process_group() -> None:
    with (
        patch("os.getpgrp", return_value=123),
        patch("os.getpid", return_value=456),
        patch("os.killpg") as finish,
        pytest.raises(RuntimeError, match="own its process group"),
    ):
        WorkerBootstrap._supervise(["harness"], {})
    finish.assert_not_called()
