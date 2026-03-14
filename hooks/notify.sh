#!/usr/bin/env bash
# Stop hook — daemon-first, fallback to subprocess.
# Business logic lives in src/punt_vox/hooks.py (handle_stop).
if _git_common_dir=$(git rev-parse --git-common-dir 2>/dev/null) && [[ -n "$_git_common_dir" ]]; then
  _repo_root=$(realpath "${_git_common_dir}/.." 2>/dev/null || echo ".")
else
  _repo_root="."
fi
[[ -f "${_repo_root}/.vox/config.md" ]] || exit 0

# Buffer stdin so fallback still has data if daemon relay fails.
_stdin=$(cat)

# Daemon relay (~15ms) — fall back to subprocess (~500ms)
_token_file="${HOME}/.punt-vox/serve.token"
if command -v mcp-proxy >/dev/null 2>&1 && [[ -f "$_token_file" ]]; then
  _token=$(cat "$_token_file")
  _encoded_dir=$(python3 -c "import urllib.parse,sys;print(urllib.parse.quote(sys.argv[1]))" "$_repo_root" 2>/dev/null || printf '%s' "$_repo_root")
  echo "$_stdin" | mcp-proxy "ws://localhost:8421/hook?config_dir=${_encoded_dir}&token=${_token}" --hook Stop 2>/dev/null && exit 0
fi
echo "$_stdin" | vox hook stop 2>/dev/null || true
