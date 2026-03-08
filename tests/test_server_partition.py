"""Partition tests for the notify/speak state machine.

Derived from the Z specification (docs/vox-notify.tex) using TTF
testing tactics.  Each test corresponds to a numbered partition in
the formal analysis.  The pre-state → post-state mapping verifies
that the implementation conforms to the spec.

Z spec mapping:
    NotifyMode:  nOff = "n",  nOn = "y",  nCont = "c"
    SpeakMode:   sUnset = None (field absent),  sChime = "n",  sVoice = "y"
    voice:       ∅ = field absent,  {v} = field present
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from punt_vox.config import read_field
from punt_vox.server import notify, speak


@pytest.fixture()
def _patch_config(  # pyright: ignore[reportUnusedFunction]
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Path:
    """Return a writable config path and patch the module."""
    import punt_vox.config as cfg
    import punt_vox.server as srv

    config = tmp_path / "config.md"
    monkeypatch.setattr(srv, "_CONFIG_PATH", config)
    monkeypatch.setattr(cfg, "DEFAULT_CONFIG_PATH", config)
    return config


def _write_state(
    config: Path,
    *,
    notify_mode: str | None = None,
    speak_mode: str | None = None,
    voice: str | None = None,
) -> None:
    """Write a config pre-state.  None fields are omitted (absent)."""
    lines = ["---\n"]
    if notify_mode is not None:
        lines.append(f'notify: "{notify_mode}"\n')
    if speak_mode is not None:
        lines.append(f'speak: "{speak_mode}"\n')
    if voice is not None:
        lines.append(f'voice: "{voice}"\n')
    lines.append("---\n")
    config.write_text("".join(lines))


def _read_state(config: Path) -> dict[str, str | None]:
    """Read the full post-state from config."""
    return {
        "notify": read_field("notify", config),
        "speak": read_field("speak", config),
        "voice": read_field("voice", config),
    }


# ---------------------------------------------------------------------------
# VoxOn — notify(mode="y")
# Z spec: notify' = nOn; speak' = IF speak = sUnset THEN sVoice ELSE speak
# ---------------------------------------------------------------------------


class TestVoxOn:
    """Partitions 1-6: VoxOn operation (/vox y)."""

    def test_partition_1_init_sunset_from_noff(self, _patch_config: Path) -> None:
        """P1: nOff/sUnset/∅ → nOn/sVoice/∅ — first enable initializes voice."""
        # No config file exists = sUnset
        result = json.loads(notify(mode="y"))
        assert result["notify"]["notify"] == "y"
        assert result["notify"]["speak"] == "y"
        state = _read_state(_patch_config)
        assert state["notify"] == "y"
        assert state["speak"] == "y"
        assert state["voice"] is None

    def test_partition_2_init_sunset_voice_preserved(self, _patch_config: Path) -> None:
        """P2: nOff/sUnset/{v1} → nOn/sVoice/{v1} — voice preserved through init."""
        _write_state(_patch_config, notify_mode="n", voice="matilda")
        # speak absent = sUnset
        result = json.loads(notify(mode="y"))
        assert result["notify"]["speak"] == "y"
        state = _read_state(_patch_config)
        assert state["voice"] == "matilda"

    def test_partition_3_preserve_schime(self, _patch_config: Path) -> None:
        """P3: nOff/sChime/∅ → nOn/sChime/∅ — muted user stays muted."""
        _write_state(_patch_config, notify_mode="n", speak_mode="n")
        result = json.loads(notify(mode="y"))
        assert "speak" not in result["notify"]
        state = _read_state(_patch_config)
        assert state["notify"] == "y"
        assert state["speak"] == "n"

    def test_partition_4_preserve_svoice(self, _patch_config: Path) -> None:
        """P4: nOff/sVoice/{v1} → nOn/sVoice/{v1} — unmuted user stays unmuted."""
        _write_state(_patch_config, notify_mode="n", speak_mode="y", voice="roger")
        result = json.loads(notify(mode="y"))
        assert "speak" not in result["notify"]
        state = _read_state(_patch_config)
        assert state["notify"] == "y"
        assert state["speak"] == "y"
        assert state["voice"] == "roger"

    def test_partition_5_idempotent_already_non(self, _patch_config: Path) -> None:
        """P5: nOn/sVoice/∅ → nOn/sVoice/∅ — idempotent."""
        _write_state(_patch_config, notify_mode="y", speak_mode="y")
        notify(mode="y")
        state = _read_state(_patch_config)
        assert state["notify"] == "y"
        assert state["speak"] == "y"

    def test_partition_6_downgrade_from_ncont(self, _patch_config: Path) -> None:
        """P6: nCont/sChime/∅ -> nOn/sChime/∅ -- cont to on, speak preserved."""
        _write_state(_patch_config, notify_mode="c", speak_mode="n")
        notify(mode="y")
        state = _read_state(_patch_config)
        assert state["notify"] == "y"
        assert state["speak"] == "n"


# ---------------------------------------------------------------------------
# VoxOff — notify(mode="n")
# Z spec: notify' = nOff; speak' = speak; voice' = voice
# ---------------------------------------------------------------------------


class TestVoxOff:
    """Partitions 7-10: VoxOff operation (/vox n)."""

    def test_partition_7_off_from_non_svoice(self, _patch_config: Path) -> None:
        """P7: nOn/sVoice/{v1} → nOff/sVoice/{v1} — speak and voice preserved."""
        _write_state(_patch_config, notify_mode="y", speak_mode="y", voice="matilda")
        notify(mode="n")
        state = _read_state(_patch_config)
        assert state["notify"] == "n"
        assert state["speak"] == "y"
        assert state["voice"] == "matilda"

    def test_partition_8_off_from_ncont_schime(self, _patch_config: Path) -> None:
        """P8: nCont/sChime/∅ → nOff/sChime/∅ — speak preserved through disable."""
        _write_state(_patch_config, notify_mode="c", speak_mode="n")
        notify(mode="n")
        state = _read_state(_patch_config)
        assert state["notify"] == "n"
        assert state["speak"] == "n"

    def test_partition_9_off_from_noff_sunset(self, _patch_config: Path) -> None:
        """P9: nOff/sUnset/∅ → nOff/sUnset/∅ — idempotent from Init."""
        _write_state(_patch_config, notify_mode="n")
        # speak absent = sUnset
        notify(mode="n")
        state = _read_state(_patch_config)
        assert state["notify"] == "n"
        assert state["speak"] is None  # sUnset preserved

    def test_partition_10_off_preserves_sunset(self, _patch_config: Path) -> None:
        """P10: VoxOff does not touch sUnset — it remains absent."""
        # No config at all
        notify(mode="n")
        state = _read_state(_patch_config)
        assert state["notify"] == "n"
        assert state["speak"] is None  # Still sUnset


# ---------------------------------------------------------------------------
# VoxCont — notify(mode="c")
# Z spec: notify' = nCont; speak' = IF speak = sUnset THEN sVoice ELSE speak
# ---------------------------------------------------------------------------


class TestVoxCont:
    """Partitions 11-14: VoxCont operation (/vox c)."""

    def test_partition_11_init_sunset_from_noff(self, _patch_config: Path) -> None:
        """P11: nOff/sUnset/∅ → nCont/sVoice/∅ — first enable initializes voice."""
        _write_state(_patch_config, notify_mode="n")
        result = json.loads(notify(mode="c"))
        assert result["notify"]["notify"] == "c"
        assert result["notify"]["speak"] == "y"

    def test_partition_12_preserve_schime(self, _patch_config: Path) -> None:
        """P12: nOff/sChime/∅ → nCont/sChime/∅ — muted user stays muted."""
        _write_state(_patch_config, notify_mode="n", speak_mode="n")
        result = json.loads(notify(mode="c"))
        assert "speak" not in result["notify"]
        state = _read_state(_patch_config)
        assert state["notify"] == "c"
        assert state["speak"] == "n"

    def test_partition_13_upgrade_non_to_ncont(self, _patch_config: Path) -> None:
        """P13: nOn/sVoice/{v1} → nCont/sVoice/{v1} — upgrade, speak preserved."""
        _write_state(_patch_config, notify_mode="y", speak_mode="y", voice="roger")
        notify(mode="c")
        state = _read_state(_patch_config)
        assert state["notify"] == "c"
        assert state["speak"] == "y"
        assert state["voice"] == "roger"

    def test_partition_14_idempotent_already_ncont(self, _patch_config: Path) -> None:
        """P14: nCont/sVoice/∅ → nCont/sVoice/∅ — no-op."""
        _write_state(_patch_config, notify_mode="c", speak_mode="y")
        notify(mode="c")
        state = _read_state(_patch_config)
        assert state["notify"] == "c"
        assert state["speak"] == "y"


# ---------------------------------------------------------------------------
# Unmute — speak(mode="y", voice=...)
# Z spec: notify' = notify; speak' = sVoice;
#         voice' = IF v? = ∅ THEN voice ELSE v?
# ---------------------------------------------------------------------------


class TestUnmute:
    """Partitions 15-21: Unmute operation (/unmute)."""

    def test_partition_15_unmute_from_schime(self, _patch_config: Path) -> None:
        """P15: nOn/sChime/∅ → nOn/sVoice/∅ — core use case."""
        _write_state(_patch_config, notify_mode="y", speak_mode="n")
        speak(mode="y")
        state = _read_state(_patch_config)
        assert state["notify"] == "y"  # notify frame
        assert state["speak"] == "y"
        assert state["voice"] is None

    def test_partition_16_unmute_from_sunset(self, _patch_config: Path) -> None:
        """P16: nOff/sUnset/∅ -> nOff/sVoice/∅ -- speak init, no notify."""
        _write_state(_patch_config, notify_mode="n")
        speak(mode="y")
        state = _read_state(_patch_config)
        assert state["notify"] == "n"  # notify frame
        assert state["speak"] == "y"

    def test_partition_17_idempotent_already_svoice(self, _patch_config: Path) -> None:
        """P17: nCont/sVoice/{v1} → nCont/sVoice/{v1} — no-op."""
        _write_state(_patch_config, notify_mode="c", speak_mode="y", voice="matilda")
        speak(mode="y")
        state = _read_state(_patch_config)
        assert state["notify"] == "c"
        assert state["speak"] == "y"
        assert state["voice"] == "matilda"

    def test_partition_18_set_voice_replaces(self, _patch_config: Path) -> None:
        """P18: nOn/sChime/{v1} + v?={v2} → nOn/sVoice/{v2} — voice replaced."""
        _write_state(_patch_config, notify_mode="y", speak_mode="n", voice="matilda")
        result = json.loads(speak(mode="y", voice="roger"))
        assert result["voice"] == "roger"
        state = _read_state(_patch_config)
        assert state["speak"] == "y"
        assert state["voice"] == "roger"

    def test_partition_19_set_voice_from_empty(self, _patch_config: Path) -> None:
        """P19: nOff/sUnset/∅ + v?={v1} -> nOff/sVoice/{v1} -- first unmute."""
        _write_state(_patch_config, notify_mode="n")
        result = json.loads(speak(mode="y", voice="sarah"))
        assert result["voice"] == "sarah"
        state = _read_state(_patch_config)
        assert state["speak"] == "y"
        assert state["voice"] == "sarah"
        assert state["notify"] == "n"  # notify frame

    def test_partition_20_notify_frame_noff(self, _patch_config: Path) -> None:
        """P20: nOff/sChime/∅ → nOff/sVoice/∅ — unmute doesn't enable notifications."""
        _write_state(_patch_config, notify_mode="n", speak_mode="n")
        speak(mode="y")
        state = _read_state(_patch_config)
        assert state["notify"] == "n"
        assert state["speak"] == "y"

    def test_partition_21_rejected_invalid_mode(self, _patch_config: Path) -> None:
        """P21: Invalid mode rejected — API guard."""
        _write_state(_patch_config, notify_mode="y", speak_mode="n")
        result = json.loads(speak(mode="x"))
        assert "error" in result
        # State unchanged
        state = _read_state(_patch_config)
        assert state["speak"] == "n"


