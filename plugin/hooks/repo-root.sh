#!/usr/bin/env bash

vox_hook_repo_root() {
  local dir=${1:-$PWD} root
  if root=$(git -C "$dir" rev-parse --show-toplevel 2>/dev/null) && [[ -n "$root" ]]; then
    printf '%s\n' "$root"
    return
  fi

  # A relative path can get stuck at "." instead of reaching "/".
  if [[ "$dir" != /* ]]; then
    dir=$(cd -- "$PWD/$dir" 2>/dev/null && pwd) || return 1
  fi
  while [[ "$dir" != / && ! -f "$dir/.punt-labs/vox/enabled" ]]; do
    dir=${dir%/*}
    dir=${dir:-/}
  done
  printf '%s\n' "$dir"
}
