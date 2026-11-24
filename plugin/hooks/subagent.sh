#!/usr/bin/env bash
# SubagentStart/SubagentStop — pipe stdin to the vox subprocess.
# Business logic lives in src/punt_vox/hooks.py.
_stdin=$(cat)

# Claude Code passes cwd and hook_event_name in JSON stdin, not as env vars.
# shellcheck source=plugin/hooks/payload.sh
source "$(dirname "${BASH_SOURCE[0]}")/payload.sh" || exit 0
_cwd=$(printf '%s' "$_stdin" | vox_hook_payload_field cwd)
_event=$(printf '%s' "$_stdin" | vox_hook_payload_field hook_event_name)
[[ -n "$_cwd" ]] || _cwd="$PWD"

# Per-repo opt-in: silent unless this repo ran `vox enable` and vox is installed.
# shellcheck source=plugin/hooks/repo-root.sh
source "$(dirname "${BASH_SOURCE[0]}")/repo-root.sh" || exit 0
_repo_root=$(vox_hook_repo_root "$_cwd") || exit 0
[ -f "$_repo_root/.punt-labs/vox/enabled" ] || exit 0
command -v vox >/dev/null 2>&1 || exit 0

# Warnings ship to vox.log via the daemon; hook stderr is discarded by Claude Code.
case "${_event}" in
  SubagentStop)  printf '%s' "$_stdin" | vox hook subagent-stop 2>/dev/null || true ;;
  SubagentStart) printf '%s' "$_stdin" | vox hook subagent-start 2>/dev/null || true ;;
  *) ;;  # unknown event — do nothing
esac
