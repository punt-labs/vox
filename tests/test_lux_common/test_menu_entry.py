"""Tests for :mod:`punt_vox.lux_common.menu_entry`.

Three things an entry promises: it cannot be built with a field that would make
it silently useless, it recognises the clicks that are its own, and registering
it hands lux all three fields -- the frame id above all, since that is what
lets a click reopen a closed window -- while leaving lux's answer, refusal or
outage, to the caller.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast, final

import pytest
from punt_lux import HubUnavailableError, OpError
from punt_lux.operations import Ok

from punt_vox.lux_common import MenuEntry

if TYPE_CHECKING:
    from punt_lux import LuxClient

_ENTRY = MenuEntry("music", "Music", "vox.music")


@final
class _FakeCallbacks:
    """``client.callback``: records each registration, answering *result*."""

    def __init__(self, result: Ok | OpError | HubUnavailableError) -> None:
        self._result = result
        self.registered: list[tuple[str, str, str]] = []

    async def register(
        self, callback_id: str, label: str, frame_id: str
    ) -> Ok | OpError:
        self.registered.append((callback_id, label, frame_id))
        if isinstance(self._result, HubUnavailableError):
            raise self._result
        return self._result


@final
class _FakeClient:
    """A ``LuxClient`` exposing only the ``callback`` accessor."""

    def __init__(self, result: Ok | OpError | HubUnavailableError) -> None:
        self.callback = _FakeCallbacks(result)


def _client(fake: _FakeClient) -> LuxClient:
    return cast("LuxClient", fake)


class TestConstruction:
    def test_a_complete_entry_keeps_its_fields(self) -> None:
        assert (_ENTRY.callback_id, _ENTRY.label, _ENTRY.frame_id) == (
            "music",
            "Music",
            "vox.music",
        )

    @pytest.mark.parametrize(
        ("callback_id", "label", "frame_id", "missing"),
        [
            ("", "Music", "vox.music", "callback_id"),
            ("music", "", "vox.music", "label"),
            ("music", "Music", "", "frame_id"),
        ],
    )
    def test_an_empty_field_is_refused(
        self, callback_id: str, label: str, frame_id: str, missing: str
    ) -> None:
        with pytest.raises(ValueError, match=f"non-empty {missing}"):
            MenuEntry(callback_id, label, frame_id)

    def test_every_empty_field_is_named(self) -> None:
        with pytest.raises(ValueError, match="callback_id, label, frame_id"):
            MenuEntry("", "", "")


class TestOwns:
    def test_its_own_callback_id_is_owned(self) -> None:
        assert _ENTRY.owns("music") is True

    @pytest.mark.parametrize("callback_id", ["vox-panel", "Music", "music ", ""])
    def test_any_other_callback_id_is_not(self, callback_id: str) -> None:
        assert _ENTRY.owns(callback_id) is False


class TestRegisterOn:
    async def test_the_frame_id_reaches_lux(self) -> None:
        fake = _FakeClient(Ok())

        await _ENTRY.register_on(_client(fake))

        assert fake.callback.registered == [("music", "Music", "vox.music")]

    async def test_a_landed_registration_answers_ok(self) -> None:
        assert isinstance(await _ENTRY.register_on(_client(_FakeClient(Ok()))), Ok)

    async def test_a_refusal_is_returned_not_raised(self) -> None:
        refusal = OpError(code="rejected", reason="taken")

        result = await _ENTRY.register_on(_client(_FakeClient(refusal)))

        assert result is refusal

    async def test_an_outage_propagates(self) -> None:
        fake = _FakeClient(HubUnavailableError("luxd down"))

        with pytest.raises(HubUnavailableError, match="luxd down"):
            await _ENTRY.register_on(_client(fake))
