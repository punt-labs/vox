"""Tests for punt_vox.doctor_guide.GuideCheck."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from punt_vox.doctor_guide import GuideCheck
from punt_vox.guide_stamp import GuideStamp


class TestGuideCheck:
    """The staleness check compares the deposited guide's source-hash stamp
    to a fresh hash of the packaged asset. The four verdicts are distinct
    on purpose -- absent stamp must not read as a false pass, and being
    outside a repo (or in a repo with vox not enabled) is not applicable,
    not a failure.
    """

    def _make_repo(self, tmp_path: Path) -> Path:
        (tmp_path / ".git").mkdir()
        (tmp_path / ".punt-labs" / "vox").mkdir(parents=True)
        return tmp_path

    def _packaged(self, tmp_path: Path) -> Path:
        asset = tmp_path / "packaged.md"
        asset.write_text("packaged body\n", encoding="utf-8")
        return asset

    def test_no_repo_is_not_applicable(self, tmp_path: Path) -> None:
        with patch("punt_vox.doctor_guide.find_repo_root", return_value=None):
            results = GuideCheck().run()
        assert results == []

    def test_repo_without_deposited_guide_is_not_applicable(
        self, tmp_path: Path
    ) -> None:
        repo = self._make_repo(tmp_path)
        with patch("punt_vox.doctor_guide.find_repo_root", return_value=repo):
            results = GuideCheck().run()
        # vox not enabled here -- no deposited guide -- reported as N/A.
        assert results == []

    def test_agree_passes(self, tmp_path: Path) -> None:
        repo = self._make_repo(tmp_path)
        packaged = self._packaged(tmp_path)
        stamp = GuideStamp(packaged)
        deposited = repo / ".punt-labs" / "vox" / "CLAUDE.md"
        deposited.write_text(stamp.stamped("packaged body\n"), encoding="utf-8")

        with (
            patch("punt_vox.doctor_guide.find_repo_root", return_value=repo),
            patch.object(GuideStamp, "for_packaged_asset", return_value=stamp),
        ):
            results = GuideCheck().run()
        assert len(results) == 1
        assert results[0].passed is True
        assert "up to date" in results[0].message

    def test_diverge_fails(self, tmp_path: Path) -> None:
        repo = self._make_repo(tmp_path)
        packaged = self._packaged(tmp_path)
        stamp = GuideStamp(packaged)
        deposited = repo / ".punt-labs" / "vox" / "CLAUDE.md"
        deposited.write_text(stamp.stamped("packaged body\n"), encoding="utf-8")
        # Packaged asset drifts after the deposit was stamped.
        packaged.write_text("new packaged body\n", encoding="utf-8")

        with (
            patch("punt_vox.doctor_guide.find_repo_root", return_value=repo),
            patch.object(GuideStamp, "for_packaged_asset", return_value=stamp),
        ):
            results = GuideCheck().run()
        assert len(results) == 1
        assert results[0].passed is False
        assert results[0].symbol == "✗"
        assert "out of date" in results[0].message
        assert "vox enable" in results[0].message

    def test_absent_stamp_warns_and_does_not_pass(self, tmp_path: Path) -> None:
        repo = self._make_repo(tmp_path)
        packaged = self._packaged(tmp_path)
        stamp = GuideStamp(packaged)
        deposited = repo / ".punt-labs" / "vox" / "CLAUDE.md"
        # A guide deposited before this stamping existed -- no HTML-comment tail.
        deposited.write_text("unstamped body\n", encoding="utf-8")

        with (
            patch("punt_vox.doctor_guide.find_repo_root", return_value=repo),
            patch.object(GuideStamp, "for_packaged_asset", return_value=stamp),
        ):
            results = GuideCheck().run()
        assert len(results) == 1
        assert results[0].passed is False
        assert results[0].symbol == "⚠"
        assert "unstamped" in results[0].message
        assert "vox enable" in results[0].message

    def test_unreadable_deposit_falls_to_absent_stamp(self, tmp_path: Path) -> None:
        # A deposit whose bytes cannot be decoded (a corrupt or truncated
        # write) must reach the ABSENT_STAMP verdict rather than crashing the
        # diagnostic that exists to say the deposit is in a bad state.
        repo = self._make_repo(tmp_path)
        packaged = self._packaged(tmp_path)
        stamp = GuideStamp(packaged)
        deposited = repo / ".punt-labs" / "vox" / "CLAUDE.md"
        deposited.write_bytes(b"\xff\xfe undecodable \x80\x81")

        with (
            patch("punt_vox.doctor_guide.find_repo_root", return_value=repo),
            patch.object(GuideStamp, "for_packaged_asset", return_value=stamp),
        ):
            results = GuideCheck().run()
        assert len(results) == 1
        assert results[0].symbol == "⚠"
        assert "unstamped" in results[0].message

    def test_is_file_oserror_reports_not_applicable(self, tmp_path: Path) -> None:
        # A rare failure of ``is_file`` (permission-denied on the parent, a
        # racing removal returning an odd errno) must not tear down the whole
        # ``vox doctor`` run -- treat "cannot see the deposit" as N/A, same
        # as "vox not enabled here".
        repo = self._make_repo(tmp_path)

        def raise_permission(*_args: object, **_kwargs: object) -> bool:
            raise PermissionError("simulated parent-dir permission denied")

        with (
            patch("punt_vox.doctor_guide.find_repo_root", return_value=repo),
            patch.object(Path, "is_file", raise_permission),
        ):
            results = GuideCheck().run()
        assert results == []
