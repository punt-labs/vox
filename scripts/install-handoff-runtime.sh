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
    Darwin/arm64) platform=darwin-arm64 ;;
    *) echo 'No Vox snapshot runtime is published for this platform.' >&2; exit 1 ;;
esac

pin=$(uv run --no-project --with 'pyyaml==6.0.3' python - "$sidecar" "$platform" <<'PY'
import re
import sys

import yaml

with open(sys.argv[1], encoding="utf-8") as sidecar:
    platforms = yaml.safe_load(sidecar)["checks"]["full-gate"]["runtime"]["platforms"]
platform = sys.argv[2]
if platform not in platforms:
    sys.exit(f"No Vox snapshot runtime is pinned for {platform}.")
entry = platforms[platform]
url, sha = entry["url"], entry["sha256"]
if not re.fullmatch(r"[0-9a-f]{64}", sha) or not re.fullmatch(
    r"https://github\.com/punt-labs/vox/releases/download/"
    r"handoff-runtime-v[1-9][0-9]*/vox-handoff-" + re.escape(platform) + r"\.tar\.gz",
    url,
):
    sys.exit(f"Invalid Vox runtime pin for {platform}.")
print(url, sha)
PY
)
read -r url sha <<< "$pin"

store=$HOME/.punt-labs/ethos/runtimes
archive=$store/$sha.tar.gz
mkdir -p -- "$store"
matches_pin() {
    local checksum
    checksum=$(shasum -a 256 -- "$1") || return 1
    [[ ${checksum%% *} == "$sha" ]]
}

if [[ ! -f $archive ]] || ! matches_pin "$archive"; then
    download=$(mktemp "$store/.vox-runtime.XXXXXX")
    trap 'rm -f -- "$download"' EXIT
    curl --proto '=https' --proto-redir '=https' --fail --silent --show-error \
        --location --output "$download" "$url"
    if ! matches_pin "$download"; then
        echo 'Vox snapshot runtime checksum mismatch; not installed.' >&2
        exit 1
    fi
    mv -- "$download" "$archive"
fi
printf '%s\n' "$archive"
