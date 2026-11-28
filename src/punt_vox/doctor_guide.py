"""Doctor sub-check for the per-repo deposited guide against the packaged asset.

The deposited guide (``.punt-labs/vox/CLAUDE.md``) is @-imported by the repo's
own ``CLAUDE.md``, so it is what every agent working in the repo actually
reads. Nothing else surfaces whether it has fallen behind the packaged source;
a stale copy silently teaches agents tools that have been retired.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Self, final

from punt_vox.dirs import find_repo_root
from punt_vox.doctor_result import CheckResult
from punt_vox.enablement_state import EffectiveEnablement, EnablementState
from punt_vox.guide_stamp import GuideStamp, GuideStampVerdict

if TYPE_CHECKING:
    from pathlib import Path

__all__ = ["GuideCheck"]

_REMEDIATION = (
    " — run 'vox enable --refresh' (or 'vox enable --scope project --refresh') "
    "to refresh"
)


@final
class GuideCheck:
    """Verdict for the deposited guide: up to date, unstamped, or out of date.

    Stateless past the imports, like :class:`~punt_vox.doctor_mpv.MpvCheck`:
    the repo root and the packaged asset are located afresh on every run.
    """

    __slots__ = ()

    def __new__(cls) -> Self:
        """Create a guide check."""
        return super().__new__(cls)

    def run(self) -> list[CheckResult]:
        """Return enablement and guide rows, or no rows outside a git repo."""
        root = find_repo_root()
        if root is None:
            return []
        state = EnablementState.resolve(root)
        results: list[CheckResult] = [self._enablement_result(state)]
        deposited = self._deposit_path(root, state.status)
        if deposited is None:
            return results
        try:
            verdict = GuideStamp.for_packaged_asset().verify(deposited)
        except OSError as exc:
            results.append(self._unreadable_asset(exc))
            return results
        results.append(self._result(verdict))
        return results

    @staticmethod
    def _enablement_result(state: EffectiveEnablement) -> CheckResult:
        """Report the effective enablement state and deciding file (TED-24)."""
        on_off = "on" if state.on else "off"
        detail = (
            f"{state.status} ({state.deciding_file})"
            if state.deciding_file is not None
            else state.status
        )
        if state.shared_guide_loads and not state.on:
            detail += "; shared guide still loads"
        return CheckResult.ok(f"enablement: {on_off} — {detail}")

    @staticmethod
    def _deposit_path(root: Path, status: str) -> Path | None:
        """Return the active scope's deposited guide, or ``None`` if absent."""
        candidates = (
            (root / ".punt-labs" / "local" / "vox" / "CLAUDE.md",)
            if status == "enabled_local"
            else (
                root / ".punt-labs" / "vox" / "CLAUDE.md",
                root / ".punt-labs" / "local" / "vox" / "CLAUDE.md",
            )
        )
        for deposited in candidates:
            try:
                if deposited.is_file():
                    return deposited
            except OSError:
                continue
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
