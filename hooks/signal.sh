#!/usr/bin/env bash
# PostToolUse (Bash) — pipe stdin to the vox subprocess.
# Business logic lives in src/punt_vox/hooks.py (handle_post_bash).
_stdin=$(cat)
if command -v jq >/dev/null 2>&1; then
  _cwd=$(printf '%s' "$_stdin" | jq -r '.cwd // empty' 2>/dev/null)
else
  _cwd=$(printf '%s' "$_stdin" | grep -oE '"cwd"[[:space:]]*:[[:space:]]*"[^"]*"' | head -1 | sed 's/.*:[[:space:]]*"//;s/"//')
fi
[[ -n "$_cwd" ]] || _cwd="$PWD"
[[ -f "${_cwd}/.punt-labs/vox/vox.md" ]] || [[ -f "${_cwd}/.punt-labs/vox/vox.local.md" ]] || exit 0

_err_log="${HOME}/.punt-labs/vox/logs/hook-errors.log"
mkdir -p "${HOME}/.punt-labs/vox/logs" 2>/dev/null
printf '%s' "$_stdin" | vox hook post-bash 2>>"${_err_log}" || true
