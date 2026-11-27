"""Audiobook API spike: exercise the ElevenLabs endpoints voxd would call.

Reads ELEVENLABS_API_KEY from the environment (never printed). Each probe
prints the endpoint, HTTP status, selected response headers and a result line,
and writes any audio under OUT_DIR. Probes are independent.

    uv run --no-project --with httpx python spike.py OUT_DIR \
        [tts|dialogue|pron|studio|all]

The probe defaults to all. Assertions print PASS or FAIL lines; the exit status
is 1 when any assertion fails.
"""

from __future__ import annotations

import argparse
import io
import json
import lzma
import math
import os
import re
import shutil
import subprocess
import time
import zipfile
import zlib
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast

import httpx

BASE = "https://api.elevenlabs.io"
GEORGE = "JBFqnCBsd6RMkjVDRZzb"  # narrator
SARAH = "EXAVITQu4vr4xnSDxMaL"  # character
ROGER = "CwhRBWXzGAHq8TQ4Fs17"  # character
POLLS = 40
POLL_SECONDS = 5
MIN_AUDIO_BYTES = 1000  # anything smaller is an error stub, not speech
HEADERS_OF_INTEREST = ("request-id", "character-cost", "content-type")

CHAPTER = (
    "[warmly] Chapter one. The workflow began with a single call to "
    "PlaceOrderEvents. If validation failed, the system raised "
    "PlaceOrderError, and checkProductCodeExists returned nothing at all. "
    "Professor Elara Chen stared at the type definition for a long time. "
)


@dataclass
class Verdicts:
    """Collect PASS/FAIL assertions so main() can set the exit status."""

    failures: list[str] = field(default_factory=list[str])
    unverified: int = 0

    def check(self, name: str, *, ok: bool, detail: str = "") -> None:
        print(f"[{name}] {'PASS' if ok else 'FAIL'} {detail}".rstrip())
        if not ok:
            self.failures.append(name)

    def expect_ok(self, name: str, r: httpx.Response) -> bool:
        """Record a FAIL unless the response is 200; return whether it was."""
        ok = r.status_code == 200
        if not ok:
            self.check(name, ok=False, detail=f"HTTP {r.status_code}")
        return ok

    def check_audio(self, name: str, r: httpx.Response, path: Path) -> bool:
        """Record whether a saved 200 reply looks like audio.

        Checks the content type, the length and that ffprobe reads a finite,
        positive duration from the saved file (a container-header read, not a
        full decode). Without ffprobe the body is unverified: that prints WARN,
        not PASS, counts toward the final summary and does not fail the run.
        """
        kind = r.headers.get("content-type", "")
        detail = f"{kind or 'no content-type'}, {len(r.content)} bytes"
        if not (kind.startswith("audio/") and len(r.content) >= MIN_AUDIO_BYTES):
            self.check(name, ok=False, detail=detail)
            return False
        duration = probe_duration(path)
        if duration is None:
            self.unverified += 1
            print(f"[{name}] WARN {detail}, unverified (no ffprobe)")
            return True
        ok = math.isfinite(duration) and duration > 0
        read = (
            f"ffprobe read a duration of {duration:.1f} s"
            if ok
            else "ffprobe could not read a finite duration"
        )
        self.check(name, ok=ok, detail=f"{detail}, {read}")
        return ok

    def summary(self) -> None:
        """Print the durable signal for WARN outcomes, which do not fail the run."""
        if self.unverified:
            print(f"{self.unverified} audio file(s) unverified (no ffprobe)")

    def check_zip(self, name: str, r: httpx.Response) -> bool:
        """Record whether a reply is a readable zip holding at least one member."""
        try:
            with zipfile.ZipFile(io.BytesIO(r.content)) as archive:
                bad_member = archive.testzip()
                members = len(archive.namelist())
        except (
            zipfile.BadZipFile,
            zlib.error,
            lzma.LZMAError,
            NotImplementedError,
            EOFError,
            RuntimeError,  # an encrypted member needs a password
        ) as exc:
            self.check(name, ok=False, detail=f"unreadable zip: {exc!r}")
            return False
        ok = bad_member is None and members >= 1
        self.check(name, ok=ok, detail=f"{members} members, bad member {bad_member}")
        return ok


