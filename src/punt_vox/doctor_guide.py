"""Doctor sub-check for the per-repo deposited guide against the packaged asset.

The deposited guide (``.punt-labs/vox/CLAUDE.md``) is @-imported by the repo's
own ``CLAUDE.md``, so it is what every agent working in the repo actually
reads. Nothing else surfaces whether it has fallen behind the packaged source;
a stale copy silently teaches agents tools that have been retired.
"""

from __future__ import annotations

from typing import Self, final

from punt_vox.dirs import find_repo_root
from punt_vox.doctor_result import CheckResult
from punt_vox.guide_stamp import GuideStamp, GuideStampVerdict

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
        compares it to a fresh hash of the packaged asset. Four verdicts,
        distinct on purpose:

        * outside a repo, or in a repo with no deposited guide (vox not
          enabled): no row -- not applicable, not a failure;
        * ``AGREE``: pass -- the deposited copy matches the packaged asset;
        * ``ABSENT_STAMP``: warn -- a copy from before this stamping existed
          (or one hand-edited beyond recognition). Unknown, not a false pass;
        * ``DIVERGE``: fail -- the deposit is provably behind the packaged
          source and re-``enable`` is needed.
        """
        root = find_repo_root()
        if root is None:
            return []
        deposited = root / ".punt-labs" / "vox" / "CLAUDE.md"
        # ``is_file`` swallows ENOENT / ENOTDIR / EBADF / ELOOP but propagates
        # OSError variants like PermissionError (e.g. an unreadable parent
        # directory) -- the same class of failure the deposit-read guard in
        # ``GuideStamp.read`` handles. A check whose job is to say the deposit
        # is in a bad state must not crash the whole ``vox doctor`` run when
        # it hits one; treat "cannot see the deposit" as not-applicable, the
        # same as "vox not enabled here".
        try:
            deposited_present = deposited.is_file()
        except OSError:
            return []
        if not deposited_present:
            return []
        verdict = GuideStamp.for_packaged_asset().verify(deposited)
        return [self._result(verdict)]

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
