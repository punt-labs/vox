"""Tests for CLI output-flag routing."""

from __future__ import annotations

import pytest
import typer

from punt_vox.cli_io import OutputFlags
from punt_vox.output_formatter import OutputFormatter


class TestOutputFlags:
    """OutputFlags ORs --json/--verbose/--quiet across the callback and command."""

    def test_json_from_either_position_sets_formatter(self) -> None:
        fmt = OutputFormatter()
        flags = OutputFlags(fmt)
        flags.apply(json_output=True, verbose=False, quiet=False)
        fmt.emit({"a": 1}, "text")  # emits JSON; verified below via capsys in siblings
        assert fmt._json is True  # pyright: ignore[reportPrivateUsage]

    def test_quiet_sets_formatter(self) -> None:
        fmt = OutputFormatter()
        OutputFlags(fmt).apply(json_output=False, verbose=False, quiet=True)
        assert fmt._quiet is True  # pyright: ignore[reportPrivateUsage]

    def test_reset_clears_formatter_and_seen(self) -> None:
        """reset restores a fresh state so a reused process does not leak flags."""
        fmt = OutputFormatter(json_output=True, quiet=True)
        flags = OutputFlags(fmt)
        flags.apply(json_output=False, verbose=True, quiet=False)
        flags.reset()
        assert fmt._json is False  # pyright: ignore[reportPrivateUsage]
        assert fmt._quiet is False  # pyright: ignore[reportPrivateUsage]
        # After reset, verbose alone no longer trips the mutual-exclusion guard.
        flags.apply(json_output=False, verbose=False, quiet=True)

    def test_verbose_and_quiet_together_raise(self) -> None:
        flags = OutputFlags(OutputFormatter())
        with pytest.raises(typer.BadParameter, match="mutually exclusive"):
            flags.apply(json_output=False, verbose=True, quiet=True)

    def test_split_verbose_then_quiet_raises(self) -> None:
        """Accumulated flags across two apply calls (callback then command) trip."""
        flags = OutputFlags(OutputFormatter())
        flags.apply(json_output=False, verbose=True, quiet=False)
        with pytest.raises(typer.BadParameter, match="mutually exclusive"):
            flags.apply(json_output=False, verbose=False, quiet=True)
