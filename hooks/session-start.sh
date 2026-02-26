#!/usr/bin/env bash
set -euo pipefail

PLUGIN_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SETTINGS="$HOME/.claude/settings.json"
COMMANDS_DIR="$HOME/.claude/commands"
TOOL_PATTERN="mcp__plugin_tts_tts__"

ACTIONS=()

# ── Deploy top-level commands if missing ──────────────────────────────
# Skip *-dev.md files — dev commands use plugin namespace (tts-dev:say-dev)
DEPLOYED=()
for cmd_file in "$PLUGIN_ROOT/commands/"*.md; do
  name="$(basename "$cmd_file")"
  [[ "$name" == *-dev.md ]] && continue
  dest="$COMMANDS_DIR/$name"
  if [[ ! -f "$dest" ]]; then
    mkdir -p "$COMMANDS_DIR"
    cp "$cmd_file" "$dest"
    DEPLOYED+=("/${name%.md}")
  fi
done
if [[ ${#DEPLOYED[@]} -gt 0 ]]; then
  ACTIONS+=("Deployed commands: ${DEPLOYED[*]}")
fi

# ── Allow MCP tools in user settings if not already allowed ──────────
if command -v jq &>/dev/null && [[ -f "$SETTINGS" ]]; then
  CHANGED=false

  if ! jq -e ".permissions.allow // [] | map(select(contains(\"$TOOL_PATTERN\"))) | length > 0" "$SETTINGS" >/dev/null 2>&1; then
    TMPFILE="$(mktemp)"
    jq '.permissions.allow = (.permissions.allow // []) + ["mcp__plugin_tts_tts__*"]' "$SETTINGS" > "$TMPFILE"
    mv "$TMPFILE" "$SETTINGS"
    CHANGED=true
  fi

  if [[ "$CHANGED" == "true" ]]; then
    ACTIONS+=("Auto-allowed tts MCP tools in permissions")
  fi
fi

# ── Notify Claude if anything was set up ─────────────────────────────
if [[ ${#ACTIONS[@]} -gt 0 ]]; then
  MSG="TTS plugin first-run setup complete."
  for action in "${ACTIONS[@]}"; do
    MSG="$MSG $action."
  done
  cat <<ENDJSON
{
  "hookSpecificOutput": {
    "hookEventName": "SessionStart",
    "additionalContext": "$MSG"
  }
}
ENDJSON
fi

exit 0
