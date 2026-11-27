"""Offline tests for the pronunciation-dictionary probe."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, cast

import httpx

import spike
from spike_fakes import AUDIO_TYPE, Api, exit_code, pron_api, run

if TYPE_CHECKING:
    import pytest


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


def test_pron_partial_alias_does_not_count_as_applied_with_dictionary(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    near_miss = "order placed notifications and verify the product codes"
    verdicts, _ = run("pron", pron_api(near_miss, "plain"), tmp_path, capsys)
    assert verdicts.failures == ["pron-with-dict"]


def test_pron_partial_alias_does_not_count_as_leaked_without_dictionary(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    applied = "order placed notifications and verify the product code"
    near_miss = "order placed notification and verify the product codes"
    verdicts, _ = run("pron", pron_api(applied, near_miss), tmp_path, capsys)
    assert verdicts.failures == []


def test_pron_fails_when_a_transcript_is_missing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    api = pron_api("", "", scribe_up=False)
    verdicts, _ = run("pron", api, tmp_path, capsys)
    assert verdicts.failures == ["pron-with-dict", "pron-without-dict"]


def test_pron_empty_transcripts_fail_both_checks(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    verdicts, out = run("pron", pron_api("", ""), tmp_path, capsys)
    assert verdicts.failures == ["pron-with-dict", "pron-without-dict"]
    assert "no transcript to judge" in out


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
