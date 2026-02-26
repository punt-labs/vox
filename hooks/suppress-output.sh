#!/usr/bin/env bash
# Format tts MCP tool output for the UI panel.
#
# Two-channel display (see punt-kit/patterns/two-channel-display.md):
#   updatedMCPToolOutput  → compact panel line (♪ prefix, max 80 cols)
#   additionalContext     → full JSON for the model to reference
#
# No `set -euo pipefail` — hooks must degrade gracefully on
# malformed input rather than failing the tool call.

INPUT=$(cat)
TOOL=$(echo "$INPUT" | jq -r '.tool_name')
TOOL_NAME="${TOOL##*__}"
RESULT=$(echo "$INPUT" | jq -r '.tool_response' | jq -r '.result // .')

# Extract common fields from single-result JSON.
extract_meta() {
  local data="$1"
  VOICE=$(echo "$data" | jq -r '.voice // empty' 2>/dev/null)
  PROVIDER=$(echo "$data" | jq -r '.provider // empty' 2>/dev/null)
}

# Truncate text to fit within 80 columns alongside prefix/suffix.
truncate() {
  local text="$1" max="$2"
  if [[ ${#text} -gt $max ]]; then
    echo "${text:0:$max}..."
  else
    echo "$text"
  fi
}

emit() {
  local summary="$1" ctx="$2"
  jq -n --arg summary "$summary" --arg ctx "$ctx" '{
    hookSpecificOutput: {
      hookEventName: "PostToolUse",
      updatedMCPToolOutput: $summary,
      additionalContext: $ctx
    }
  }'
}

if [[ "$TOOL_NAME" == "speak" ]]; then
  TEXT=$(echo "$RESULT" | jq -r '.text // empty' 2>/dev/null || echo "$RESULT")
  extract_meta "$RESULT"
  PREVIEW=$(truncate "$TEXT" 40)
  SUFFIX=""
  [[ -n "$VOICE" ]] && SUFFIX=" — $VOICE"
  [[ -n "$PROVIDER" ]] && SUFFIX="$SUFFIX ($PROVIDER)"
  emit "♪ \"$PREVIEW\"$SUFFIX" "$RESULT"
  exit 0
fi

if [[ "$TOOL_NAME" == "chorus" ]]; then
  COUNT=$(echo "$RESULT" | jq -r 'length' 2>/dev/null || echo "?")
  FIRST=$(echo "$RESULT" | jq -r '.[0]' 2>/dev/null)
  extract_meta "$FIRST"
  SUFFIX=""
  [[ -n "$VOICE" ]] && SUFFIX=" — $VOICE"
  [[ -n "$PROVIDER" ]] && SUFFIX="$SUFFIX ($PROVIDER)"
  emit "♪♪ $COUNT tracks$SUFFIX" "$RESULT"
  exit 0
fi

if [[ "$TOOL_NAME" == "duet" ]]; then
  TEXT=$(echo "$RESULT" | jq -r '.text // empty' 2>/dev/null || echo "$RESULT")
  extract_meta "$RESULT"
  PREVIEW=$(truncate "$TEXT" 40)
  SUFFIX=""
  [[ -n "$VOICE" ]] && SUFFIX=" — $VOICE"
  [[ -n "$PROVIDER" ]] && SUFFIX="$SUFFIX ($PROVIDER)"
  emit "♪ \"$PREVIEW\"$SUFFIX" "$RESULT"
  exit 0
fi

if [[ "$TOOL_NAME" == "ensemble" ]]; then
  COUNT=$(echo "$RESULT" | jq -r 'length' 2>/dev/null || echo "?")
  FIRST=$(echo "$RESULT" | jq -r '.[0]' 2>/dev/null)
  extract_meta "$FIRST"
  SUFFIX=""
  [[ -n "$VOICE" ]] && SUFFIX=" — $VOICE"
  [[ -n "$PROVIDER" ]] && SUFFIX="$SUFFIX ($PROVIDER)"
  emit "♪♪ $COUNT pairs$SUFFIX" "$RESULT"
  exit 0
fi

# Fallback: full output in panel
jq -n --arg r "$RESULT" '{
  hookSpecificOutput: {
    hookEventName: "PostToolUse",
    updatedMCPToolOutput: $r
  }
}'
