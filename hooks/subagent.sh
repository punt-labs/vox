#!/usr/bin/env bash
# SubagentStart/SubagentStop — thin dispatcher.
# Detects event from HOOK_EVENT_NAME env var.
# Business logic lives in src/punt_vox/hooks.py.
if _git_common_dir=$(git rev-parse --git-common-dir 2>/dev/null) && [[ -n "$_git_common_dir" ]]; then
  _repo_root=$(realpath "${_git_common_dir}/.." 2>/dev/null || echo ".")
else
  _repo_root="."
fi
[[ -f "${_repo_root}/.vox/config.md" ]] || exit 0

case "${HOOK_EVENT_NAME:-}" in
  SubagentStop) vox hook subagent-stop 2>/dev/null || true ;;
  *)            vox hook subagent-start 2>/dev/null || true ;;
esac
