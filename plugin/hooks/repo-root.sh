#!/usr/bin/env bash

vox_hook_git_root() {
  local root
  root=$(git -C "$1" rev-parse --show-toplevel 2>/dev/null) && [[ -n "$root" ]] ||
    return 1
  printf '%s\n' "$root"
}

vox_hook_marker_root() {
  local dir=$1
  while [[ ! -f "$dir/.punt-labs/vox/enabled" ]]; do
    [[ "$dir" != / ]] || return 1
    dir=${dir%/*}
    dir=${dir:-/}
  done
  printf '%s\n' "$dir"
}

vox_hook_repo_root() {
  local dir=${1:-$PWD} root
  if vox_hook_git_root "$dir"; then
    return
  fi

  # A relative path can get stuck at "." instead of reaching "/".
  if [[ "$dir" != /* ]]; then
    local absolute="$PWD/$dir" logical
    dir="$absolute"
    while ! root=$(cd -P -- "$dir" 2>/dev/null && pwd -P); do
      [[ "$dir" != / ]] || return 1
      dir=${dir%/*}
      dir=${dir:-/}
    done
    if [[ "$dir" != "$absolute" ]] && vox_hook_git_root "$root"; then
      return
    fi
    if logical=$(cd -- "$dir" 2>/dev/null && pwd) &&
      [[ "$logical" != "$root" ]] && vox_hook_marker_root "$logical"; then
      return
    fi
    dir="$root"
  fi
  vox_hook_marker_root "$dir" || printf '/\n'
}