# ---------------------------------------------------------------------------
# Mute — speak(mode="n")
# Z spec: notify' = notify; speak' = sChime; voice' = voice
# ---------------------------------------------------------------------------


class TestMute:
    """Partitions 22-25: Mute operation (/mute)."""

    def test_partition_22_mute_from_svoice(self, _patch_config: Path) -> None:
        """P22: nOn/sVoice/{v1} → nOn/sChime/{v1} — core use case, voice preserved."""
        _write_state(_patch_config, notify_mode="y", speak_mode="y", voice="matilda")
        speak(mode="n")
        state = _read_state(_patch_config)
        assert state["notify"] == "y"
        assert state["speak"] == "n"
        assert state["voice"] == "matilda"

    def test_partition_23_mute_from_sunset(self, _patch_config: Path) -> None:
        """P23: nOff/sUnset/∅ → nOff/sChime/∅ — explicit mute before first enable."""
        _write_state(_patch_config, notify_mode="n")
        speak(mode="n")
        state = _read_state(_patch_config)
        assert state["notify"] == "n"
        assert state["speak"] == "n"

    def test_partition_24_idempotent_already_schime(self, _patch_config: Path) -> None:
        """P24: nCont/sChime/∅ → nCont/sChime/∅ — no-op."""
        _write_state(_patch_config, notify_mode="c", speak_mode="n")
        speak(mode="n")
        state = _read_state(_patch_config)
        assert state["notify"] == "c"
        assert state["speak"] == "n"

    def test_partition_25_notify_frame_ncont(self, _patch_config: Path) -> None:
        """P25: nCont/sVoice/∅ → nCont/sChime/∅ — mute doesn't change notify."""
        _write_state(_patch_config, notify_mode="c", speak_mode="y")
        speak(mode="n")
        state = _read_state(_patch_config)
        assert state["notify"] == "c"
        assert state["speak"] == "n"


