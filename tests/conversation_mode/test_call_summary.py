"""Tests for :class:`CallSummaryStore`: where call summaries live and how long."""

from __future__ import annotations

import logging
import re
import stat
from pathlib import Path
from unittest.mock import patch

import pytest

from punt_vox.voxd.conversation_mode import call_summary as summary_module
from punt_vox.voxd.conversation_mode.call_summary import (
    SUMMARY_PROMPT,
    CallSummaryStore,
)


def test_a_summary_lands_at_call_id_summary_md(tmp_path: Path) -> None:
    path = CallSummaryStore(tmp_path).write("20261105T140302Z-a1b2c3", "## Decisions")
    assert path == tmp_path / "20261105T140302Z-a1b2c3-summary.md"


def test_the_document_carries_a_header_and_the_agent_text(tmp_path: Path) -> None:
    path = CallSummaryStore(tmp_path).write("id1", "## Decisions Made\n- None.\n")
    assert path.read_text() == (
        "# Call Summary\n**Call:** id1\n\n## Decisions Made\n- None.\n"
    )


def test_a_secret_shaped_line_in_a_summary_is_stored_redacted(
    tmp_path: Path,
) -> None:
    """A summary outlives the call on disk; a secret the agent quoted must not."""
    secret = "sk-ant-api03-abcdefghijklmnopqrstuvwxyz0123456789"
    body = f"## Decisions Made\n- Rotate the key {secret}\n- DB_PASSWORD=hunter2"
    stored = CallSummaryStore(tmp_path).write("c1", body).read_text()
    assert (secret in stored, "hunter2" in stored, "[redacted]" in stored) == (
        False,
        False,
        True,
    )


def test_a_summary_keeps_the_file_paths_it_names(tmp_path: Path) -> None:
    path = "src/punt_vox/voxd/conversation_mode/pi_call_agent.py"
    body = f"## Action Items\n- Edit {path}"
    assert path in CallSummaryStore(tmp_path).write("c1", body).read_text()


def test_a_summary_is_private(tmp_path: Path) -> None:
    root = tmp_path / "calls"
    path = CallSummaryStore(root).write("id1", "text")
    assert (stat.S_IMODE(path.stat().st_mode), stat.S_IMODE(root.stat().st_mode)) == (
        0o600,
        0o700,
    )


def test_only_the_newest_summaries_are_kept(tmp_path: Path) -> None:
    store = CallSummaryStore(tmp_path, keep=2)
    for call_id in ("20260101T000000Z-a", "20260102T000000Z-b", "20260103T000000Z-c"):
        store.write(call_id, "text")
    assert sorted(p.name for p in tmp_path.iterdir()) == [
        "20260102T000000Z-b-summary.md",
        "20260103T000000Z-c-summary.md",
    ]


def test_pruning_ignores_unrelated_files(tmp_path: Path) -> None:
    (tmp_path / "notes.txt").write_text("mine")
    CallSummaryStore(tmp_path, keep=1).write("id1", "text")
    assert (tmp_path / "notes.txt").exists()


def test_a_prune_failure_is_logged_and_the_summary_still_lands(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    store = CallSummaryStore(tmp_path, keep=1)
    store.write("20261105T000001Z-aaaaaa", "old")
    with (
        patch.object(Path, "unlink", side_effect=PermissionError("read-only")),
        caplog.at_level(logging.WARNING, logger=summary_module.__name__),
    ):
        path = store.write("20261105T000002Z-bbbbbb", "new")
    assert (path.read_text().endswith("new\n"), "read-only" in caplog.text) == (
        True,
        True,
    )


def test_keep_must_be_positive(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="at least one"):
        CallSummaryStore(tmp_path, keep=0)


def test_call_ids_are_timestamped_and_unique() -> None:
    first, second = CallSummaryStore.new_call_id(), CallSummaryStore.new_call_id()
    assert re.fullmatch(r"\d{8}T\d{6}Z-[0-9a-f]{6}", first)
    assert first != second


@pytest.mark.parametrize(
    "section",
    [
        "## Decisions Made",
        "## Action Items",
        "## Context Gathered",
        "## Beads Touched",
        "## Notes / Open Questions",
    ],
)
def test_the_prompt_asks_for_every_section(section: str) -> None:
    assert section in SUMMARY_PROMPT
