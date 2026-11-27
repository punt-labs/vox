"""Offline tests for the Studio probe."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

import spike
from spike_fakes import (
    SNAP,
    Api,
    FakeClock,
    exit_code,
    make_zip,
    run,
    studio_api,
)

REFUSED = {"detail": "plan required"}


def test_studio_sends_multipart_without_filenames(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    api = Api(lambda _r: httpx.Response(403, json=REFUSED))
    run("studio", api, tmp_path, capsys)
    (request,) = api.requests
    assert request.headers["content-type"].startswith("multipart/form-data")
    assert b'name="from_content_json"' in request.content
    assert b"filename=" not in request.content


def test_studio_reports_403_body_and_stops(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    api = Api(lambda _r: httpx.Response(403, json=REFUSED))
    _, out = run("studio", api, tmp_path, capsys)
    assert len(api.requests) == 1
    assert "plan required" in out


def test_studio_403_exits_one(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    api = Api(lambda _r: httpx.Response(403, json=REFUSED))
    assert exit_code("studio", api, tmp_path) == 1
    assert "[studio-create] FAIL" in capsys.readouterr().out


def test_studio_html_error_while_polling_fails_the_probe_and_main_continues(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    FakeClock().install(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(200, json={"project_id": "p1", "state": "converting"})
        return httpx.Response(500, text="<html>gateway error</html>")

    api = Api(handler)
    assert exit_code("studio", api, tmp_path) == 1
    assert "[studio-poll] FAIL" in capsys.readouterr().out
    assert len(api.requests) == 2  # create, one poll; the probe stopped


def test_studio_non_json_ok_body_while_polling_is_recorded(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    FakeClock().install(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(200, json={"project_id": "p1"})
        return httpx.Response(200, text="<html>not json</html>")

    assert exit_code("studio", Api(handler), tmp_path) == 1
    assert "[studio] FAIL" in capsys.readouterr().out


def test_studio_poll_returning_a_list_is_recorded_as_studio_fail(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    FakeClock().install(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(200, json={"project_id": "p1"})
        return httpx.Response(200, json=[])

    assert exit_code("studio", Api(handler), tmp_path) == 1
    assert "[studio] FAIL" in capsys.readouterr().out


def test_studio_never_converting_fails_after_all_polls(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    clock = FakeClock().install(monkeypatch)
    api = studio_api(state="converting", clock=clock)
    assert exit_code("studio", api, tmp_path) == 1
    assert "[studio-convert] FAIL state converting after 240 s (40 polls)" in (
        capsys.readouterr().out
    )
    assert len(api.requests) == 1 + spike.POLLS  # create, polls; probe stopped


def test_studio_convert_pass_reports_elapsed_time_including_requests(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    clock = FakeClock().install(monkeypatch)  # 5 s sleep + 1 s request per poll
    exit_code("studio", studio_api(snapshots=SNAP, clock=clock), tmp_path)
    assert "[studio-convert] PASS state default after 6 s (1 polls)" in (
        capsys.readouterr().out
    )


def test_studio_full_success_exits_zero(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    FakeClock().install(monkeypatch)
    api = studio_api(snapshots=SNAP)
    assert exit_code("studio", api, tmp_path) == 0
    out = capsys.readouterr().out
    assert "[studio-snapshot] PASS" in out
    assert "[studio-archive] PASS 1 members" in out
    assert (tmp_path / "out" / "studio-book.zip").exists()


@pytest.mark.parametrize("reply", [{}, {"snapshots": []}, {"snapshots": None}])
def test_studio_without_snapshots_fails(
    reply: dict[str, object],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    FakeClock().install(monkeypatch)
    api = studio_api(snapshots=reply)
    assert exit_code("studio", api, tmp_path) == 1
    assert "[studio-snapshot] FAIL" in capsys.readouterr().out
    assert not any(p.endswith("/archive") for p in api.paths())


def test_studio_empty_chapter_list_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    FakeClock().install(monkeypatch)
    api = studio_api(snapshots=SNAP, chapters=[])
    assert exit_code("studio", api, tmp_path) == 1
    assert "[studio-chapters] FAIL 0 listed" in capsys.readouterr().out


def corrupted_zip() -> bytes:
    """A zip whose stored member no longer matches its CRC."""
    data = bytearray(make_zip())
    data[100] ^= 0xFF
    return bytes(data)


@pytest.mark.parametrize(
    ("body", "verdict"),
    [
        pytest.param(make_zip(), "PASS", id="real-zip"),
        pytest.param(b"PK" + bytes(2000), "FAIL", id="pk-prefix-zero-bytes"),
        pytest.param(b"stub", "FAIL", id="stub"),
        pytest.param(make_zip({}), "FAIL", id="empty-archive"),
        pytest.param(corrupted_zip(), "FAIL", id="crc-mismatch"),
    ],
)
def test_studio_archive_must_be_a_readable_non_empty_zip(
    body: bytes,
    verdict: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    FakeClock().install(monkeypatch)
    exit_code("studio", studio_api(snapshots=SNAP, archive=body), tmp_path)
    assert f"[studio-archive] {verdict}" in capsys.readouterr().out