# ---------------------------------------------------------------------------
# Observation partitions — config-based guards
# These verify hook/watcher guard predicates against static config state
# (written via _write_state, not produced by notify/speak operations).
# ---------------------------------------------------------------------------


class TestStopObservations:
    """Partitions 26-31: StopFires/StopChime/StopVoice observations."""

    def test_partition_26_stop_fires_non(self, _patch_config: Path) -> None:
        """P26: nOn/sVoice → stop fires (notify ≠ nOff)."""
        _write_state(_patch_config, notify_mode="y", speak_mode="y")
        assert read_field("notify", _patch_config) != "n"

    def test_partition_27_stop_fires_ncont(self, _patch_config: Path) -> None:
        """P27: nCont/sChime → stop fires (notify ≠ nOff)."""
        _write_state(_patch_config, notify_mode="c", speak_mode="n")
        assert read_field("notify", _patch_config) != "n"

    def test_partition_28_stop_rejected_noff(self, _patch_config: Path) -> None:
        """P28: nOff → stop does not fire (notify = nOff)."""
        _write_state(_patch_config, notify_mode="n", speak_mode="y")
        assert read_field("notify", _patch_config) == "n"

    def test_partition_29_stop_chime_output(self, _patch_config: Path) -> None:
        """P29: nOn/sChime → StopChime (speak = "n")."""
        _write_state(_patch_config, notify_mode="y", speak_mode="n")
        assert read_field("notify", _patch_config) != "n"
        assert read_field("speak", _patch_config) == "n"

    def test_partition_30_stop_voice_output(self, _patch_config: Path) -> None:
        """P30: nOn/sVoice → StopVoice (speak ≠ "n")."""
        _write_state(_patch_config, notify_mode="y", speak_mode="y")
        assert read_field("speak", _patch_config) != "n"

    def test_partition_31_stop_voice_sunset(self, _patch_config: Path) -> None:
        """P31: nOn/sUnset → StopVoice (speak ≠ sChime, sUnset behaves as voice).

        Note: sUnset with nOn is unreachable in practice (VoxOn initializes
        speak), but verifies the observation guard logic is correct.
        """
        # Manually construct this state to test the guard
        _write_state(_patch_config, notify_mode="y")
        assert read_field("speak", _patch_config) != "n"


