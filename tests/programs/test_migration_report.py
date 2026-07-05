"""Tests for the migration outcome value object."""

from __future__ import annotations

from punt_vox.voxd.programs.migration_report import MigrationReport


def test_empty_report_summarises_as_nothing() -> None:
    """A report with no names is empty and summarises accordingly."""
    report = MigrationReport(names=(), parts=0)

    assert report.is_empty
    assert report.programs == 0
    assert report.summary() == "nothing to migrate"


def test_report_counts_programs_and_lists_names() -> None:
    """A non-empty report reports its program count and names in the summary."""
    report = MigrationReport(names=("ambient_techno", "jazz_lounge"), parts=5)

    assert not report.is_empty
    assert report.programs == 2
    text = report.summary()
    assert "5 track(s)" in text
    assert "2 program(s)" in text
    assert "ambient_techno" in text
    assert "jazz_lounge" in text