def client() -> httpx.Client:
    key = os.environ["ELEVENLABS_API_KEY"]
    return httpx.Client(base_url=BASE, headers={"xi-api-key": key}, timeout=300)


def probe_duration(path: Path) -> float | None:
    """Return the duration ffprobe reads, 0.0 if it rejects the file, None if absent."""
    if shutil.which("ffprobe") is None:
        return None
    done = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=nw=1:nk=1",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    try:
        return float(done.stdout) if done.returncode == 0 else 0.0
    except ValueError:
        return 0.0


def seconds(path: Path) -> str:
    duration = probe_duration(path)
    if duration and math.isfinite(duration):
        return f"{duration:.1f}s"
    return f"{path.stat().st_size} bytes"


def report(name: str, r: httpx.Response) -> None:
    hdrs = {h: r.headers[h] for h in HEADERS_OF_INTEREST if h in r.headers}
    print(f"[{name}] {r.request.method} {r.request.url.path} -> {r.status_code} {hdrs}")
    if r.status_code >= 400:
        print(f"[{name}] ERROR BODY: {r.text[:600]}")


def save(out: Path, name: str, r: httpx.Response) -> Path:
    path = out / name
    path.write_bytes(r.content)
    print(f"[{name}] wrote {path} ({seconds(path)})")
    return path


def probe_tts(c: httpx.Client, out: Path, verdicts: Verdicts) -> None:
    """v4 text-to-speech: a long chapter in one call, then a stitched follow-on."""
    text = CHAPTER * 12  # about 3,100 characters
    print(f"[tts] chapter length {len(text)} chars")
    body = {"text": text, "model_id": "eleven_v4"}
    r = c.post(
        f"/v1/text-to-speech/{GEORGE}",
        params={"output_format": "mp3_44100_128"},
        json=body,
    )
    report("tts-long", r)
    if not verdicts.expect_ok("tts-long", r):
        return
    saved = save(out, "tts-long.mp3", r)
    verdicts.check_audio("tts-long", r, saved)
    rid = r.headers.get("request-id")
    if not rid:
        verdicts.check("tts-stitched", ok=False, detail="no request-id to stitch from")
        return
    r2 = c.post(
        f"/v1/text-to-speech/{GEORGE}",
        params={"output_format": "mp3_44100_128"},
        json={
            "text": "[softly] And then the cat looked up.",
            "model_id": "eleven_v4",
            "previous_request_ids": [rid],
        },
    )
    report("tts-stitched", r2)
    if verdicts.expect_ok("tts-stitched", r2):
        saved2 = save(out, "tts-stitched.mp3", r2)
        verdicts.check_audio("tts-stitched", r2, saved2)


def probe_dialogue(c: httpx.Client, out: Path, verdicts: Verdicts) -> None:
    """Text-to-dialogue with 2 voices: does eleven_v4 take them, or only v3?

    Each model's call is its own verdict: a refusal, or a 200 whose body is not
    audio, records FAIL for that model. The overall "dialogue" verdict passes
    only when both models returned audio.
    """
    inputs = [
        {"text": "[warmly] Professor Chen, the proof has a gap.", "voice_id": SARAH},
        {
            "text": "[sighs] There is no gap. There is only my tea going cold.",
            "voice_id": ROGER,
        },
        {"text": "[curious] And the cat?", "voice_id": SARAH},
    ]
    accepted: list[bool] = []
    for model in ("eleven_v4", "eleven_v3"):
        r = c.post(
            "/v1/text-to-dialogue",
            params={"output_format": "mp3_44100_128"},
            json={"inputs": inputs, "model_id": model},
        )
        report(f"dialogue-{model}", r)
        if verdicts.expect_ok(f"dialogue-{model}", r):
            saved = save(out, f"dialogue-{model}.mp3", r)
            accepted.append(verdicts.check_audio(f"dialogue-{model}", r, saved))
        else:
            accepted.append(False)
    verdicts.check(
        "dialogue", ok=all(accepted), detail="both models accepted two voices"
    )


def words(text: str) -> str:
    """Lowercase and strip punctuation so transcripts compare by words."""
    return " ".join(re.findall(r"[a-z0-9]+", text.lower()))


