"""Tests for MusicControlChannel -- ownership and the loop control signal."""
# pyright: reportPrivateUsage=false

from __future__ import annotations

from punt_vox.voxd.music.control import MusicControlChannel

__all__: list[str] = []


class TestOwnership:
    """claim / release / owned_by manage who may drive music."""

    def test_starts_unowned(self) -> None:
        assert MusicControlChannel().owner == ""

    def test_claim_records_owner(self) -> None:
        chan = MusicControlChannel()
        chan.claim("u1")
        assert chan.owner == "u1"
        assert chan.owned_by("u1")

    def test_release_clears_owner(self) -> None:
        chan = MusicControlChannel()
        chan.claim("u1")
        chan.release()
        assert chan.owner == ""
        assert not chan.owned_by("u1")

    def test_owned_by_rejects_other_session(self) -> None:
        chan = MusicControlChannel()
        chan.claim("u1")
        assert not chan.owned_by("other")

    def test_owned_by_rejects_empty_even_when_unowned(self) -> None:
        # An empty owner_id must never match an unowned channel -- otherwise a
        # message with no session would be accepted as the owner.
        assert not MusicControlChannel().owned_by("")


class TestSignal:
    """signal records a pending action and wakes the loop; take is one-shot."""

    def test_default_action_is_none(self) -> None:
        chan = MusicControlChannel()
        assert chan.take() == "none"
        assert not chan.changed.is_set()

    def test_signal_sets_action_and_event(self) -> None:
        chan = MusicControlChannel()
        chan.signal("skip")
        assert chan.changed.is_set()
        assert chan.take() == "skip"

    def test_take_is_one_shot(self) -> None:
        chan = MusicControlChannel()
        chan.signal("off")
        assert chan.take() == "off"
        assert chan.take() == "none"  # reset after read

    def test_signal_overwrites_prior_pending_action(self) -> None:
        chan = MusicControlChannel()
        chan.signal("vibe")
        chan.signal("off")
        assert chan.take() == "off"  # latest wins
