"""Direct coverage of the privacy-sensitive event boundary and failure feedback."""

from __future__ import annotations

import logging
from unittest.mock import Mock

import pytest

from punt_vox.voxd.music_player.command_ports import ProgramSeam
from punt_vox.voxd.music_player.music_event_receiver import MusicEventReceiver
from punt_vox.voxd.music_player.presenter_ports import ScenePresenter

_PRIVATE = "SYNTHETIC-PRIVATE-ALBUM-34C1"


@pytest.fixture
def service() -> Mock:
    commands = Mock(spec=ProgramSeam)
    commands.catalog_albums.return_value = ()
    return commands


@pytest.fixture
def presenter() -> Mock:
    return Mock(spec=ScenePresenter)


def test_stop_applies_once_without_logging_payload(
    service: Mock, presenter: Mock, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    MusicEventReceiver(service, presenter).receive("music.stop", {"extra": _PRIVATE})
    service.stop.assert_called_once_with()
    presenter.present_stop_failure.assert_not_called()
    assert "received music.stop; applying" in caplog.text
    assert _PRIVATE not in caplog.text


@pytest.mark.parametrize("payload", [{}, {"anchor": "", "extra": _PRIVATE}])
def test_empty_anchor_is_rejected_without_payload(
    service: Mock,
    presenter: Mock,
    caplog: pytest.LogCaptureFixture,
    payload: dict[str, object],
) -> None:
    caplog.set_level(logging.DEBUG)
    MusicEventReceiver(service, presenter).receive("music.play", payload)
    service.replay_album.assert_not_called()
    presenter.present_resolve_failure.assert_not_called()
    _assert_rejected_at_warning(caplog)
    assert _PRIVATE not in caplog.text
    assert all(record.exc_info is None for record in caplog.records)


@pytest.mark.parametrize("surface_fails", [False, True])
def test_unresolved_anchor_is_surfaced_but_never_logged(
    service: Mock,
    presenter: Mock,
    caplog: pytest.LogCaptureFixture,
    surface_fails: bool,
) -> None:
    caplog.set_level(logging.DEBUG)
    if surface_fails:
        presenter.present_resolve_failure.side_effect = ValueError(_PRIVATE)
    MusicEventReceiver(service, presenter).receive("music.play", {"anchor": _PRIVATE})
    presenter.present_resolve_failure.assert_called_once_with(_PRIVATE)
    service.replay_album.assert_not_called()
    assert "received music.play; applying" in caplog.text
    assert "names no catalogued album; surfacing" in caplog.text
    assert ("could not surface the resolve failure" in caplog.text) == surface_fails
    assert _PRIVATE not in caplog.text
    assert all(record.exc_info is None for record in caplog.records)


@pytest.mark.parametrize("surface_fails", [False, True])
def test_command_refusal_is_surfaced_without_exception_values(
    service: Mock,
    presenter: Mock,
    caplog: pytest.LogCaptureFixture,
    surface_fails: bool,
) -> None:
    caplog.set_level(logging.DEBUG)
    service.stop.side_effect = ValueError(_PRIVATE)
    if surface_fails:
        presenter.present_stop_failure.side_effect = ValueError(_PRIVATE)
    MusicEventReceiver(service, presenter).receive("music.stop", {})
    service.stop.assert_called_once_with()
    presenter.present_stop_failure.assert_called_once_with()
    assert "music operation could not play; showing it in the scene" in caplog.text
    assert ("could not surface the playback failure" in caplog.text) == surface_fails
    assert _PRIVATE not in caplog.text
    assert all(record.exc_info is None for record in caplog.records)


@pytest.mark.parametrize(
    ("command", "method"),
    [("next", "advance"), ("prev", "prev"), ("pause", "pause"), ("resume", "resume")],
)
def test_idle_transport_reprojects_without_a_fault(
    service: Mock,
    presenter: Mock,
    caplog: pytest.LogCaptureFixture,
    command: str,
    method: str,
) -> None:
    caplog.set_level(logging.INFO)
    transport = getattr(service, method)
    transport.return_value = False
    MusicEventReceiver(service, presenter).receive(
        f"music.{command}", {"extra": _PRIVATE}
    )
    transport.assert_called_once_with()
    presenter.present_transport_failure.assert_called_once_with()
    assert f"received music.{command}; applying" in caplog.text
    assert [
        record.levelno
        for record in caplog.records
        if "ignoring transport control: Nothing playing." in record.getMessage()
    ] == [logging.INFO]
    assert all(record.levelno < logging.WARNING for record in caplog.records)
    assert _PRIVATE not in caplog.text


def test_unknown_topic_is_sanitized(
    service: Mock, presenter: Mock, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    MusicEventReceiver(service, presenter).receive(_PRIVATE, {"anchor": _PRIVATE})
    service.stop.assert_not_called()
    service.replay_album.assert_not_called()
    assert "received unknown; applying" in caplog.text
    _assert_rejected_at_warning(caplog)
    assert _PRIVATE not in caplog.text


def test_unexpected_decode_fault_stays_an_error(
    service: Mock, presenter: Mock, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    service.catalog_albums.side_effect = RuntimeError(_PRIVATE)
    MusicEventReceiver(service, presenter).receive("music.play", {"anchor": "x"})
    service.replay_album.assert_not_called()
    errors = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert [r.getMessage() for r in errors] == [
        "[lux] dropping music event: reason=unexpected_error"
    ]
    assert _PRIVATE not in caplog.text


def _assert_rejected_at_warning(caplog: pytest.LogCaptureFixture) -> None:
    """Assert an expected rejection logged one WARNING and no ERROR."""
    rejected = [r for r in caplog.records if "reason=invalid_event" in r.getMessage()]
    assert [r.levelno for r in rejected] == [logging.WARNING]
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]
