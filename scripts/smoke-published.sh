#!/usr/bin/env bash
set -euo pipefail

version=${1:?Pass the published version}
[[ "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || { echo "Invalid release version" >&2; exit 1; }
: "${OPENAI_API_KEY:?Set OPENAI_API_KEY for the published speech smoke}"
work=$(mktemp -d)
trap 'rm -rf -- "$work"' EXIT
cd "$work"
unset PYTHONPATH PYTHONHOME
unset UV_INDEX UV_INDEX_URL UV_EXTRA_INDEX_URL UV_FIND_LINKS UV_NO_INDEX
export PUNT_TELEMETRY=off
uv venv --python 3.13 "$work/venv"
for attempt in 1 2 3 4 5 6; do
    if uv --no-config pip install --python "$work/venv/bin/python" \
        --refresh-package punt-vox --default-index https://pypi.org/simple \
        "punt-vox[call]==$version"; then
        break
    fi
    (( attempt < 6 )) || exit 1
    sleep 7
done
"$work/venv/bin/vox" version
"$work/venv/bin/python" -I - "$version" <<'PY'
import re
import sys
from importlib.metadata import version
from pathlib import Path

from openai import OpenAI

from punt_vox.providers.openai import OpenAIProvider
from punt_vox.types import SynthesisRequest

if version("punt-vox") != sys.argv[1]:
    raise SystemExit("Installed package does not match the release tag")

line = "The river carries the morning light past the garden."
path = Path("smoke.mp3")
OpenAIProvider().synthesize(SynthesisRequest(text=line, language="en"), path)
with path.open("rb") as audio:
    transcript = OpenAI(timeout=20, max_retries=0).audio.transcriptions.create(
        model="gpt-4o-mini-transcribe", file=audio, language="en"
    ).text
if re.findall(r"[a-z]+", transcript.casefold()) != re.findall(r"[a-z]+", line.casefold()):
    raise SystemExit(f"Published Vox speech did not round-trip: {transcript!r}")
print("Published Vox speech round-trip passed (every word matched).")
PY
