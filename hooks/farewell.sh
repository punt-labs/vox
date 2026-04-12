#!/usr/bin/env bash
# SessionEnd — daemon-first, fallback to subprocess.
# Business logic lives in src/punt_vox/hooks.py (handle_session_end).
if _git_common_dir=$(git rev-parse --git-common-dir 2>/dev/null) && [[ -n "$_git_common_dir" ]]; then
  _repo_root=$(realpath "${_git_common_dir}/.." 2>/dev/null || echo ".")
else
  _repo_root="."
fi
[[ -f "${_repo_root}/.punt-labs/vox/config.md" ]] || [[ -f "${_repo_root}/.vox/config.md" ]] || exit 0

# Buffer stdin so fallback still has data if daemon relay fails.
_stdin=$(cat)

# Daemon relay (~15ms) — fall back to subprocess (~500ms)
_state_dir="${HOME}/.punt-labs/vox"
_err_log="${_state_dir}/logs/hook-errors.log"
mkdir -p "${_state_dir}/logs" 2>/dev/null
_token_file="${_state_dir}/serve.token"
_port_file="${_state_dir}/serve.port"
if command -v mcp-proxy >/dev/null 2>&1 && [[ -s "$_token_file" ]] && [[ -s "$_port_file" ]]; then
  _token=$(cat "$_token_file")
  _port=$(cat "$_port_file")
  _encoded_dir=$(python3 -c "import urllib.parse,sys;print(urllib.parse.quote(sys.argv[1]))" "$_repo_root" 2>/dev/null || printf '%s' "$_repo_root")
  echo "$_stdin" | mcp-proxy "ws://localhost:${_port}/hook?config_dir=${_encoded_dir}&token=${_token}" --hook --async SessionEnd 2>>"${_err_log}" && exit 0
fi
echo "$_stdin" | vox hook session-end 2>>"${_err_log}" || true
