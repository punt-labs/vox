"""Tests for the shared CLI synthesis options."""

from __future__ import annotations

import subprocess
import sys
from typing import get_type_hints

from punt_vox.types_cli_synthesis import SynthesisOptions


def test_synthesis_options_import_without_typer() -> None:
    code = (
        "import sys; import punt_vox.types_cli_synthesis; "
        "assert 'typer' not in sys.modules"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        check=False,
        timeout=5,
    )
    assert result.returncode == 0, result.stderr


def test_synthesis_options_require_all_shared_settings() -> None:
    expected = {
        "voice": str | None,
        "language": str | None,
        "rate": int,
        "provider": str | None,
        "model": str | None,
        "stability": float | None,
        "similarity": float | None,
        "style": float | None,
    }
    assert SynthesisOptions.__required_keys__ == expected.keys()
    assert get_type_hints(SynthesisOptions) == expected
