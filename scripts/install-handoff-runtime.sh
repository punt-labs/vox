#!/usr/bin/env bash
set -euo pipefail

repo=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)
sidecar=$repo/.punt-labs/ethos/checks/snapshots/vox-python.yaml
if [[ ! -f $sidecar ]]; then
    echo 'No Vox snapshot runtime has been pinned in this checkout.' >&2
    exit 1
fi

case "$(uname -s)/$(uname -m)" in
    Linux/x86_64) platform=linux-amd64 ;;
    Darwin/arm64)
        macos=$(sw_vers -productVersion)
        if [[ ! $macos =~ ^([0-9]+)\.([0-9]+)(\.[0-9]+)?$ ]] ||
            ! ((10#${BASH_REMATCH[1]} > 13 ||
                (10#${BASH_REMATCH[1]} == 13 && 10#${BASH_REMATCH[2]} >= 5))); then
            printf 'The Vox handoff runtime requires macOS 13.5 or newer (found %s).\n' "$macos" >&2
            exit 1
        fi
        platform=darwin-arm64
        ;;
    *) echo 'No Vox snapshot runtime is published for this platform.' >&2; exit 1 ;;
esac

cd /
if ! python3 -I -c '' || ! command -v curl >/dev/null; then
    echo 'Vox snapshot runtime installation requires Python 3 and curl.' >&2
    exit 1
fi

pin=$(python3 -I - "$sidecar" "$platform" <<'PY'
import json
import re
import sys

try:
    with open(sys.argv[1], encoding="utf-8") as sidecar:
        platforms = json.load(sidecar)["checks"]["full-gate"]["runtime"]["platforms"]
    if not isinstance(platforms, dict):
        raise TypeError
except (OSError, KeyError, TypeError, ValueError):
    sys.exit("Invalid Vox snapshot sidecar: expected a JSON-formatted platform map.")
platform = sys.argv[2]
if platform not in platforms:
    sys.exit(f"No Vox snapshot runtime is pinned for {platform}.")
entry = platforms[platform]
expected_url = (
    r"https://github\.com/punt-labs/vox/releases/download/"
    r"handoff-runtime-v[1-9][0-9]*/vox-handoff-" + re.escape(platform) + r"\.tar\.gz"
)
try:
    url, sha = entry["url"], entry["sha256"]
    if not re.fullmatch(r"[0-9a-f]{64}", sha) or not re.fullmatch(expected_url, url):
        raise ValueError
except (KeyError, TypeError, ValueError):
    sys.exit(f"Invalid Vox runtime pin for {platform}.")
print(url, sha)
PY
)
read -r url sha <<< "$pin"

local_root=$HOME/.punt-labs
ethos_root=$local_root/ethos
store=$ethos_root/runtimes
archive=$store/$sha.tar.gz
umask 077
for dir in "$local_root" "$ethos_root" "$store"; do
    if [[ -L $dir ]]; then
        printf 'Vox runtime store path must not be a symlink: %s\n' "$dir" >&2
        exit 1
    fi
    mkdir -p -- "$dir"
done
if [[ ! -O $store ]]; then
    echo 'Vox runtime store must be owned by the current user.' >&2
    exit 1
fi
chmod 700 -- "$store"
digest() {
    python3 -I - "$1" <<'PY'
import hashlib
import sys

sha = hashlib.sha256()
with open(sys.argv[1], "rb") as archive:
    for block in iter(lambda: archive.read(1024 * 1024), b""):
        sha.update(block)
print(sha.hexdigest())
PY
}

if [[ -L $archive || ( -e $archive && ! -f $archive ) ]]; then
    printf 'Vox runtime archive path must be a regular file, not a symlink: %s\n' "$archive" >&2
    exit 1
fi
if [[ -f $archive ]]; then
    if [[ $(digest "$archive") == "$sha" ]]; then
        printf '%s\n' "$archive"
        exit 0
    fi
    echo 'Cached Vox runtime failed verification; downloading a fresh copy.' >&2
fi

download=$(mktemp "$store/.vox-runtime.XXXXXX")
trap 'rm -f -- "$download"' EXIT
curl --proto '=https' --proto-redir '=https' --fail --silent --show-error \
    --connect-timeout 15 --retry 2 --retry-max-time 120 --max-time 300 \
    --max-filesize 1073741824 --location --output "$download" "$url"
actual=$(digest "$download")
if [[ $actual != "$sha" ]]; then
    printf 'Vox snapshot runtime checksum mismatch; expected %s, got %s.\n' "$sha" "$actual" >&2
    exit 1
fi
mv -- "$download" "$archive"
printf '%s\n' "$archive"
