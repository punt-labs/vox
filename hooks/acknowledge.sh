#!/usr/bin/env bash
# UserPromptSubmit — thin dispatcher.
# Business logic lives in src/punt_vox/hooks.py (handle_user_prompt_submit).
if _git_common_dir=$(git rev-parse --git-common-dir 2>/dev/null) && [[ -n "$_git_common_dir" ]]; then
  _repo_root=$(realpath "${_git_common_dir}/.." 2>/dev/null || echo ".")
else
  _repo_root="."
fi
[[ -f "${_repo_root}/.vox/config.md" ]] || exit 0
vox hook user-prompt-submit 2>/dev/null || true
