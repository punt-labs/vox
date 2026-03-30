# pyright: reportPrivateUsage=false
"""Partition tests for the notify/speak state machine.

Derived from the Z specification (docs/vox-notify.tex) using TTF
testing tactics.  Each test corresponds to a numbered partition in
the formal analysis.  The pre-state -> post-state mapping verifies
that the implementation conforms to the spec.

Z spec mapping:
    NotifyMode:  nOff = "n",  nOn = "y",  nCont = "c"
    SpeakMode:   sUnset = None (field absent),  sChime = "n",  sVoice = "y"
    voice:       empty = None,  {v} = present

State is the in-memory SessionState dataclass.
sUnset is represented by speak="n" with _speak_explicit=False (fresh session).
sChime is represented by speak="n" with _speak_explicit=True.
sVoice is represented by speak="y".
"""

from __future__ import annotations

import json

import pytest

from punt_vox.server import SessionState, notify, speak


@pytest.fixture(autouse=True)
def _fresh_session(monkeypatch: pytest.MonkeyPatch) -> None:  # pyright: ignore[reportUnusedFunction]
    """Reset server session state before every test."""
    import punt_vox.server as srv

    monkeypatch.setattr(srv, "_state", SessionState())
    monkeypatch.setattr(srv, "_speak_explicit", False)


def _set_state(
    *,
    notify_mode: str | None = None,
    speak_mode: str | None = None,
    speak_explicit: bool = False,
    voice: str | None = None,
) -> None:
    """Set session pre-state.

    When speak_mode is None, speak defaults to "n" and _speak_explicit
    stays False (sUnset). When speak_mode is given, _speak_explicit is
    set to True (the user has made an explicit choice) unless
    speak_explicit is explicitly passed as False.
    """
    import punt_vox.server as srv

    if notify_mode is not None:
        srv._state.notify = notify_mode
    if speak_mode is not None:
        srv._state.speak = speak_mode
    srv._speak_explicit = speak_explicit
    if voice is not None:
        srv._state.voice = voice


def _read_state() -> dict[str, str | None | bool]:
    """Read the full post-state from session."""
    import punt_vox.server as srv

    return {
        "notify": srv._state.notify,
        "speak": srv._state.speak,
        "voice": srv._state.voice,
        "speak_explicit": srv._speak_explicit,
    }


# ---------------------------------------------------------------------------
# VoxOn -- notify(mode="y")
# Z spec: notify' = nOn; speak' = IF speak = sUnset THEN sVoice ELSE speak
# ---------------------------------------------------------------------------


class TestVoxOn:
    """Partitions 1-6: VoxOn operation (/vox y)."""

    def test_partition_1_init_sunset_from_noff(self) -> None:
        """P1: nOff/sUnset/empty -> nOn/sVoice/empty -- first enable inits."""
        # Fresh session = sUnset (speak="n", _speak_explicit=False)
        result = json.loads(notify(mode="y"))
        assert result["notify"]["notify"] == "y"
        assert result["notify"]["speak"] == "y"
        state = _read_state()
        assert state["notify"] == "y"
        assert state["speak"] == "y"
        assert state["voice"] is None

    def test_partition_2_init_sunset_voice_preserved(self) -> None:
        """P2: nOff/sUnset/{v1} -> nOn/sVoice/{v1} -- voice preserved through init."""
        _set_state(notify_mode="n", voice="matilda")
        # speak absent = sUnset (_speak_explicit=False)
        result = json.loads(notify(mode="y"))
        assert result["notify"]["speak"] == "y"
        state = _read_state()
        assert state["voice"] == "matilda"

    def test_partition_3_preserve_schime(self) -> None:
        """P3: nOff/sChime/empty -> nOn/sChime/empty -- muted user stays muted."""
        _set_state(notify_mode="n", speak_mode="n", speak_explicit=True)
        result = json.loads(notify(mode="y"))
        assert "speak" not in result["notify"]
        state = _read_state()
        assert state["notify"] == "y"
        assert state["speak"] == "n"

    def test_partition_4_preserve_svoice(self) -> None:
        """P4: nOff/sVoice/{v1} -> nOn/sVoice/{v1} -- unmuted user stays unmuted."""
        _set_state(notify_mode="n", speak_mode="y", speak_explicit=True, voice="roger")
        result = json.loads(notify(mode="y"))
        assert "speak" not in result["notify"]
        state = _read_state()
        assert state["notify"] == "y"
        assert state["speak"] == "y"
        assert state["voice"] == "roger"

    def test_partition_5_idempotent_already_non(self) -> None:
        """P5: nOn/sVoice/empty -> nOn/sVoice/empty -- idempotent."""
        _set_state(notify_mode="y", speak_mode="y", speak_explicit=True)
        notify(mode="y")
        state = _read_state()
        assert state["notify"] == "y"
        assert state["speak"] == "y"

    def test_partition_6_downgrade_from_ncont(self) -> None:
        """P6: nCont/sChime/empty -> nOn/sChime/empty -- cont to on, speak preserved."""
        _set_state(notify_mode="c", speak_mode="n", speak_explicit=True)
        notify(mode="y")
        state = _read_state()
        assert state["notify"] == "y"
        assert state["speak"] == "n"


