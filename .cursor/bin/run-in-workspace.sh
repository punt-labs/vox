#!/usr/bin/env bash
# Run a command with cwd set to the Cursor workspace root.
# Cursor may pass workspaceFolder as ~/path; expand before cd.
set -euo pipefail
root="$(eval echo "$1")"
cd "$root"
shift
exec "$@"
