"""Tests for the tool-owned files ``enable`` deposits (``DepositedGuide``)."""

from __future__ import annotations

from pathlib import Path

from punt_vox.deposited_files import DepositedGuide
from punt_vox.guide_stamp import GuideStamp, GuideStampVerdict

_ASSETS = Path(__file__).resolve().parents[1] / "src" / "punt_vox" / "assets"
_SHIPPED_GUIDE = _ASSETS / "agent-guide.md"


def _guide(tmp_path: Path) -> DepositedGuide:
    return DepositedGuide(tmp_path / ".punt-labs" / "vox" / "CLAUDE.md", tmp_path)


def test_deposit_copies_the_shipped_agent_guide(tmp_path: Path) -> None:
    guide = _guide(tmp_path)
    guide.deposit()
    body = guide.path.read_text(encoding="utf-8")
    assert body.startswith(_SHIPPED_GUIDE.read_text(encoding="utf-8"))


def test_deposit_carries_the_shipped_guide_stamp(tmp_path: Path) -> None:
    guide = _guide(tmp_path)
    guide.deposit()
    digest = GuideStamp(_SHIPPED_GUIDE).packaged_hash()
    assert GuideStamp(_SHIPPED_GUIDE).read(guide.path) == digest


def test_fresh_deposit_verifies(tmp_path: Path) -> None:
    guide = _guide(tmp_path)
    guide.deposit()
    assert guide.verify() is GuideStampVerdict.AGREE


def test_hand_edited_deposit_is_unstamped(tmp_path: Path) -> None:
    guide = _guide(tmp_path)
    guide.path.parent.mkdir(parents=True)
    guide.path.write_text("hand-written guide\n", encoding="utf-8")
    assert guide.verify() is GuideStampVerdict.ABSENT_STAMP
