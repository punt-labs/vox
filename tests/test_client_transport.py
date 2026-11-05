"""Tests for punt_vox.client_transport.VoxdTransport's connect boundary."""

from __future__ import annotations

import tomllib
from pathlib import Path

_PYPROJECT = Path(__file__).resolve().parents[1] / "pyproject.toml"


def test_the_websockets_floor_accepts_the_proxy_keyword_connect_passes() -> None:
    """connect() passes ``proxy=None`` so the token never crosses a proxy.

    websockets accepts ``proxy=`` only from 15.0; on 14.x the call raises
    ``TypeError`` before connecting, which no connect handler classifies.
    """
    project = tomllib.loads(_PYPROJECT.read_text(encoding="utf-8"))["project"]
    floors = [d for d in project["dependencies"] if d.startswith("websockets")]
    assert floors == ["websockets>=15.0"]
