"""Present Vox diagnostic results on the CLI."""

from __future__ import annotations

from typing import TYPE_CHECKING, final

import typer

from punt_vox.doctor import DoctorCheck
from punt_vox.doctor_result import CheckResults

if TYPE_CHECKING:
    from punt_vox.client_sync import VoxClientSync
    from punt_vox.output_formatter import OutputFormatter


@final
class DoctorCli:
    """Render a doctor run and map failed checks to a CLI exit."""

    @staticmethod
    def emit(formatter: OutputFormatter, client: VoxClientSync) -> None:
        """Emit diagnostics in the selected output mode.

        Raises:
            typer.Exit: If any required check fails.
        """
        results = CheckResults(DoctorCheck(client=client).run_all())
        payload, text = results.format()
        formatter.emit(payload, text)
        if payload.get("failed", 0):
            raise typer.Exit(code=1)