def probe_pron(c: httpx.Client, out: Path, verdicts: Verdicts) -> None:
    """Alias dictionary: apply it, then assert the transcript carries the aliases."""
    # The aliases share no words with their source text, so a transcript
    # holding them can only come from an applied dictionary.
    rules = [
        {
            "type": "alias",
            "string_to_replace": "PlaceOrderEvents",
            "alias": "order placed notifications",
        },
        {
            "type": "alias",
            "string_to_replace": "checkProductCodeExists",
            "alias": "verify the product code",
        },
    ]
    transcripts: dict[str, str] = {}
    r = c.post(
        "/v1/pronunciation-dictionaries/add-from-rules",
        json={"name": "vox-audiobook-spike", "rules": rules},
    )
    report("pron-create", r)
    if not verdicts.expect_ok("pron-create", r):
        for label in ("with-dict", "without-dict"):
            verdicts.check(f"pron-{label}", ok=False, detail="no dictionary created")
        return
    loc = {
        "pronunciation_dictionary_id": r.json()["id"],
        "version_id": r.json()["version_id"],
    }
    print(f"[pron] dictionary {loc}")
    text = (
        "The call to PlaceOrderEvents failed, "
        "so checkProductCodeExists returned nothing."
    )
    for label, locators in (("with-dict", [loc]), ("without-dict", [])):
        body = {
            "text": text,
            "model_id": "eleven_v4",
            "pronunciation_dictionary_locators": locators,
        }
        rr = c.post(
            f"/v1/text-to-speech/{GEORGE}",
            params={"output_format": "mp3_44100_128"},
            json=body,
        )
        report(f"pron-tts-{label}", rr)
        if rr.status_code != 200:
            continue
        path = save(out, f"pron-{label}.mp3", rr)
        if not verdicts.check_audio(f"pron-tts-{label}", rr, path):
            continue  # no speech to transcribe: falls to "no transcript to judge"
        with path.open("rb") as fh:
            tr = c.post(
                "/v1/speech-to-text",
                data={"model_id": "scribe_v1"},
                files={"file": (path.name, fh, "audio/mpeg")},
            )
        report(f"pron-stt-{label}", tr)
        if tr.status_code == 200:
            text_heard = tr.json().get("text")
            # An empty transcript is no transcript: it would pass "lacks" vacuously.
            if isinstance(text_heard, str) and words(text_heard):
                transcripts[label] = words(text_heard)
                print(f"[pron-{label}] transcript: {transcripts[label]!r}")
    aliases = [words(rule["alias"]) for rule in rules]
    for label, expect_aliases in (("with-dict", True), ("without-dict", False)):
        heard = transcripts.get(label)
        if heard is None:
            verdicts.check(f"pron-{label}", ok=False, detail="no transcript to judge")
            continue
        # Pad with spaces so "verify the product codes" is not the alias.
        found = [f" {alias} " in f" {heard} " for alias in aliases]
        ok = all(found) if expect_aliases else not any(found)
        expected = "carries" if expect_aliases else "lacks"
        verdicts.check(
            f"pron-{label}", ok=ok, detail=f"transcript {expected} the alias words"
        )


