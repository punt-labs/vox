"""Private environment transfer and strict bootstrap decoding."""

from __future__ import annotations

import io
import json
import socket
import stat
from unittest.mock import patch

import pytest

from punt_vox.voxd.workers.bootstrap import WorkerBootstrap
from punt_vox.voxd.workers.environment import WorkerEnvironment


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


@pytest.mark.parametrize("payload", ["[]", '{"KEY": 1}'])
def test_bootstrap_rejects_non_string_environments(payload: str) -> None:
    with patch("socket.socket") as factory:
        connection = factory.return_value.__enter__.return_value
        connection.makefile.return_value.__enter__.return_value = io.StringIO(payload)
        with pytest.raises(ValueError, match="Worker environment must"):
            WorkerBootstrap.run("private-socket", ["harness"])


def test_bootstrap_credentials_are_environment_not_arguments() -> None:
    with patch("socket.socket") as factory, patch("os.execvpe") as execute:
        connection = factory.return_value.__enter__.return_value
        connection.makefile.return_value.__enter__.return_value = io.StringIO(
            '{"KEY": "fake-private-value"}'
        )
        WorkerBootstrap.run("private-socket", ["harness", "task"])
        execute.assert_called_once_with(
            "harness", ["harness", "task"], {"KEY": "fake-private-value"}
        )
