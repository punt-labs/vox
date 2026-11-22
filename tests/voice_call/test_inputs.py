from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import pytest

from punt_vox.voxd.voice_call.inputs import ScriptInput, ScriptTurn

if TYPE_CHECKING:
    from pathlib import Path


async def test_script_speaks_padded_frames_then_silence() -> None:
    script = ScriptInput((ScriptTurn(b"\x01" * 700, 0.04),))
    stream = script.chunks()
    frames = [await anext(stream) for _ in range(5)]
    assert [len(f) for f in frames] == [640] * 5
    assert frames[0] == b"\x01" * 640
    assert frames[1] == b"\x01" * 60 + bytes(580)
    assert frames[2:] == [bytes(640)] * 3


async def test_script_is_paced_in_real_time() -> None:
    stream = ScriptInput((ScriptTurn(bytes(640 * 10), 0.0),)).chunks()
    loop = asyncio.get_running_loop()
    started = loop.time()
    for _ in range(10):
        await anext(stream)
    assert loop.time() - started >= 0.18


def test_from_wire_reads_turn_files(tmp_path: Path) -> None:
    pcm = tmp_path / "hi.pcm"
    pcm.write_bytes(b"\x02" * 64)
    script = ScriptInput.from_wire([{"pcm_path": str(pcm), "pause_s": 1.5}])
    assert isinstance(script, ScriptInput)


@pytest.mark.parametrize(
    "raw",
    [[], "x", [1], [{"pcm_path": "rel.pcm"}], [{"pcm_path": "/x", "pause_s": -1}]],
)
def test_from_wire_refuses_malformed_scripts(raw: object) -> None:
    with pytest.raises(ValueError):
        ScriptInput.from_wire(raw)
