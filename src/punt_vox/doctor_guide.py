"""Doctor sub-check for the per-repo deposited guide against the packaged asset.

The deposited guide (``.punt-labs/vox/CLAUDE.md``) is @-imported by the repo's
own ``CLAUDE.md``, so it is what every agent working in the repo actually
reads. Nothing else surfaces whether it has fallen behind the packaged source;
a stale copy silently teaches agents tools that have been retired.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Self, final

from punt_vox.deposited_files import DepositedGuide
from punt_vox.dirs import find_repo_root
from punt_vox.doctor_result import CheckResult
from punt_vox.guide_stamp import GuideStampVerdict

if TYPE_CHECKING:
    from pathlib import Path

__all__ = ["GuideCheck"]

_REMEDIATION = " — run 'vox enable' (or mic:enablement action=enable) to refresh"


@final
class GuideCheck:
    """Verdict for the deposited guide: up to date, unstamped, or out of date.

    Stateless past the imports, like :class:`~punt_vox.doctor_mpv.MpvCheck`:
    the repo root and the packaged asset are located afresh on every run.
    """

    __slots__ = ()

    def __new__(cls) -> Self:
        return super().__new__(cls)

    def run(self) -> list[CheckResult]:
        """Return the guide row, or no row where the check does not apply.

        The check reads the source-hash stamp
        (:class:`~punt_vox.guide_stamp.GuideStamp`) embedded on deposit and
        compares it to a fresh hash of the packaged asset. Five verdicts,
        distinct on purpose:

        * outside a repo, or in a repo with no deposited guide (vox not
          enabled): no row -- not applicable, not a failure;
        * packaged asset unreadable: fail -- the vox install is broken, which
          re-``enable`` cannot fix, so it is reported whatever the deposit holds;
        * ``AGREE``: pass -- the deposited copy matches the packaged asset;
        * ``ABSENT_STAMP``: warn -- a copy from before this stamping existed
          (or one hand-edited beyond recognition). Unknown, not a false pass;
        * ``DIVERGE``: fail -- the deposit is provably behind the packaged
          source and re-``enable`` is needed.
        """
        deposited = self._deposit_path()
        if deposited is None:
            return []
        # ``verify`` hashes the packaged asset before it reads the deposit, so a
        # missing or unreadable asset raises here whatever state the deposit is
        # in, instead of a verdict whose remedy fails on the same broken install.
        try:
            verdict = DepositedGuide(deposited, deposited.parents[2]).verify()
        except OSError as exc:
            return [self._unreadable_asset(exc)]
        return [self._result(verdict)]

    @staticmethod
    def _deposit_path() -> Path | None:
        """Return the repo's deposited guide, or ``None`` where there is none.

        ``None`` is the not-applicable contract: outside a repo, vox not enabled
        here, or a deposit the process cannot even stat. ``is_file`` swallows
        ENOENT / ENOTDIR / EBADF / ELOOP but propagates OSError variants like
        PermissionError (an unreadable parent directory); a check whose job is
        to say the deposit is in a bad state must not crash the whole
        ``vox doctor`` run when it hits one.
        """
        root = find_repo_root()
        if root is None:
            return None
        deposited = root / ".punt-labs" / "vox" / "CLAUDE.md"
        try:
            return deposited if deposited.is_file() else None
        except OSError:
            return None

    @staticmethod
    def _unreadable_asset(exc: OSError) -> CheckResult:
        """Return the fail row for a broken install, naming no host path.

        ``str(exc)`` carries the asset's absolute install path; doctor rows
        report verdicts, never host locations, so only ``strerror`` is kept.
        """
        reason = (exc.strerror or type(exc).__name__).lower()
        return CheckResult.fail(
            f"deposited guide: packaged asset unreadable ({reason})"
            " — reinstall punt-vox"
        )

    @staticmethod
    def _result(verdict: GuideStampVerdict) -> CheckResult:
        """Turn a :class:`GuideStampVerdict` into the matching check line."""
        if verdict is GuideStampVerdict.AGREE:
            return CheckResult.ok("deposited guide: up to date")
        if verdict is GuideStampVerdict.DIVERGE:
            return CheckResult.fail(f"deposited guide: out of date{_REMEDIATION}")
        return CheckResult.warn(
            f"deposited guide: unstamped, freshness unknown{_REMEDIATION}"
        )
