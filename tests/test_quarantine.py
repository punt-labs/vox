"""Require a tracked reason for every quarantine, including inherited marks."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from conftest import pytest_collection_modifyitems


@pytest.mark.parametrize(
    "reason", ["PL-1234: owner Jim", "https://linear.app/punt-labs/issue/PL-1234/flaky"]
)
def test_accepts_tracked_reason(reason: str) -> None:
    item = MagicMock(spec=pytest.Item)
    item.iter_markers.return_value = [pytest.mark.quarantine(reason=reason).mark]
    pytest_collection_modifyitems([item])


@pytest.mark.parametrize(
    "kwargs",
    [
        {},
        {"reason": "flaky"},
        {"reason": 123},
        {"reason": "PL-nope"},
        {"reason": "https://linear.app/not-a-ticket"},
    ],
)
def test_rejects_missing_or_untracked_reason(kwargs: dict[str, object]) -> None:
    item = MagicMock(spec=pytest.Item)
    item.nodeid = "test_example"
    item.iter_markers.return_value = [pytest.mark.quarantine(**kwargs).mark]
    with pytest.raises(pytest.UsageError, match="test_example: quarantine requires"):
        pytest_collection_modifyitems([item])


def test_accepts_unmarked_tests() -> None:
    item = MagicMock(spec=pytest.Item)
    item.iter_markers.return_value = []
    pytest_collection_modifyitems([item])


@pytest.mark.parametrize(
    "target", ["test-e2e", "test-roundtrip", "test-roundtrip-live"]
)
def test_explicit_make_tiers_exclude_quarantine(target: str) -> None:
    makefile = Path(__file__).resolve().parents[1] / "Makefile"
    recipe = makefile.read_text().split(f"\n{target}:", 1)[1].split("\n\n", 1)[0]
    assert "not quarantine" in recipe
