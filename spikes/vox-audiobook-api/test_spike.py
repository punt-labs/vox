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

AUDIO = b"not really mp3 bytes"  # ffprobe fails on it; seconds() falls back


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
    return httpx.Response(200, content=AUDIO, headers={"request-id": "req-1"})


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