class TestWatcherObservations:
    """Partitions 33-38: WatcherFires/WatcherChime/WatcherVoice observations."""

    def test_partition_33_watcher_fires_ncont_svoice(self, _patch_config: Path) -> None:
        """P33: nCont/sVoice → watcher fires (notify = "c")."""
        _write_state(_patch_config, notify_mode="c", speak_mode="y")
        assert read_field("notify", _patch_config) == "c"

    def test_partition_34_watcher_fires_ncont_schime(self, _patch_config: Path) -> None:
        """P34: nCont/sChime → watcher fires (notify = "c")."""
        _write_state(_patch_config, notify_mode="c", speak_mode="n")
        assert read_field("notify", _patch_config) == "c"

    def test_partition_35_watcher_rejected_non(self, _patch_config: Path) -> None:
        """P35: nOn → watcher does not fire (notify ≠ "c")."""
        _write_state(_patch_config, notify_mode="y", speak_mode="y")
        assert read_field("notify", _patch_config) != "c"

    def test_partition_36_watcher_rejected_noff(self, _patch_config: Path) -> None:
        """P36: nOff → watcher does not fire (notify ≠ "c")."""
        _write_state(_patch_config, notify_mode="n", speak_mode="y")
        assert read_field("notify", _patch_config) != "c"

    def test_partition_37_watcher_chime_output(self, _patch_config: Path) -> None:
        """P37: nCont/sChime → WatcherChime."""
        _write_state(_patch_config, notify_mode="c", speak_mode="n")
        assert read_field("notify", _patch_config) == "c"
        assert read_field("speak", _patch_config) == "n"

    def test_partition_38_watcher_voice_output(self, _patch_config: Path) -> None:
        """P38: nCont/sVoice → WatcherVoice."""
        _write_state(_patch_config, notify_mode="c", speak_mode="y")
        assert read_field("notify", _patch_config) == "c"
        assert read_field("speak", _patch_config) != "n"


