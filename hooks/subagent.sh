#!/usr/bin/env bash
# SubagentStart/SubagentStop — thin dispatcher.
# Reads hook_event_name from JSON stdin to determine event type.
# Business logic lives in src/punt_vox/hooks.py.
if _git_common_dir=$(git rev-parse --git-common-dir 2>/dev/null) && [[ -n "$_git_common_dir" ]]; then
  _repo_root=$(realpath "${_git_common_dir}/.." 2>/dev/null || echo ".")
else
  _repo_root="."
fi
[[ -f "${_repo_root}/.vox/config.md" ]] || exit 0

# Claude Code passes hook_event_name in JSON stdin, not as an env var.
# Read stdin once and extract the event name with jq (or grep fallback).
_stdin=$(cat)
if command -v jq >/dev/null 2>&1; then
  _event=$(printf '%s' "$_stdin" | jq -r '.hook_event_name // empty' 2>/dev/null)
else
  _event=$(printf '%s' "$_stdin" | grep -oE '"hook_event_name"[[:space:]]*:[[:space:]]*"[^"]*"' | head -1 | sed 's/.*:[[:space:]]*"//;s/"//')
fi

case "${_event}" in
  SubagentStop)  echo "$_stdin" | vox hook subagent-stop 2>/dev/null || true ;;
  SubagentStart) echo "$_stdin" | vox hook subagent-start 2>/dev/null || true ;;
  *) ;;  # unknown event — do nothing
esac
