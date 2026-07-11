"""Unit tests for the audit log and its structural waiver scoping."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.oo_ratchet.audit import AuditError, AuditLog


def _relax(audit: AuditLog, path: str, metric: str) -> None:
    audit.append(
        files_scored=1,
        files_improved=0,
        files_regressed=1,
        verdict="relaxed",
        deltas={path: {metric: [1.0, 3.0]}},
        source="vox-x #1",
        commit="abc123",
        reason="accepted",
    )


class TestRelaxationsSince:
    """Waiver scoping compares audit entries structurally, not by raw text."""

    def test_reformatted_base_line_still_matches(self, tmp_path: Path) -> None:
        audit = AuditLog(tmp_path)
        _relax(audit, "sub/w.py", "max_complexity")
        # The base log holds the SAME entry but reformatted: keys sorted, spaces
        # after separators -- a different raw string, same semantics.
        in_tree = (tmp_path / AuditLog.FILENAME).read_text().splitlines()[0]
        reformatted = json.dumps(
            json.loads(in_tree), sort_keys=True, separators=(", ", ": ")
        )
        assert reformatted != in_tree  # genuinely different bytes
        assert audit.relaxations_since(reformatted) == frozenset()

    def test_absent_base_leaves_relaxation_waivable(self, tmp_path: Path) -> None:
        audit = AuditLog(tmp_path)
        _relax(audit, "sub/w.py", "max_complexity")
        assert audit.relaxations_since(None) == {("sub/w.py", "max_complexity")}

    def test_different_base_entry_leaves_relaxation_waivable(
        self, tmp_path: Path
    ) -> None:
        audit = AuditLog(tmp_path)
        _relax(audit, "sub/w.py", "max_complexity")
        other = json.dumps({"verdict": "relaxed", "deltas": {"sub/other.py": {}}})
        assert audit.relaxations_since(other) == {("sub/w.py", "max_complexity")}


class TestMalformedAudit:
    """A malformed audit line is a controlled AuditError, not a traceback."""

    def test_bad_working_tree_line_raises_audit_error(self, tmp_path: Path) -> None:
        (tmp_path / AuditLog.FILENAME).write_text("<<<<<<< conflict marker\n")
        with pytest.raises(AuditError):
            AuditLog(tmp_path).relaxations_since(None)

    def test_bad_base_line_raises_audit_error(self, tmp_path: Path) -> None:
        audit = AuditLog(tmp_path)
        _relax(audit, "sub/w.py", "max_complexity")
        with pytest.raises(AuditError):
            audit.relaxations_since("{ not json")