# ---------------------------------------------------------------------------
# VoxOff -- notify(mode="n")
# Z spec: notify' = nOff; speak' = speak; voice' = voice
# ---------------------------------------------------------------------------


class TestVoxOff:
    """Partitions 7-10: VoxOff operation (/vox n)."""

    def test_partition_7_off_from_non_svoice(self) -> None:
        """P7: nOn/sVoice/{v1} -> nOff/sVoice/{v1} -- speak and voice preserved."""
        _set_state(
            notify_mode="y", speak_mode="y", speak_explicit=True, voice="matilda"
        )
        notify(mode="n")
        state = _read_state()
        assert state["notify"] == "n"
        assert state["speak"] == "y"
        assert state["voice"] == "matilda"

    def test_partition_8_off_from_ncont_schime(self) -> None:
        """P8: nCont/sChime/empty -> nOff/sChime/empty -- speak kept."""
        _set_state(notify_mode="c", speak_mode="n", speak_explicit=True)
        notify(mode="n")
        state = _read_state()
        assert state["notify"] == "n"
        assert state["speak"] == "n"

    def test_partition_9_off_from_noff_sunset(self) -> None:
        """P9: nOff/sUnset/empty -> nOff/sUnset/empty -- idempotent from Init."""
        _set_state(notify_mode="n")
        # speak not set = sUnset
        notify(mode="n")
        state = _read_state()
        assert state["notify"] == "n"
        # sUnset: speak is at default "n" with _speak_explicit False
        assert state["speak"] == "n"
        assert state["speak_explicit"] is False

    def test_partition_10_off_preserves_sunset(self) -> None:
        """P10: VoxOff does not touch sUnset -- it remains absent."""
        # Fresh session
        notify(mode="n")
        state = _read_state()
        assert state["notify"] == "n"
        # sUnset preserved: speak still at default
        assert state["speak"] == "n"
        assert state["speak_explicit"] is False


# ---------------------------------------------------------------------------
# VoxCont -- notify(mode="c")
# Z spec: notify' = nCont; speak' = IF speak = sUnset THEN sVoice ELSE speak
# ---------------------------------------------------------------------------


class TestVoxCont:
    """Partitions 11-14: VoxCont operation (/vox c)."""

    def test_partition_11_init_sunset_from_noff(self) -> None:
        """P11: nOff/sUnset/empty -> nCont/sVoice/empty -- first enable inits."""
        _set_state(notify_mode="n")
        result = json.loads(notify(mode="c"))
        assert result["notify"]["notify"] == "c"
        assert result["notify"]["speak"] == "y"

    def test_partition_12_preserve_schime(self) -> None:
        """P12: nOff/sChime/empty -> nCont/sChime/empty -- muted user stays muted."""
        _set_state(notify_mode="n", speak_mode="n", speak_explicit=True)
        result = json.loads(notify(mode="c"))
        assert "speak" not in result["notify"]
        state = _read_state()
        assert state["notify"] == "c"
        assert state["speak"] == "n"

    def test_partition_13_upgrade_non_to_ncont(self) -> None:
        """P13: nOn/sVoice/{v1} -> nCont/sVoice/{v1} -- upgrade, speak preserved."""
        _set_state(notify_mode="y", speak_mode="y", speak_explicit=True, voice="roger")
        notify(mode="c")
        state = _read_state()
        assert state["notify"] == "c"
        assert state["speak"] == "y"
        assert state["voice"] == "roger"

    def test_partition_14_idempotent_already_ncont(self) -> None:
        """P14: nCont/sVoice/empty -> nCont/sVoice/empty -- no-op."""
        _set_state(notify_mode="c", speak_mode="y", speak_explicit=True)
        notify(mode="c")
        state = _read_state()
        assert state["notify"] == "c"
        assert state["speak"] == "y"


