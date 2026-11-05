"""``StillUp``: the timeout wording for what a stop tick found still running."""

from __future__ import annotations

from punt_vox.service.still_up import StillUp


def test_nothing_up_is_false() -> None:
    assert not StillUp([], [])


def test_unmanaged_voxd_is_named_still_running() -> None:
    still = StillUp([201, 200], [])
    assert still
    assert still.line() == "voxd pid 200, 201 is still running"


def test_main_process_is_named_as_the_services_own() -> None:
    still = StillUp([], [100])
    assert still
    assert still.line() == "voxd.service's own process pid 100 did not exit"


def test_both_kinds_name_unmanaged_first() -> None:
    assert StillUp([200], [100]).line() == (
        "voxd pid 200 is still running; voxd.service's own process pid 100 did not exit"
    )