# ---------------------------------------------------------------------------
# Invariant preservation
# ---------------------------------------------------------------------------


class TestInvariantPreservation:
    """Partitions 39-41: Design invariants via multi-step sequences."""

    def test_partition_39_voice_replacement(self, _patch_config: Path) -> None:
        """P39: Unmute replaces voice (not union) — #voice ≤ 1 maintained."""
        _write_state(_patch_config, notify_mode="y", speak_mode="y", voice="matilda")
        speak(mode="y", voice="roger")
        state = _read_state(_patch_config)
        assert state["voice"] == "roger"
        # Only one voice, not both

    def test_partition_40_sunset_one_way(self, _patch_config: Path) -> None:
        """P40: sUnset -> sVoice is one-way; VoxOff+VoxOn keeps sVoice."""
        # Step 1: VoxOn from Init (sUnset → sVoice)
        notify(mode="y")
        state = _read_state(_patch_config)
        assert state["speak"] == "y"  # sVoice

        # Step 2: VoxOff (speak preserved)
        notify(mode="n")
        state = _read_state(_patch_config)
        assert state["speak"] == "y"  # Still sVoice, NOT sUnset

        # Step 3: VoxOn again (speak still sVoice, not re-initialized)
        result = json.loads(notify(mode="y"))
        assert "speak" not in result["notify"]  # No init needed
        state = _read_state(_patch_config)
        assert state["speak"] == "y"

    def test_partition_41_sticky_mute_through_enable(self, _patch_config: Path) -> None:
        """P41: User's mute choice survives notify enable — design invariant #2."""
        # Step 1: Mute (sUnset → sChime)
        speak(mode="n")
        state = _read_state(_patch_config)
        assert state["speak"] == "n"

        # Step 2: VoxCont — sChime preserved (not overridden to sVoice)
        notify(mode="c")
        state = _read_state(_patch_config)
        assert state["speak"] == "n"  # Sticky!
        assert state["notify"] == "c"


# ---------------------------------------------------------------------------
# Rejected partitions — invalid inputs
# ---------------------------------------------------------------------------


class TestRejectedInputs:
    """Additional rejected partition for notify tool."""

    def test_notify_invalid_mode(self, _patch_config: Path) -> None:
        """Notify rejects invalid mode values."""
        _write_state(_patch_config, notify_mode="y", speak_mode="y")
        result = json.loads(notify(mode="x"))
        assert "error" in result
        # State unchanged
        state = _read_state(_patch_config)
        assert state["notify"] == "y"

    def test_speak_invalid_mode(self, _patch_config: Path) -> None:
        """Speak rejects invalid mode values."""
        _write_state(_patch_config, notify_mode="y", speak_mode="y")
        result = json.loads(speak(mode="c"))
        assert "error" in result
        state = _read_state(_patch_config)
        assert state["speak"] == "y"