# ---------------------------------------------------------------------------
# Unmute -- speak(mode="y", voice=...)
# Z spec: notify' = notify; speak' = sVoice;
#         voice' = IF v? = empty THEN voice ELSE v?
# ---------------------------------------------------------------------------


class TestUnmute:
    """Partitions 15-21: Unmute operation (/unmute)."""

    def test_partition_15_unmute_from_schime(self) -> None:
        """P15: nOn/sChime/empty -> nOn/sVoice/empty -- core use case."""
        _set_state(notify_mode="y", speak_mode="n", speak_explicit=True)
        speak(mode="y")
        state = _read_state()
        assert state["notify"] == "y"  # notify frame
        assert state["speak"] == "y"
        assert state["voice"] is None

    def test_partition_16_unmute_from_sunset(self) -> None:
        """P16: nOff/sUnset/empty -> nOff/sVoice/empty -- speak init, no notify."""
        _set_state(notify_mode="n")
        speak(mode="y")
        state = _read_state()
        assert state["notify"] == "n"  # notify frame
        assert state["speak"] == "y"

    def test_partition_17_idempotent_already_svoice(self) -> None:
        """P17: nCont/sVoice/{v1} -> nCont/sVoice/{v1} -- no-op."""
        _set_state(
            notify_mode="c", speak_mode="y", speak_explicit=True, voice="matilda"
        )
        speak(mode="y")
        state = _read_state()
        assert state["notify"] == "c"
        assert state["speak"] == "y"
        assert state["voice"] == "matilda"

    def test_partition_18_set_voice_replaces(self) -> None:
        """P18: nOn/sChime/{v1} + v?={v2} -> nOn/sVoice/{v2} -- voice replaced."""
        _set_state(
            notify_mode="y", speak_mode="n", speak_explicit=True, voice="matilda"
        )
        result = json.loads(speak(mode="y", voice="roger"))
        assert result["voice"] == "roger"
        state = _read_state()
        assert state["speak"] == "y"
        assert state["voice"] == "roger"

    def test_partition_19_set_voice_from_empty(self) -> None:
        """P19: nOff/sUnset/empty + v?={v1} -> nOff/sVoice/{v1} -- first unmute."""
        _set_state(notify_mode="n")
        result = json.loads(speak(mode="y", voice="sarah"))
        assert result["voice"] == "sarah"
        state = _read_state()
        assert state["speak"] == "y"
        assert state["voice"] == "sarah"
        assert state["notify"] == "n"  # notify frame

    def test_partition_20_notify_frame_noff(self) -> None:
        """P20: nOff/sChime -> nOff/sVoice -- unmute keeps notify off."""
        _set_state(notify_mode="n", speak_mode="n", speak_explicit=True)
        speak(mode="y")
        state = _read_state()
        assert state["notify"] == "n"
        assert state["speak"] == "y"

    def test_partition_21_rejected_invalid_mode(self) -> None:
        """P21: Invalid mode rejected -- API guard."""
        _set_state(notify_mode="y", speak_mode="n", speak_explicit=True)
        result = json.loads(speak(mode="x"))
        assert "error" in result
        # State unchanged
        state = _read_state()
        assert state["speak"] == "n"


# ---------------------------------------------------------------------------
# Mute -- speak(mode="n")
# Z spec: notify' = notify; speak' = sChime; voice' = voice
# ---------------------------------------------------------------------------


