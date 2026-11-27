"""Offline tests for the audiobook API spike: httpx.MockTransport, no network."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import cast

import httpx
import pytest

import spike

Handler = Callable[[httpx.Request], httpx.Response]

ZIP = b"PK" + bytes(2000)
AUDIO = b"ID3" + bytes(2000)  # ffprobe rejects it; seconds() falls back to bytes


class Api:
    """A fake ElevenLabs: records every request, answers through a handler."""

    def __init__(self, handler: Handler) -> None:
        self.requests: list[httpx.Request] = []
        self.handler = handler

    def client(self) -> httpx.Client:
        return httpx.Client(
            base_url=spike.BASE, transport=httpx.MockTransport(self._answer)
        )

    def _answer(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self.handler(request)

    def paths(self) -> list[str]:
        return [r.url.path for r in self.requests]

    def json_bodies(self, path_prefix: str = "/") -> list[dict[str, object]]:
        """Return the JSON bodies of the requests whose path starts with the prefix."""
        return [
            json.loads(r.content)
            for r in self.requests
            if r.url.path.startswith(path_prefix)
        ]


def audio_ok(_request: httpx.Request) -> httpx.Response:
    return httpx.Response(
        200,
        content=AUDIO,
        headers={"request-id": "req-1", "content-type": "audio/mpeg"},
    )


def run(
    probe: str, api: Api, out: Path, capsys: pytest.CaptureFixture[str]
) -> tuple[spike.Verdicts, str]:
    verdicts = spike.Verdicts()
    with api.client() as c:
        spike.PROBES[probe](c, out, verdicts)
    return verdicts, capsys.readouterr().out


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


def test_dialogue_attempts_both_models(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    api = Api(audio_ok)
    run("dialogue", api, tmp_path, capsys)
    assert [b["model_id"] for b in api.json_bodies()] == ["eleven_v4", "eleven_v3"]


def test_dialogue_tries_v3_after_v4_refusal(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if json.loads(request.content)["model_id"] == "eleven_v4":
            return httpx.Response(422, text="unsupported model")
        return audio_ok(request)

    api = Api(handler)
    _, out = run("dialogue", api, tmp_path, capsys)
    assert len(api.requests) == 2
    assert "unsupported model" in out
    assert [p.name for p in tmp_path.iterdir()] == ["dialogue-eleven_v3.mp3"]


def pron_api(with_dict: str, without_dict: str, *, scribe_up: bool = True) -> Api:
    """Fake the dictionary, speech and Scribe endpoints with canned transcripts."""

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/add-from-rules"):
            return httpx.Response(200, json={"id": "dict-1", "version_id": "v-1"})
        if path.startswith("/v1/text-to-speech/"):
            return audio_ok(request)
        assert path == "/v1/speech-to-text"
        if not scribe_up:
            return httpx.Response(500, text="scribe down")
        text = with_dict if b"pron-with-dict.mp3" in request.content else without_dict
        return httpx.Response(200, json={"text": text})

    return Api(handler)


def test_pron_creates_dictionary_then_speaks_with_and_without_it(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    api = pron_api("", "")
    run("pron", api, tmp_path, capsys)
    assert api.paths() == [
        "/v1/pronunciation-dictionaries/add-from-rules",
        f"/v1/text-to-speech/{spike.GEORGE}",
        "/v1/speech-to-text",
        f"/v1/text-to-speech/{spike.GEORGE}",
        "/v1/speech-to-text",
    ]
    with_dict, without_dict = api.json_bodies("/v1/text-to-speech/")
    assert with_dict["pronunciation_dictionary_locators"] == [
        {"pronunciation_dictionary_id": "dict-1", "version_id": "v-1"}
    ]
    assert without_dict["pronunciation_dictionary_locators"] == []


def test_pron_aliases_share_no_words_with_their_source(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    api = pron_api("", "")
    run("pron", api, tmp_path, capsys)
    (created,) = api.json_bodies("/v1/pronunciation-dictionaries/")
    rules = cast("list[dict[str, str]]", created["rules"])
    for rule in rules:
        source = set(spike.words(rule["string_to_replace"]).split())
        assert source.isdisjoint(spike.words(rule["alias"]).split())


def test_pron_passes_when_dictionary_changes_the_transcript(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    api = pron_api(
        "The call to order placed notifications failed, so verify the product code"
        " returned nothing.",
        "The call to place order events failed, so check product code exists"
        " returned nothing.",
    )
    verdicts, out = run("pron", api, tmp_path, capsys)
    assert verdicts.failures == []
    assert "[pron-with-dict] PASS" in out
    assert "[pron-without-dict] PASS" in out


def test_pron_fails_when_dictionary_is_ignored(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    same = "The call to place order events failed."
    verdicts, out = run("pron", pron_api(same, same), tmp_path, capsys)
    assert verdicts.failures == ["pron-with-dict"]
    assert "[pron-with-dict] FAIL" in out


def test_pron_fails_when_aliases_leak_without_the_dictionary(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    leaked = "order placed notifications and verify the product code"
    verdicts, out = run("pron", pron_api(leaked, leaked), tmp_path, capsys)
    assert verdicts.failures == ["pron-without-dict"]
    assert "[pron-without-dict] FAIL" in out


def test_pron_fails_when_a_transcript_is_missing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    api = pron_api("", "", scribe_up=False)
    verdicts, _ = run("pron", api, tmp_path, capsys)
    assert verdicts.failures == ["pron-with-dict", "pron-without-dict"]


def test_studio_sends_multipart_without_filenames(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    api = Api(lambda _r: httpx.Response(403, json={"detail": "plan required"}))
    run("studio", api, tmp_path, capsys)
    (request,) = api.requests
    assert request.headers["content-type"].startswith("multipart/form-data")
    assert b'name="from_content_json"' in request.content
    assert b"filename=" not in request.content


def test_studio_reports_403_body_and_stops(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    api = Api(lambda _r: httpx.Response(403, json={"detail": "plan required"}))
    _, out = run("studio", api, tmp_path, capsys)
    assert len(api.requests) == 1
    assert "plan required" in out


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


def no_sleep(_seconds: float) -> None:
    """Stand in for time.sleep so polling tests run instantly."""


def exit_code(probe: str, api: Api, out: Path) -> int:
    return spike.main([str(out / "out"), probe], make_client=api.client)


def test_pron_refused_create_exits_one_with_fail_lines(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    api = Api(lambda _r: httpx.Response(403, text="no pron"))
    assert exit_code("pron", api, tmp_path) == 1
    out = capsys.readouterr().out
    assert "[pron-with-dict] FAIL" in out
    assert "[pron-without-dict] FAIL" in out


def test_pron_scribe_timeout_exits_one(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ok = pron_api("", "")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/speech-to-text":
            raise httpx.ReadTimeout("scribe too slow", request=request)
        return ok.handler(request)

    api = Api(handler)
    assert exit_code("pron", api, tmp_path) == 1
    assert "[pron] FAIL" in capsys.readouterr().out


def test_tts_refusal_exits_one(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    api = Api(lambda _r: httpx.Response(401, text="bad key"))
    assert exit_code("tts", api, tmp_path) == 1
    assert "[tts-long] FAIL" in capsys.readouterr().out


def test_tts_success_exits_zero(tmp_path: Path) -> None:
    assert exit_code("tts", Api(audio_ok), tmp_path) == 0


def test_studio_403_exits_one(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    api = Api(lambda _r: httpx.Response(403, json={"detail": "plan required"}))
    assert exit_code("studio", api, tmp_path) == 1
    assert "[studio-create] FAIL" in capsys.readouterr().out


def test_studio_html_error_while_polling_fails_the_probe_and_main_continues(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(spike.time, "sleep", no_sleep)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(200, json={"project_id": "p1", "state": "converting"})
        return httpx.Response(500, text="<html>gateway error</html>")

    api = Api(handler)
    assert exit_code("studio", api, tmp_path) == 1
    out = capsys.readouterr().out
    assert "[studio-poll] FAIL" in out
    assert len(api.requests) == 2  # create, one poll; the probe stopped


def test_studio_non_json_ok_body_while_polling_is_recorded(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(spike.time, "sleep", no_sleep)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(200, json={"project_id": "p1"})
        return httpx.Response(200, text="<html>not json</html>")

    assert exit_code("studio", Api(handler), tmp_path) == 1
    assert "[studio] FAIL" in capsys.readouterr().out


def studio_api(
    *,
    state: str = "default",
    snapshots: dict[str, object] | None = None,
    chapters: list[object] | None = None,
    archive: bytes | None = None,
) -> Api:
    """Fake a Studio project; the chapters default to the two the probe creates."""
    chapters = [{}, {}] if chapters is None else chapters

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.method == "POST" and path == "/v1/studio/projects":
            return httpx.Response(200, json={"project_id": "p1"})
        if path == "/v1/studio/projects/p1":
            return httpx.Response(200, json={"state": state})
        if path.endswith("/snapshots"):
            return httpx.Response(200, json=snapshots or {})
        if path.endswith("/archive"):
            return httpx.Response(200, content=ZIP if archive is None else archive)
        return httpx.Response(200, json={"chapters": chapters})

    return Api(handler)


def test_studio_never_converting_fails_after_all_polls(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(spike.time, "sleep", no_sleep)
    api = studio_api(state="converting")
    assert exit_code("studio", api, tmp_path) == 1
    assert (
        "[studio-convert] FAIL state converting after 200 s" in capsys.readouterr().out
    )
    assert len(api.requests) == 1 + spike.POLLS  # create, polls; probe stopped


def test_studio_full_success_exits_zero(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(spike.time, "sleep", no_sleep)
    api = studio_api(snapshots={"snapshots": [{"project_snapshot_id": "s1"}]})
    assert exit_code("studio", api, tmp_path) == 0
    out = capsys.readouterr().out
    assert "[studio-convert] PASS" in out
    assert "[studio-snapshot] PASS" in out
    assert (tmp_path / "out" / "studio-book.zip").exists()


@pytest.mark.parametrize("reply", [{}, {"snapshots": []}, {"snapshots": None}])
def test_studio_without_snapshots_fails(
    reply: dict[str, object],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(spike.time, "sleep", no_sleep)
    api = studio_api(snapshots=reply)
    assert exit_code("studio", api, tmp_path) == 1
    assert "[studio-snapshot] FAIL" in capsys.readouterr().out
    assert not any(p.endswith("/archive") for p in api.paths())


def test_pron_empty_transcripts_fail_both_checks(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    verdicts, out = run("pron", pron_api("", ""), tmp_path, capsys)
    assert verdicts.failures == ["pron-with-dict", "pron-without-dict"]
    assert "no transcript to judge" in out


def test_tts_without_request_id_fails_stitching_and_sends_no_second_call(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    api = Api(
        lambda _r: httpx.Response(
            200, content=AUDIO, headers={"content-type": "audio/mpeg"}
        )
    )
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
    out = capsys.readouterr().out
    assert "[tts] FAIL" in out
    assert "/v1/text-to-dialogue" in api.paths()  # dialogue ran after tts


def full_studio_exit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, api: Api) -> int:
    monkeypatch.setattr(spike.time, "sleep", no_sleep)
    return exit_code("studio", api, tmp_path)


AUDIO_TYPE = {"content-type": "audio/mpeg"}
SNAP: dict[str, object] = {"snapshots": [{"project_snapshot_id": "s1"}]}


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


def test_studio_poll_returning_a_list_is_recorded_as_studio_fail(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(200, json={"project_id": "p1"})
        return httpx.Response(200, json=[])

    assert full_studio_exit(tmp_path, monkeypatch, Api(handler)) == 1
    assert "[studio] FAIL" in capsys.readouterr().out


def test_studio_empty_chapter_list_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    api = studio_api(snapshots=SNAP, chapters=[])
    assert full_studio_exit(tmp_path, monkeypatch, api) == 1
    assert "[studio-chapters] FAIL 0 listed" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("body", "verdict"),
    [(b"stub", "FAIL"), (b"PK" + bytes(5), "FAIL"), (ZIP, "PASS")],
)
def test_studio_archive_must_be_a_real_zip(
    body: bytes,
    verdict: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    api = studio_api(snapshots=SNAP, archive=body)
    full_studio_exit(tmp_path, monkeypatch, api)
    assert f"[studio-archive] {verdict}" in capsys.readouterr().out


def test_studio_convert_pass_reports_actual_polls(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    full_studio_exit(tmp_path, monkeypatch, studio_api(snapshots=SNAP))
    assert "[studio-convert] PASS state default after 5 s (1 polls)" in (
        capsys.readouterr().out
    )


def test_dialogue_stub_body_is_not_an_acceptance(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    api = Api(lambda _r: httpx.Response(200, content=b"x", headers=AUDIO_TYPE))
    assert exit_code("dialogue", api, tmp_path) == 1
    assert "[dialogue] FAIL" in capsys.readouterr().out


def test_pron_stub_speech_is_no_transcript(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith("/v1/text-to-speech/"):
            return httpx.Response(200, content=b"x", headers=AUDIO_TYPE)
        return pron_api("", "").handler(request)

    api = Api(handler)
    assert exit_code("pron", api, tmp_path) == 1
    out = capsys.readouterr().out
    assert "[pron-tts-with-dict] FAIL" in out
    assert "[pron-with-dict] FAIL no transcript to judge" in out
    assert "/v1/speech-to-text" not in api.paths()


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
