"""Tests for punt_vox.service.process_identity -- one process, named by ps."""

from __future__ import annotations

import pytest

from punt_vox.service.process_identity import ProcessIdentity

_LSTART = "Tue Nov  4 15:53:01 2026"


def test_from_ps_splits_start_time_and_command() -> None:
    seen = ProcessIdentity.from_ps(f"{_LSTART} /usr/local/bin/voxd --port 8421")
    assert seen == ProcessIdentity(_LSTART, "/usr/local/bin/voxd --port 8421")


def test_from_ps_rejects_a_line_with_no_command() -> None:
    with pytest.raises(ValueError, match="unparseable ps line"):
        ProcessIdentity.from_ps(_LSTART)


@pytest.mark.parametrize(
    ("command", "voxd"),
    [
        ("/usr/local/bin/voxd --port 8421", True),
        ("/home/u/.local/share/uv/tools/punt-vox/bin/python /home/u/bin/voxd", True),
        ("/usr/bin/voxdx", False),
        ("vox serve", False),
        ("nginx: master process", False),
    ],
)
def test_is_voxd_matches_the_word(command: str, voxd: bool) -> None:
    assert ProcessIdentity(_LSTART, command).is_voxd() is voxd


def test_a_reused_pid_is_a_different_identity() -> None:
    first = ProcessIdentity(_LSTART, "/bin/voxd")
    later = ProcessIdentity("Tue Nov  4 16:00:00 2026", "/bin/voxd")
    assert first != later