def probe_studio(c: httpx.Client, out: Path, verdicts: Verdicts) -> None:
    """Studio project from JSON: two chapters, two voices; convert; fetch snapshot.

    A refused create (403 when the account lacks Studio) is an expected account
    limit, but the probe could not do its job, so it records FAIL and the exit
    status is 1. A non-200 or non-JSON reply while polling does the same.
    """
    content = [
        {
            "name": "Chapter 1",
            "blocks": [
                {
                    "sub_type": "p",
                    "nodes": [
                        {
                            "type": "tts_node",
                            "voice_id": GEORGE,
                            "text": (
                                "Professor Elara Chen had spent thirty years "
                                "proving everything reduced to logic."
                            ),
                        }
                    ],
                },
                {
                    "sub_type": "p",
                    "nodes": [
                        {
                            "type": "tts_node",
                            "voice_id": SARAH,
                            "text": "There is a gap in the proof, Professor.",
                        }
                    ],
                },
            ],
        },
        {
            "name": "Chapter 2",
            "blocks": [
                {
                    "sub_type": "p",
                    "nodes": [
                        {
                            "type": "tts_node",
                            "voice_id": ROGER,
                            "text": "The cat, for the first time, looked up.",
                        }
                    ],
                },
            ],
        },
    ]
    form = {
        "name": "vox-audiobook-spike",
        "default_title_voice_id": GEORGE,
        "default_paragraph_voice_id": GEORGE,
        "default_model_id": "eleven_v4",
        "from_content_json": json.dumps(content),
        "auto_convert": "true",
    }
    # The API documents only multipart/form-data; (None, value) makes each
    # field a filename-free part.
    r = c.post(
        "/v1/studio/projects",
        files={key: (None, value) for key, value in form.items()},
    )
    report("studio-create", r)
    if not verdicts.expect_ok("studio-create", r):
        return
    project = r.json().get("project", r.json())
    pid = project["project_id"]
    print(f"[studio] project {pid}, state {project.get('state')}")
    state = None
    polls = 0
    started = time.monotonic()
    for _ in range(POLLS):
        polls += 1
        time.sleep(POLL_SECONDS)
        polled = c.get(f"/v1/studio/projects/{pid}")
        if not verdicts.expect_ok("studio-poll", polled):
            report("studio-poll", polled)
            return
        state = polled.json().get("state")  # non-JSON raises; main() records it
        print(f"[studio] state {state}")
        if state == "default":
            break
    elapsed = time.monotonic() - started
    verdicts.check(
        "studio-convert",
        ok=state == "default",
        detail=f"state {state} after {elapsed:.0f} s ({polls} polls)",
    )
    if state != "default":
        return
    snaps = c.get(f"/v1/studio/projects/{pid}/snapshots")
    report("studio-snapshots", snaps)
    verdicts.expect_ok("studio-snapshots", snaps)
    items: list[dict[str, str]] = (
        snaps.json().get("snapshots") or [] if snaps.status_code == 200 else []
    )
    if snaps.status_code == 200:
        verdicts.check("studio-snapshot", ok=bool(items), detail=f"{len(items)} found")
    if items:
        sid: str = items[0]["project_snapshot_id"]
        a = c.post(f"/v1/studio/projects/{pid}/snapshots/{sid}/archive", json={})
        report("studio-archive", a)
        if verdicts.expect_ok("studio-archive", a):
            save(out, "studio-book.zip", a)
            verdicts.check_zip("studio-archive", a)
    chapters = c.get(f"/v1/studio/projects/{pid}/chapters")
    report("studio-chapters", chapters)
    if verdicts.expect_ok("studio-chapters", chapters):
        listed: object = chapters.json().get("chapters")
        count = len(cast("list[object]", listed)) if isinstance(listed, list) else -1
        # The probe created two chapters.
        verdicts.check("studio-chapters", ok=count == 2, detail=f"{count} listed")
    print(f"[studio] project left in place for inspection: {pid}")


PROBES: dict[str, Callable[[httpx.Client, Path, Verdicts], None]] = {
    "tts": probe_tts,
    "dialogue": probe_dialogue,
    "pron": probe_pron,
    "studio": probe_studio,
}


def parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Probe the ElevenLabs endpoints an audiobook Program would call.",
    )
    parser.add_argument("out_dir", type=Path, metavar="OUT_DIR")
    parser.add_argument("probe", nargs="?", choices=[*PROBES, "all"], default="all")
    return parser.parse_args(argv)


def main(
    argv: Sequence[str] | None = None,
    make_client: Callable[[], httpx.Client] = client,
) -> int:
    """Run the chosen probes; return 1 when any assertion failed."""
    args = parse_args(argv)  # before any filesystem or client side effect
    args.out_dir.mkdir(parents=True, exist_ok=True)
    verdicts = Verdicts()
    with make_client() as c:
        for name in PROBES if args.probe == "all" else [args.probe]:
            print(f"=== {name} ===")
            try:
                PROBES[name](c, args.out_dir, verdicts)
            except (
                httpx.HTTPError,
                ValueError,
                KeyError,
                TypeError,
                AttributeError,
                OSError,
            ) as exc:
                # ValueError covers JSONDecodeError; KeyError/TypeError/AttributeError
                # an unexpected JSON shape (AttributeError: valid JSON that is not an
                # object); OSError a failed save. Later probes still run.
                verdicts.check(name, ok=False, detail=repr(exc))
    verdicts.summary()
    return 1 if verdicts.failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
