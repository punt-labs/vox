"""Offline tests for main(): argument parsing, exit status and probe isolation."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

import spike
from spike_fakes import Api, audio_ok, pron_api


def test_help_exits_zero_without_side_effects(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(tmp_path)

    def no_client() -> httpx.Client:
        pytest.fail("--help must not build the HTTP client")

    with pytest.raises(SystemExit) as exc:
        spike.main(["--help"], make_client=no_client)
    assert exc.value.code == 0
    assert "OUT_DIR" in capsys.readouterr().out
    assert list(tmp_path.iterdir()) == []


def test_unknown_probe_is_a_usage_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    def no_client() -> httpx.Client:
        pytest.fail("a usage error must not build the HTTP client")

    out = tmp_path / "out"
    with pytest.raises(SystemExit) as exc:
        spike.main([str(out), "bogus"], make_client=no_client)
    assert exc.value.code == 2
    assert "invalid choice: 'bogus'" in capsys.readouterr().err
    assert not out.exists()


def test_main_defaults_to_all_probes_and_exits_one_when_all_refused(
    tmp_path: Path,
) -> None:
    api = Api(lambda _r: httpx.Response(403, text="nope"))
    assert spike.main([str(tmp_path / "out")], make_client=api.client) == 1
    assert api.paths() == [  # every probe ran, each refused at its first call
        f"/v1/text-to-speech/{spike.GEORGE}",
        "/v1/text-to-dialogue",
        "/v1/text-to-dialogue",
        "/v1/pronunciation-dictionaries/add-from-rules",
        "/v1/studio/projects",
    ]


def test_main_exits_one_when_a_probe_fails(tmp_path: Path) -> None:
    api = pron_api("same", "same")
    assert spike.main([str(tmp_path / "out"), "pron"], make_client=api.client) == 1


def test_unexpected_json_shape_fails_that_probe_and_later_probes_run(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/add-from-rules"):
            return httpx.Response(200, json={})  # no "id": KeyError in the probe
        return httpx.Response(403, text="nope")

    api = Api(handler)
    assert spike.main([str(tmp_path / "out")], make_client=api.client) == 1
    assert "[pron] FAIL" in capsys.readouterr().out
    assert api.paths()[-1] == "/v1/studio/projects"  # studio ran after pron


def test_pron_create_without_id_fails_that_probe_and_later_probes_run(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A list reply to add-from-rules raises TypeError at r.json()["id"]."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/add-from-rules"):
            return httpx.Response(200, json=[])
        return httpx.Response(403, text="nope")

    api = Api(handler)
    assert spike.main([str(tmp_path / "out")], make_client=api.client) == 1
    assert "[pron] FAIL" in capsys.readouterr().out
    assert api.paths()[-1] == "/v1/studio/projects"


def test_scribe_non_object_json_fails_that_probe_and_later_probes_run(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A list reply from Scribe raises AttributeError at tr.json().get("text")."""
    ok = pron_api("", "")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/speech-to-text":
            return httpx.Response(200, json=[])
        if request.url.path == "/v1/studio/projects":
            return httpx.Response(403, text="nope")
        if request.url.path == "/v1/text-to-dialogue":
            return audio_ok(request)
        return ok.handler(request)

    api = Api(handler)
    assert spike.main([str(tmp_path / "out"), "all"], make_client=api.client) == 1
    assert "[pron] FAIL" in capsys.readouterr().out
    assert api.paths()[-1] == "/v1/studio/projects"


def test_save_oserror_fails_that_probe_and_later_probes_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def broken_save(_out: Path, _name: str, _r: httpx.Response) -> Path:
        raise OSError("disk full")

    monkeypatch.setattr(spike, "save", broken_save)
    api = Api(audio_ok)
    assert spike.main([str(tmp_path / "out"), "all"], make_client=api.client) == 1
    assert "[tts] FAIL" in capsys.readouterr().out
    assert "/v1/text-to-dialogue" in api.paths()  # dialogue ran after tts
