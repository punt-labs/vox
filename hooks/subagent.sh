#!/usr/bin/env bash
# SubagentStart/SubagentStop — pipe stdin to the vox subprocess.
# Business logic lives in src/punt_vox/hooks.py.
_stdin=$(cat)

# Claude Code passes cwd and hook_event_name in JSON stdin, not as env vars.
if command -v jq >/dev/null 2>&1; then
  _cwd=$(printf '%s' "$_stdin" | jq -r '.cwd // empty' 2>/dev/null)
  _event=$(printf '%s' "$_stdin" | jq -r '.hook_event_name // empty' 2>/dev/null)
else
  _cwd=$(printf '%s' "$_stdin" | grep -oE '"cwd"[[:space:]]*:[[:space:]]*"[^"]*"' | head -1 | sed 's/.*:[[:space:]]*"//;s/"//')
  _event=$(printf '%s' "$_stdin" | grep -oE '"hook_event_name"[[:space:]]*:[[:space:]]*"[^"]*"' | head -1 | sed 's/.*:[[:space:]]*"//;s/"//')
fi
[[ -n "$_cwd" ]] || _cwd="$PWD"
[[ -f "${_cwd}/.punt-labs/vox/vox.md" ]] || [[ -f "${_cwd}/.punt-labs/vox/vox.local.md" ]] || exit 0

# Warnings ship to vox.log via the daemon; hook stderr is discarded by Claude Code.
case "${_event}" in
  SubagentStop)  printf '%s' "$_stdin" | vox hook subagent-stop 2>/dev/null || true ;;
  SubagentStart) printf '%s' "$_stdin" | vox hook subagent-start 2>/dev/null || true ;;
  *) ;;  # unknown event — do nothing
esac
