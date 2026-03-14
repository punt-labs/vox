#!/usr/bin/env bash
# Stop hook — daemon-first, fallback to subprocess.
# Business logic lives in src/punt_vox/hooks.py (handle_stop).
if _git_common_dir=$(git rev-parse --git-common-dir 2>/dev/null) && [[ -n "$_git_common_dir" ]]; then
  _repo_root=$(realpath "${_git_common_dir}/.." 2>/dev/null || echo ".")
else
  _repo_root="."
fi
[[ -f "${_repo_root}/.vox/config.md" ]] || exit 0

# Daemon relay (~15ms) — fall back to subprocess (~500ms)
if command -v mcp-proxy >/dev/null 2>&1; then
  mcp-proxy "ws://localhost:8421/hook?config_dir=${_repo_root}" --hook Stop 2>/dev/null && exit 0
fi
vox hook stop 2>/dev/null || true
