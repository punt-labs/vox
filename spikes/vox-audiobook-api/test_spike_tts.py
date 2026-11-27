"""Offline tests for the tts and dialogue probes and the audio verification."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import httpx
import pytest

import spike
from spike_fakes import (
    AUDIO_TYPE,
    MP3,
    UNDECODABLE_MP3,
    Api,
    audio_ok,
    exit_code,
    pron_api,
    run,
)

needs_ffprobe = pytest.mark.skipif(
    shutil.which("ffprobe") is None, reason="ffprobe is not on PATH"
)


def test_tts_stitches_second_call_with_first_request_id(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    api = Api(audio_ok)
    run("tts", api, tmp_path, capsys)
    long_call, stitched = api.json_bodies()
    assert "previous_request_ids" not in long_call
    assert stitched["previous_request_ids"] == ["req-1"]


def test_tts_writes_both_files(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    run("tts", Api(audio_ok), tmp_path, capsys)
    assert sorted(p.name for p in tmp_path.iterdir()) == [
        "tts-long.mp3",
        "tts-stitched.mp3",
    ]


def test_tts_stops_after_failed_long_call(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    api = Api(lambda _r: httpx.Response(401, text="bad key"))
    _, out = run("tts", api, tmp_path, capsys)
    assert len(api.requests) == 1
    assert "ERROR BODY: bad key" in out


def test_tts_refusal_exits_one(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    api = Api(lambda _r: httpx.Response(401, text="bad key"))
    assert exit_code("tts", api, tmp_path) == 1
    assert "[tts-long] FAIL" in capsys.readouterr().out


def test_tts_success_exits_zero(tmp_path: Path) -> None:
    assert exit_code("tts", Api(audio_ok), tmp_path) == 0


def test_tts_without_request_id_fails_stitching_and_sends_no_second_call(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    api = Api(lambda _r: httpx.Response(200, content=MP3, headers=AUDIO_TYPE))
    assert exit_code("tts", api, tmp_path) == 1
    assert "[tts-stitched] FAIL" in capsys.readouterr().out
    assert len(api.requests) == 1


def test_tts_tiny_audio_fails(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    api = Api(lambda _r: httpx.Response(200, content=b"x", headers={"request-id": "r"}))
    assert exit_code("tts", api, tmp_path) == 1
    out = capsys.readouterr().out
    assert "[tts-long] FAIL no content-type, 1 bytes" in out
    assert "[tts-stitched] FAIL no content-type, 1 bytes" in out


@needs_ffprobe
def test_tts_decodable_mp3_is_verified_by_ffprobe(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    verdicts, out = run("tts", Api(audio_ok), tmp_path, capsys)
    assert verdicts.failures == []
    assert (
        "[tts-long] PASS audio/mpeg, 5004 bytes, ffprobe read a duration of 0.3 s"
        in out
    )


@needs_ffprobe
def test_tts_undecodable_audio_with_audio_type_and_length_fails(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    api = Api(
        lambda _r: httpx.Response(
            200,
            content=UNDECODABLE_MP3,
            headers={"request-id": "r", **AUDIO_TYPE},
        )
    )
    assert exit_code("tts", api, tmp_path) == 1
    out = capsys.readouterr().out
    assert "[tts-long] FAIL audio/mpeg, 2003 bytes, ffprobe could not read" in out


def no_ffprobe(_name: str) -> None:
    """Stand in for shutil.which on a host without ffprobe."""


def test_without_ffprobe_audio_is_unverified_not_pass(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(spike.shutil, "which", no_ffprobe)
    api = Api(
        lambda _r: httpx.Response(
            200,
            content=UNDECODABLE_MP3,
            headers={"request-id": "r", **AUDIO_TYPE},
        )
    )
    verdicts, out = run("tts", api, tmp_path, capsys)
    assert verdicts.failures == []
    assert "[tts-long] WARN audio/mpeg, 2003 bytes, unverified (no ffprobe)" in out
    assert "[tts-long] PASS" not in out


def test_dialogue_attempts_both_models(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    api = Api(audio_ok)
    run("dialogue", api, tmp_path, capsys)
    assert [b["model_id"] for b in api.json_bodies()] == ["eleven_v4", "eleven_v3"]


def dialogue_api(*, refused: tuple[str, ...]) -> Api:
    """Fake text-to-dialogue refusing the named models and answering the rest."""

    def handler(request: httpx.Request) -> httpx.Response:
        if json.loads(request.content)["model_id"] in refused:
            return httpx.Response(422, text="unsupported model")
        return audio_ok(request)

    return Api(handler)


def test_dialogue_both_models_ok_exits_zero(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    verdicts, out = run("dialogue", dialogue_api(refused=()), tmp_path, capsys)
    assert verdicts.failures == []
    assert "[dialogue] PASS both models accepted two voices" in out


@pytest.mark.parametrize("refused", ["eleven_v4", "eleven_v3"])
def test_dialogue_a_refused_model_fails_the_run_and_the_other_still_runs(
    refused: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    api = dialogue_api(refused=(refused,))
    assert exit_code("dialogue", api, tmp_path) == 1
    out = capsys.readouterr().out
    assert f"[dialogue-{refused}] FAIL HTTP 422" in out
    assert "unsupported model" in out
    assert len(api.requests) == 2  # both models were attempted
    assert len(list((tmp_path / "out").iterdir())) == 1


def test_dialogue_both_refused_fails_each_model_and_overall(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    api = dialogue_api(refused=("eleven_v4", "eleven_v3"))
    verdicts, _ = run("dialogue", api, tmp_path, capsys)
    assert verdicts.failures == ["dialogue-eleven_v4", "dialogue-eleven_v3", "dialogue"]


def test_dialogue_stub_body_is_not_an_acceptance(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    api = Api(lambda _r: httpx.Response(200, content=b"x", headers=AUDIO_TYPE))
    assert exit_code("dialogue", api, tmp_path) == 1
    assert "[dialogue] FAIL" in capsys.readouterr().out


@pytest.mark.parametrize("probe", ["tts", "dialogue", "pron"])
def test_html_200_is_not_audio(
    probe: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    html = httpx.Response(
        200,
        content=b"<html>" + bytes(2000),
        headers={"content-type": "text/html", "request-id": "r"},
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/add-from-rules"):
            return pron_api("", "").handler(request)
        return html

    assert exit_code(probe, Api(handler), tmp_path) == 1
    assert "FAIL text/html" in capsys.readouterr().out


def infinite_duration(_path: Path) -> float:
    return float("inf")


def test_infinite_duration_is_not_a_pass(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(spike, "probe_duration", infinite_duration)
    verdicts, out = run("tts", Api(audio_ok), tmp_path, capsys)
    assert "tts-long" in verdicts.failures
    assert "[tts-long] FAIL" in out
    assert "ffprobe could not read a finite duration" in out


def test_without_ffprobe_the_run_ends_with_an_unverified_summary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(spike.shutil, "which", no_ffprobe)
    assert exit_code("tts", Api(audio_ok), tmp_path) == 0  # WARN does not fail
    last_line = capsys.readouterr().out.splitlines()[-1]
    assert last_line == "2 audio file(s) unverified (no ffprobe)"


@needs_ffprobe
def test_verified_run_prints_no_unverified_summary(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert exit_code("tts", Api(audio_ok), tmp_path) == 0
    assert "unverified" not in capsys.readouterr().out
