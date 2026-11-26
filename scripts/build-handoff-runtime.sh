#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 || ! -d $1 ]]; then
    printf 'usage: %s OUTPUT_DIRECTORY\n' "$0" >&2
    exit 2
fi

repo=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)
output=$(cd -- "$1" && pwd -P)
require_tool() {
    command -v "$1" || { printf 'Required tool not found: %s\n' "$1" >&2; exit 1; }
}
tar_tool=$(command -v gtar || require_tool tar)
if [[ $("$tar_tool" --version) != *'GNU tar'* ]]; then
    printf 'GNU tar is required (gtar on macOS).\n' >&2
    exit 1
fi

uv_tool=$(require_tool uv)
shellcheck_tool=$(require_tool shellcheck)
node_tool=$(require_tool node)
python_version=$(<"$repo/.python-version")
node_tool=$(env -i PATH=/usr/bin:/bin "$node_tool" -p 'process.execPath')
node_home=$(dirname -- "$(dirname -- "$node_tool")")
if [[ ! -f $node_home/lib/node_modules/npm/bin/npx-cli.js ]]; then
    printf 'Node must come from a distribution that bundles npm and npx.\n' >&2
    exit 1
fi

work=$(mktemp -d "$output/.vox-runtime.XXXXXX")
trap 'rm -rf -- "$work"' EXIT
runtime=$work/runtime
mkdir -p "$runtime/bin" "$runtime/node/bin" "$runtime/node/lib/node_modules" \
    "$runtime/cache/uv" "$runtime/cache/npm" "$work/home" "$work/tmp"
install -m 0755 "$uv_tool" "$runtime/bin/uv"
install -m 0755 "$shellcheck_tool" "$runtime/bin/shellcheck"
install -m 0755 "$node_tool" "$runtime/node/bin/node"
cp -R -P "$node_home/lib/node_modules/npm" "$runtime/node/lib/node_modules/npm"
ln -s ../lib/node_modules/npm/bin/npm-cli.js "$runtime/node/bin/npm"
ln -s ../lib/node_modules/npm/bin/npx-cli.js "$runtime/node/bin/npx"

isolated=(env -i "HOME=$work/home" "TMPDIR=$work/tmp"
    "PATH=$runtime/bin:$runtime/node/bin:/usr/bin:/bin" LC_ALL=C TZ=UTC PYTHONDONTWRITEBYTECODE=1)
python_env=("${isolated[@]}" "UV_PYTHON_INSTALL_DIR=$work/python" "UV_CACHE_DIR=$work/python-cache")
"${python_env[@]}" uv --no-config python install "$python_version" --no-bin >&2
python_tool=$("${python_env[@]}" uv --no-config python find --managed-python --no-project --system "$python_version")
python_home=$("${isolated[@]}" "$python_tool" -I -c 'import sys; print(sys.base_prefix)')
mv -- "$python_home" "$runtime/python"
python_tool=$runtime/python/bin/python3
if [[ ! -x $python_tool ]]; then
    printf 'Managed Python has no bin/python3: %s\n' "$python_home" >&2
    exit 1
fi
uv_env=("${isolated[@]}" "UV_PYTHON=$python_tool"
    UV_PYTHON_DOWNLOADS=never "UV_CACHE_DIR=$runtime/cache/uv"
    "UV_PROJECT_ENVIRONMENT=$work/venv" UV_LINK_MODE=copy)

# Seed only locked dependencies and the build backend, not the checkout's
# editable wheel. The gate creates that wheel at the actual snapshot path.
"${isolated[@]}" "$python_tool" -I -c '
import sys, tomllib
with open(sys.argv[1], "rb") as config:
    project = tomllib.load(config)
if set(project["build-system"]["requires"]) != set(project["dependency-groups"]["handoff-runtime"]):
    sys.exit("Update the handoff-runtime dependency group to match build-system.requires.")
' "$repo/pyproject.toml"
"${uv_env[@]}" uv --directory "$repo" sync --locked --group docs \
    --group handoff-runtime --no-install-project >&2

"${isolated[@]}" "npm_config_cache=$runtime/cache/npm" npm_config_ignore_scripts=true \
    npm_config_audit=false npm_config_fund=false npm_config_update_notifier=false \
    make -C "$repo" handoff-runtime-npm-cache >&2
rm -rf -- "$runtime/cache/npm/_logs"
rm -f -- "$runtime/cache/npm/_update-notifier-last-checked"
{
    "${isolated[@]}" uv --version
    "${isolated[@]}" "$python_tool" --version
    "${isolated[@]}" node --version
    "${isolated[@]}" npm --version
    "${isolated[@]}" shellcheck --version
} > "$runtime/versions.txt"

# Normalize archive headers; package-manager cache contents can change on a
# fresh fetch. Pin the digest of the published bytes, not a local rebuild.
LC_ALL=C "$tar_tool" --sort=name --format=gnu --mtime=@0 --numeric-owner \
    --owner=0 --group=0 --hard-dereference -C "$runtime" -cf - bin cache node python versions.txt \
    | gzip -n > "$work/runtime.tar.gz"
sha=$("$python_tool" -c '
import hashlib, sys
with open(sys.argv[1], "rb") as archive:
    print(hashlib.file_digest(archive, "sha256").hexdigest())
' "$work/runtime.tar.gz")
archive=$output/$sha.tar.gz
mv -- "$work/runtime.tar.gz" "$archive"
printf '%s\n' "$archive"