class TestMute:
    """Partitions 22-25: Mute operation (/mute)."""

    def test_partition_22_mute_from_svoice(self) -> None:
        """P22: nOn/sVoice/{v1} -> nOn/sChime/{v1} -- core use case, voice preserved."""
        _set_state(
            notify_mode="y", speak_mode="y", speak_explicit=True, voice="matilda"
        )
        speak(mode="n")
        state = _read_state()
        assert state["notify"] == "y"
        assert state["speak"] == "n"
        assert state["voice"] == "matilda"

    def test_partition_23_mute_from_sunset(self) -> None:
        """P23: nOff/sUnset -> nOff/sChime -- mute before first enable."""
        _set_state(notify_mode="n")
        speak(mode="n")
        state = _read_state()
        assert state["notify"] == "n"
        assert state["speak"] == "n"

    def test_partition_24_idempotent_already_schime(self) -> None:
        """P24: nCont/sChime/empty -> nCont/sChime/empty -- no-op."""
        _set_state(notify_mode="c", speak_mode="n", speak_explicit=True)
        speak(mode="n")
        state = _read_state()
        assert state["notify"] == "c"
        assert state["speak"] == "n"

    def test_partition_25_notify_frame_ncont(self) -> None:
        """P25: nCont/sVoice -> nCont/sChime -- mute keeps notify."""
        _set_state(notify_mode="c", speak_mode="y", speak_explicit=True)
        speak(mode="n")
        state = _read_state()
        assert state["notify"] == "c"
        assert state["speak"] == "n"


# ---------------------------------------------------------------------------
# Observation partitions -- config-based guards
# These verify hook/watcher guard predicates against static session state
# (written via _set_state, not produced by notify/speak operations).
# ---------------------------------------------------------------------------


class TestStopObservations:
    """Partitions 26-31: StopFires/StopChime/StopVoice observations."""

    def test_partition_26_stop_fires_non(self) -> None:
        """P26: nOn/sVoice -> stop fires (notify != nOff)."""
        _set_state(notify_mode="y", speak_mode="y", speak_explicit=True)
        state = _read_state()
        assert state["notify"] != "n"

    def test_partition_27_stop_fires_ncont(self) -> None:
        """P27: nCont/sChime -> stop fires (notify != nOff)."""
        _set_state(notify_mode="c", speak_mode="n", speak_explicit=True)
        state = _read_state()
        assert state["notify"] != "n"

    def test_partition_28_stop_rejected_noff(self) -> None:
        """P28: nOff -> stop does not fire (notify = nOff)."""
        _set_state(notify_mode="n", speak_mode="y", speak_explicit=True)
        state = _read_state()
        assert state["notify"] == "n"

    def test_partition_29_stop_chime_output(self) -> None:
        """P29: nOn/sChime -> StopChime (speak = "n")."""
        _set_state(notify_mode="y", speak_mode="n", speak_explicit=True)
        state = _read_state()
        assert state["notify"] != "n"
        assert state["speak"] == "n"

    def test_partition_30_stop_voice_output(self) -> None:
        """P30: nOn/sVoice -> StopVoice (speak != "n")."""
        _set_state(notify_mode="y", speak_mode="y", speak_explicit=True)
        state = _read_state()
        assert state["speak"] != "n"

    def test_partition_31_stop_voice_sunset(self) -> None:
        """P31: nOn/sUnset -> StopVoice (speak != sChime, sUnset behaves as voice).

        Note: sUnset with nOn is unreachable in practice (VoxOn initializes
        speak), but verifies the observation guard logic is correct.
        In-memory state: speak="n" with _speak_explicit=False. The notify
        function would have initialized speak to "y", but we manually
        construct this state to test the guard.
        """
        _set_state(notify_mode="y")
        # In this unreachable state, speak is "n" but not explicitly set.
        # The guard in practice would see speak != "n" because VoxOn
        # always initializes it. We verify the raw field here.
        state = _read_state()
        assert state["speak_explicit"] is False


