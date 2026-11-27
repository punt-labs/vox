"""Shared fakes for the audiobook spike tests: a mock ElevenLabs and its fixtures."""

from __future__ import annotations

import io
import json
import zipfile
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Self

import httpx

import spike

Handler = Callable[[httpx.Request], httpx.Response]

# MPEG-1 Layer III, 128 kbps, 44.1 kHz, mono: 417 bytes per frame. Twelve silent
# frames are a real, decodable MP3 (about 0.3 s) that ffprobe accepts.
_MP3_FRAME = bytes([0xFF, 0xFB, 0x90, 0xC4]) + bytes(413)
MP3 = _MP3_FRAME * 12
# Long enough to pass the size check, but ffprobe rejects it.
UNDECODABLE_MP3 = b"ID3" + bytes(2000)
AUDIO_TYPE = {"content-type": "audio/mpeg"}
SNAP: dict[str, object] = {"snapshots": [{"project_snapshot_id": "s1"}]}

if TYPE_CHECKING:
    import pytest


def make_zip(
    members: dict[str, bytes] | None = None, *, compression: int = zipfile.ZIP_STORED
) -> bytes:
    """Return a real zip archive holding the given members (one by default)."""
    members = {"chapter-1.mp3": MP3} if members is None else members
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression) as archive:
        for name, data in members.items():
            archive.writestr(name, data)
    return buffer.getvalue()


class Api:
    """A fake ElevenLabs: records every request, answers through a handler."""

    _requests: list[httpx.Request]
    _handler: Handler

    def __new__(cls, handler: Handler) -> Self:
        self = super().__new__(cls)
        self._requests = []
        self._handler = handler
        return self

    @property
    def requests(self) -> list[httpx.Request]:
        return self._requests

    @property
    def handler(self) -> Handler:
        return self._handler

    def client(self) -> httpx.Client:
        return httpx.Client(
            base_url=spike.BASE, transport=httpx.MockTransport(self._answer)
        )

    def _answer(self, request: httpx.Request) -> httpx.Response:
        self._requests.append(request)
        return self._handler(request)

    def paths(self) -> list[str]:
        return [r.url.path for r in self._requests]

    def json_bodies(self, path_prefix: str = "/") -> list[dict[str, object]]:
        """Return the JSON bodies of the requests whose path starts with the prefix."""
        return [
            json.loads(r.content)
            for r in self._requests
            if r.url.path.startswith(path_prefix)
        ]


class FakeClock:
    """A clock the polling loop can wait on instantly; time moves only when told."""

    _now: float

    def __new__(cls) -> Self:
        self = super().__new__(cls)
        self._now = 0.0
        return self

    def advance(self, seconds: float) -> None:
        self._now += seconds

    def monotonic(self) -> float:
        return self._now

    def install(self, monkeypatch: pytest.MonkeyPatch) -> Self:
        monkeypatch.setattr(spike.time, "sleep", self.advance)
        monkeypatch.setattr(spike.time, "monotonic", self.monotonic)
        return self


def audio_ok(_request: httpx.Request) -> httpx.Response:
    return httpx.Response(
        200,
        content=MP3,
        headers={"request-id": "req-1", "content-type": "audio/mpeg"},
    )


def run(
    probe: str, api: Api, out: Path, capsys: pytest.CaptureFixture[str]
) -> tuple[spike.Verdicts, str]:
    verdicts = spike.Verdicts()
    with api.client() as c:
        spike.PROBES[probe](c, out, verdicts)
    return verdicts, capsys.readouterr().out


def exit_code(probe: str, api: Api, out: Path) -> int:
    return spike.main([str(out / "out"), probe], make_client=api.client)


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


def studio_api(
    *,
    state: str = "default",
    snapshots: dict[str, object] | None = None,
    chapters: list[object] | None = None,
    archive: bytes | None = None,
    clock: FakeClock | None = None,
) -> Api:
    """Fake a Studio project; the chapters default to the two the probe creates.

    With a clock, each status poll takes one second of request time.
    """
    chapters = [{}, {}] if chapters is None else chapters
    archive = make_zip() if archive is None else archive

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.method == "POST" and path == "/v1/studio/projects":
            return httpx.Response(200, json={"project_id": "p1"})
        if path == "/v1/studio/projects/p1":
            if clock is not None:
                clock.advance(1.0)
            return httpx.Response(200, json={"state": state})
        if path.endswith("/snapshots"):
            return httpx.Response(200, json=snapshots or {})
        if path.endswith("/archive"):
            return httpx.Response(200, content=archive)
        return httpx.Response(200, json={"chapters": chapters})

    return Api(handler)
