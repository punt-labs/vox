"""Tests for the server-authored DJ music-panel phrasing."""

from __future__ import annotations

from collections.abc import Sequence

import pytest

from punt_vox.music_phrases import (
    GENERATING_NO_STYLE,
    GENERATING_WITH_STYLE,
    REPLAY_RADIO,
    REPLAY_WITH_NAME,
    SKIP,
    STOPPED,
    MusicMarquee,
)

ALL_POOLS: tuple[tuple[str, ...], ...] = (
    GENERATING_WITH_STYLE,
    GENERATING_NO_STYLE,
    STOPPED,
    REPLAY_WITH_NAME,
    REPLAY_RADIO,
    SKIP,
)

# The panel prefixes the line with "♪ " (marker + space) before display.
_PREFIX = "♪ "
_MAX_COLS = 80


def _first(pool: Sequence[str]) -> str:
    """A deterministic chooser: always the pool's first phrase."""
    return pool[0]


class TestPools:
    """Every pool is populated and fits the prefixed panel budget."""

    @pytest.mark.parametrize("pool", ALL_POOLS)
    def test_pool_is_non_empty(self, pool: tuple[str, ...]) -> None:
        assert pool

    @pytest.mark.parametrize("pool", ALL_POOLS)
    def test_templates_fit_prefixed_budget(self, pool: tuple[str, ...]) -> None:
        for phrase in pool:
            assert len(_PREFIX + phrase) <= _MAX_COLS

    def test_interpolated_style_fits_budget(self) -> None:
        style = "progressive melodic techno"  # a generously long real style
        for phrase in GENERATING_WITH_STYLE:
            line = _PREFIX + phrase.format(style=style)
            assert len(line) <= _MAX_COLS

    def test_interpolated_name_fits_budget(self) -> None:
        name = "midnight-crate-sessions-vol-3"  # a generously long real handle
        for phrase in REPLAY_WITH_NAME:
            line = _PREFIX + phrase.format(name=name)
            assert len(line) <= _MAX_COLS


class TestGenerating:
    """``generating`` fills in a style, or falls back to the no-style pool."""

    def test_with_style_interpolates(self) -> None:
        marquee = MusicMarquee(chooser=_first)
        assert marquee.generating("techno") == "dropping a techno beat"

    def test_with_style_member_of_pool(self) -> None:
        marquee = MusicMarquee()
        line = marquee.generating("house")
        assert line in {p.format(style="house") for p in GENERATING_WITH_STYLE}

    def test_no_style_uses_no_style_pool(self) -> None:
        marquee = MusicMarquee()
        assert marquee.generating(None) in GENERATING_NO_STYLE

    def test_no_style_has_no_placeholder(self) -> None:
        marquee = MusicMarquee(chooser=_first)
        assert "{" not in marquee.generating(None)


class TestStopped:
    def test_member_of_pool(self) -> None:
        marquee = MusicMarquee()
        assert marquee.stopped() in STOPPED


class TestReplay:
    """``replay`` names a track, or falls back to the radio (no-name) pool."""

    def test_with_name_interpolates(self) -> None:
        marquee = MusicMarquee(chooser=_first)
        assert marquee.replay("deep cuts") == "now spinning: deep cuts"

    def test_with_name_member_of_pool(self) -> None:
        marquee = MusicMarquee()
        line = marquee.replay("deep cuts")
        assert line in {p.format(name="deep cuts") for p in REPLAY_WITH_NAME}

    def test_no_name_uses_radio_pool(self) -> None:
        marquee = MusicMarquee()
        assert marquee.replay(None) in REPLAY_RADIO


class TestSkip:
    def test_member_of_pool(self) -> None:
        marquee = MusicMarquee()
        assert marquee.skip() in SKIP


class TestChooserInjection:
    """The injected chooser makes selection deterministic for tests."""

    def test_last_element_chooser(self) -> None:
        marquee = MusicMarquee(chooser=lambda pool: pool[-1])
        assert marquee.stopped() == STOPPED[-1]
        assert marquee.skip() == SKIP[-1]