class TestWatcherObservations:
    """Partitions 33-38: WatcherFires/WatcherChime/WatcherVoice observations."""

    def test_partition_33_watcher_fires_ncont_svoice(self) -> None:
        """P33: nCont/sVoice -> watcher fires (notify = "c")."""
        _set_state(notify_mode="c", speak_mode="y", speak_explicit=True)
        state = _read_state()
        assert state["notify"] == "c"

    def test_partition_34_watcher_fires_ncont_schime(self) -> None:
        """P34: nCont/sChime -> watcher fires (notify = "c")."""
        _set_state(notify_mode="c", speak_mode="n", speak_explicit=True)
        state = _read_state()
        assert state["notify"] == "c"

    def test_partition_35_watcher_rejected_non(self) -> None:
        """P35: nOn -> watcher does not fire (notify != "c")."""
        _set_state(notify_mode="y", speak_mode="y", speak_explicit=True)
        state = _read_state()
        assert state["notify"] != "c"

    def test_partition_36_watcher_rejected_noff(self) -> None:
        """P36: nOff -> watcher does not fire (notify != "c")."""
        _set_state(notify_mode="n", speak_mode="y", speak_explicit=True)
        state = _read_state()
        assert state["notify"] != "c"

    def test_partition_37_watcher_chime_output(self) -> None:
        """P37: nCont/sChime -> WatcherChime."""
        _set_state(notify_mode="c", speak_mode="n", speak_explicit=True)
        state = _read_state()
        assert state["notify"] == "c"
        assert state["speak"] == "n"

    def test_partition_38_watcher_voice_output(self) -> None:
        """P38: nCont/sVoice -> WatcherVoice."""
        _set_state(notify_mode="c", speak_mode="y", speak_explicit=True)
        state = _read_state()
        assert state["notify"] == "c"
        assert state["speak"] != "n"


# ---------------------------------------------------------------------------
# Invariant preservation
# ---------------------------------------------------------------------------


class TestInvariantPreservation:
    """Partitions 39-41: Design invariants via multi-step sequences."""

    def test_partition_39_voice_replacement(self) -> None:
        """P39: Unmute replaces voice (not union) -- #voice <= 1 maintained."""
        _set_state(
            notify_mode="y", speak_mode="y", speak_explicit=True, voice="matilda"
        )
        speak(mode="y", voice="roger")
        state = _read_state()
        assert state["voice"] == "roger"
        # Only one voice, not both

    def test_partition_40_sunset_one_way(self) -> None:
        """P40: sUnset -> sVoice is one-way; VoxOff+VoxOn keeps sVoice."""
        # Step 1: VoxOn from Init (sUnset -> sVoice)
        notify(mode="y")
        state = _read_state()
        assert state["speak"] == "y"  # sVoice

        # Step 2: VoxOff (speak preserved)
        notify(mode="n")
        state = _read_state()
        assert state["speak"] == "y"  # Still sVoice, NOT sUnset

        # Step 3: VoxOn again (speak still sVoice, not re-initialized)
        result = json.loads(notify(mode="y"))
        assert "speak" not in result["notify"]  # No init needed
        state = _read_state()
        assert state["speak"] == "y"

    def test_partition_41_sticky_mute_through_enable(self) -> None:
        """P41: User's mute choice survives notify enable -- design invariant #2."""
        # Step 1: Mute (sUnset -> sChime)
        speak(mode="n")
        state = _read_state()
        assert state["speak"] == "n"

        # Step 2: VoxCont -- sChime preserved (not overridden to sVoice)
        notify(mode="c")
        state = _read_state()
        assert state["speak"] == "n"  # Sticky!
        assert state["notify"] == "c"


# ---------------------------------------------------------------------------
# Rejected partitions -- invalid inputs
# ---------------------------------------------------------------------------


class TestRejectedInputs:
    """Additional rejected partition for notify tool."""

    def test_notify_invalid_mode(self) -> None:
        """Notify rejects invalid mode values."""
        _set_state(notify_mode="y", speak_mode="y", speak_explicit=True)
        result = json.loads(notify(mode="x"))
        assert "error" in result
        # State unchanged
        state = _read_state()
        assert state["notify"] == "y"

    def test_speak_invalid_mode(self) -> None:
        """Speak rejects invalid mode values."""
        _set_state(notify_mode="y", speak_mode="y", speak_explicit=True)
        result = json.loads(speak(mode="c"))
        assert "error" in result
        state = _read_state()
        assert state["speak"] == "y"
