"""Audiobook API spike: exercise the ElevenLabs endpoints voxd would call.

Reads ELEVENLABS_API_KEY from the environment (never printed). Each probe
prints the endpoint, HTTP status, selected response headers and a result line,
and writes any audio under OUT_DIR. Probes are independent.

    uv run --no-project --with httpx python spike.py OUT_DIR \
        tts|dialogue|pron|studio|all
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import httpx

BASE = "https://api.elevenlabs.io"
GEORGE = "JBFqnCBsd6RMkjVDRZzb"  # narrator
SARAH = "EXAVITQu4vr4xnSDxMaL"  # character
ROGER = "CwhRBWXzGAHq8TQ4Fs17"  # character
HEADERS_OF_INTEREST = ("request-id", "character-cost", "content-type")

CHAPTER = (
    "[warmly] Chapter one. The workflow began with a single call to "
    "PlaceOrderEvents. If validation failed, the system raised "
    "PlaceOrderError, and checkProductCodeExists returned nothing at all. "
    "Professor Elara Chen stared at the type definition for a long time. "
)


def client() -> httpx.Client:
    key = os.environ["ELEVENLABS_API_KEY"]
    return httpx.Client(base_url=BASE, headers={"xi-api-key": key}, timeout=300)


def seconds(path: Path) -> str:
    try:
        out = subprocess.run(
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
            check=True,
        )
        return f"{float(out.stdout):.1f}s"
    except (OSError, subprocess.CalledProcessError, ValueError):
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


def probe_tts(c: httpx.Client, out: Path) -> None:
    """v4 text-to-speech: a long chapter in one call, then a stitched follow-on."""
    text = CHAPTER * 12  # about 5,000 characters
    print(f"[tts] chapter length {len(text)} chars")
    body = {"text": text, "model_id": "eleven_v4"}
    r = c.post(
        f"/v1/text-to-speech/{GEORGE}",
        params={"output_format": "mp3_44100_128"},
        json=body,
    )
    report("tts-long", r)
    if r.status_code != 200:
        return
    save(out, "tts-long.mp3", r)
    rid = r.headers.get("request-id")
    r2 = c.post(
        f"/v1/text-to-speech/{GEORGE}",
        params={"output_format": "mp3_44100_128"},
        json={
            "text": "[softly] And then the cat looked up.",
            "model_id": "eleven_v4",
            "previous_request_ids": [rid] if rid else [],
        },
    )
    report("tts-stitched", r2)
    if r2.status_code == 200:
        save(out, "tts-stitched.mp3", r2)


def probe_dialogue(c: httpx.Client, out: Path) -> None:
    """Text-to-dialogue with 2 voices: does eleven_v4 take them, or only v3?"""
    inputs = [
        {"text": "[warmly] Professor Chen, the proof has a gap.", "voice_id": SARAH},
        {
            "text": "[sighs] There is no gap. There is only my tea going cold.",
            "voice_id": ROGER,
        },
        {"text": "[curious] And the cat?", "voice_id": SARAH},
    ]
    for model in ("eleven_v4", "eleven_v3"):
        r = c.post(
            "/v1/text-to-dialogue",
            params={"output_format": "mp3_44100_128"},
            json={"inputs": inputs, "model_id": model},
        )
        report(f"dialogue-{model}", r)
        if r.status_code == 200:
            save(out, f"dialogue-{model}.mp3", r)


def probe_pron(c: httpx.Client, out: Path) -> None:
    """Alias dictionary: create it, apply it, transcribe back to check the words."""
    rules = [
        {
            "type": "alias",
            "string_to_replace": "PlaceOrderEvents",
            "alias": "Place Order Events",
        },
        {
            "type": "alias",
            "string_to_replace": "checkProductCodeExists",
            "alias": "check product code exists",
        },
    ]
    r = c.post(
        "/v1/pronunciation-dictionaries/add-from-rules",
        json={"name": "vox-audiobook-spike", "rules": rules},
    )
    report("pron-create", r)
    if r.status_code != 200:
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
        with path.open("rb") as fh:
            tr = c.post(
                "/v1/speech-to-text",
                data={"model_id": "scribe_v1"},
                files={"file": (path.name, fh, "audio/mpeg")},
            )
        report(f"pron-stt-{label}", tr)
        if tr.status_code == 200:
            print(f"[pron-{label}] transcript: {tr.json().get('text')!r}")


def probe_studio(c: httpx.Client, out: Path) -> None:
    """Studio project from JSON: two chapters, two voices; convert; fetch snapshot."""
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
    r = c.post("/v1/studio/projects", data=form)
    report("studio-create", r)
    if r.status_code != 200:
        return
    project = r.json().get("project", r.json())
    pid = project["project_id"]
    print(f"[studio] project {pid}, state {project.get('state')}")
    for _ in range(40):
        time.sleep(5)
        state = c.get(f"/v1/studio/projects/{pid}").json().get("state")
        print(f"[studio] state {state}")
        if state == "default":
            break
    snaps = c.get(f"/v1/studio/projects/{pid}/snapshots")
    report("studio-snapshots", snaps)
    items: list[dict[str, str]] = (
        snaps.json().get("snapshots", []) if snaps.status_code == 200 else []
    )
    if items:
        sid: str = items[0]["project_snapshot_id"]
        a = c.post(f"/v1/studio/projects/{pid}/snapshots/{sid}/archive", json={})
        report("studio-archive", a)
        if a.status_code == 200:
            save(out, "studio-book.zip", a)
    report("studio-chapters", c.get(f"/v1/studio/projects/{pid}/chapters"))
    print(f"[studio] project left in place for inspection: {pid}")


PROBES = {
    "tts": probe_tts,
    "dialogue": probe_dialogue,
    "pron": probe_pron,
    "studio": probe_studio,
}


def main() -> None:
    out = Path(sys.argv[1])
    out.mkdir(parents=True, exist_ok=True)
    which = sys.argv[2] if len(sys.argv) > 2 else "all"
    c = client()
    for name in PROBES if which == "all" else [which]:
        print(f"=== {name} ===")
        try:
            PROBES[name](c, out)
        except httpx.HTTPError as exc:
            print(f"[{name}] transport error: {exc!r}")


if __name__ == "__main__":
    main()
