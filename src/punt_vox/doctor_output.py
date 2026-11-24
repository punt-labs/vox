"""Format the daemon's album output diagnostics for doctor."""

from __future__ import annotations

from typing import TYPE_CHECKING, Self, final

from punt_vox.doctor_result import OPTIONAL, CheckResult

if TYPE_CHECKING:
    from punt_vox.types_health import HealthStatus

__all__ = ["OutputDirCheck"]

_CREATED_BY = "created when 'vox music on' saves its first album"
_OUTPUT_VERDICTS = {
    "absent": CheckResult.warn(f"output: absent — {_CREATED_BY}"),
    "not_writable": CheckResult.fail("output: not writable — check permissions"),
    "writable": CheckResult.ok("output: writable"),
}


@final
class OutputDirCheck:
    """Render path-free verdicts from the running daemon."""

    __slots__ = ("_health",)
    _health: HealthStatus

    def __new__(cls, health: HealthStatus) -> Self:
        """Create a report from the daemon's health snapshot."""
        self = super().__new__(cls)
        self._health = health
        return self

    def run(self) -> list[CheckResult]:
        """Return the output verdict and any relevant missing music-dir warning."""
        rows: list[CheckResult] = []
        if self._health.music_dir_missing:
            rows.append(CheckResult.warn(f"output music dir: absent — {_CREATED_BY}"))
        rows.append(_OUTPUT_VERDICTS.get(self._health.output_dir) or self.unreported())
        return rows

    @staticmethod
    def unreported() -> CheckResult:
        """Show that no daemon verdict was available; never guess from the CLI."""
        return CheckResult.of(
            OPTIONAL,
            "output: not checked — voxd did not report a verdict",
            required=False,
        )
