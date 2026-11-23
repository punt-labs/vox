"""The CLI surfaces failed call startup before entering live polling."""

from unittest.mock import ANY, AsyncMock, Mock

import pytest
import typer

from punt_vox.commands.voice_call_control import CallControl
from punt_vox.commands.voice_call_follow import CallFollower
from punt_vox.commands.voice_call_startup import CallStartup
from punt_vox.commands.voice_call_status import CallStatusView


def test_failed_start_reports_its_returned_status_without_polling() -> None:
    control = Mock(spec=CallControl)
    control.request = AsyncMock(
        return_value=CallStatusView(
            {"state": "failed", "call_id": "first", "error": "cannot dial"}
        )
    )
    follower = Mock(spec=CallFollower)
    follower.finish.side_effect = typer.Exit(1)
    with pytest.raises(typer.Exit) as raised:
        CallStartup(control, follower).start({})
    assert raised.value.exit_code == 1
    control.request.assert_awaited_once_with("start", call_id=ANY)
    follower.until_end.assert_not_called()
    assert follower.finish.call_args.args[0].state == "failed"
