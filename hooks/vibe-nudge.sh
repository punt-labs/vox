#!/usr/bin/env bash
# UserPromptSubmit — pipe stdin to the vox subprocess and pass its stdout through.
# Business logic lives in src/punt_vox/hooks.py (handle_vibe_nudge). This hook is
# SYNCHRONOUS (never async): only synchronous UserPromptSubmit stdout is injected
# as additionalContext. It is non-blocking: it only injects context.
_stdin=$(cat)
if command -v jq >/dev/null 2>&1; then
  _cwd=$(printf '%s' "$_stdin" | jq -r '.cwd // empty' 2>/dev/null)
else
  _cwd=$(printf '%s' "$_stdin" | grep -oE '"cwd"[[:space:]]*:[[:space:]]*"[^"]*"' | head -1 | sed 's/.*:[[:space:]]*"//;s/"//')
fi
[[ -n "$_cwd" ]] || _cwd="$PWD"
[[ -f "${_cwd}/.punt-labs/vox/vox.md" ]] || [[ -f "${_cwd}/.punt-labs/vox/vox.local.md" ]] || exit 0

# Warnings ship to vox.log via the daemon; hook stderr is discarded by Claude Code.
printf '%s' "$_stdin" | vox hook vibe-nudge 2>/dev/null || true
