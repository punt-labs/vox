"""The CLI surfaces failed call startup before entering live polling."""

from unittest.mock import AsyncMock, Mock, call

import pytest
import typer

from punt_vox.client_errors import VoxdProtocolError
from punt_vox.commands.voice_call_follow import CallFollower
from punt_vox.commands.voice_call_startup import CallStartup
from punt_vox.daemon_control import DaemonControl


def test_refused_start_reports_failed_status_without_polling() -> None:
    control = Mock(spec=DaemonControl)
    control.request = AsyncMock(
        side_effect=[VoxdProtocolError("cannot dial"), {"state": "failed"}]
    )
    follower = Mock(spec=CallFollower)
    with pytest.raises(typer.Exit) as raised:
        CallStartup(control, follower).start(None)
    assert raised.value.exit_code == 1
    assert control.request.await_args_list == [
        call("start", timeout=60.0),
        call("status", timeout=30.0),
    ]
    follower.until_end.assert_not_called()
    assert follower.finish.call_args.args[0].state == "failed"
