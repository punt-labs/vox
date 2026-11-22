"""Tests for :class:`MpvOutage` -- one outage, its restart cap, and its log story.

An outage is told once: an onset WARNING, DEBUG per further attempt, and a
single ``recovered`` or ``failed`` outcome. The ``failed`` outcome's level and
fault kind depend on whether mpv ever passed its handshake.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from punt_vox.types_programs.playback_fault import PlaybackFaultKind
from punt_vox.voxd.programs.mpv.mpv_outage import MpvOutage

if TYPE_CHECKING:
    import pytest

_LOGGER = "punt_vox.voxd.programs.mpv.mpv_outage"


def _records(caplog: pytest.LogCaptureFixture) -> list[tuple[int, str]]:
    return [(r.levelno, r.getMessage()) for r in caplog.records if r.name == _LOGGER]


def test_a_never_ready_mpv_fails_as_unavailable_at_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    outage = MpvOutage(1)
    with caplog.at_level(logging.DEBUG, logger=_LOGGER):
        outage.bring_up_failed()
        assert outage.may_restart()
        outage.bring_up_failed()
        assert not outage.may_restart()
        kind = outage.gave_up()
    assert kind is PlaybackFaultKind.PLAYER_UNAVAILABLE
    assert _records(caplog) == [
        (
            logging.WARNING,
            "mpv unavailable; music playback degraded, retrying up to 1 times",
        ),
        (logging.DEBUG, "mpv bring-up failed: failures=2"),
        (logging.WARNING, "mpv outcome=failed failures=2; music playback unavailable"),
    ]


def test_an_mpv_that_ran_then_died_fails_as_failed_at_error(
    caplog: pytest.LogCaptureFixture,
) -> None:
    outage = MpvOutage(0)
    with caplog.at_level(logging.INFO, logger=_LOGGER):
        outage.ready()
        outage.crashed()
        assert not outage.may_restart()
        kind = outage.gave_up()
    assert kind is PlaybackFaultKind.PLAYER_FAILED
    assert _records(caplog) == [
        (logging.INFO, "mpv ready"),
        (
            logging.WARNING,
            "mpv crashed; music playback degraded, restarting up to 0 times",
        ),
        (logging.ERROR, "mpv outcome=failed failures=1; music playback unavailable"),
    ]


def test_a_recovery_closes_the_outage_and_resets_the_cap(
    caplog: pytest.LogCaptureFixture,
) -> None:
    outage = MpvOutage(1)
    with caplog.at_level(logging.INFO, logger=_LOGGER):
        outage.bring_up_failed()
        outage.bring_up_failed()
        outage.ready()
    assert outage.may_restart()
    assert _records(caplog)[-1] == (logging.INFO, "mpv outcome=recovered failures=2")
