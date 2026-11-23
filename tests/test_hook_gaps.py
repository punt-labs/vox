"""Durable hook gap tracking."""

from punt_vox.voxd.hook_gaps import HookGaps


def test_restored_gap_can_be_healed_without_replaying_deliveries() -> None:
    original = HookGaps()
    assert original.observe(4)
    saved = original.report()
    assert not HookGaps.received(saved, 2)
    assert HookGaps.received(saved, 4)

    restored = HookGaps()
    restored.restore(saved)
    assert not restored.observe(4)
    assert restored.observe(2)
    assert restored.report()["missing_ranges"] == [(1, 1), (3, 3)]
