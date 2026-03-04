#!/usr/bin/env bash
# Format vox MCP tool output for the UI panel.
#
# Two-channel display (see punt-kit/patterns/two-channel-display.md):
#   updatedMCPToolOutput  → compact panel line (♪ prefix, max 80 cols)
#   additionalContext     → full JSON for the model to reference
#
# No `set -euo pipefail` — hooks must degrade gracefully on
# malformed input rather than failing the tool call.

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=hooks/state.sh
source "$SCRIPT_DIR/state.sh"

INPUT=$(cat)
TOOL=$(echo "$INPUT" | jq -r '.tool_name')
TOOL_NAME="${TOOL##*__}"
RESULT=$(echo "$INPUT" | jq -r '
  def unpack: if type == "string" then (fromjson? // .) else . end;
  if (.tool_response | type) == "array" then
    (.tool_response[0].text // "" | unpack)
  else
    (.tool_response | unpack)
  end
  | if type == "object" and has("result") then (.result | unpack) else . end
')

# Extract voice name from single-result JSON.
# Falls back to "the voice" when missing or empty.
extract_voice() {
  local data="$1"
  VOICE=$(echo "$data" | jq -r '.[0].voice // .voice // empty' 2>/dev/null)
  [[ -z "$VOICE" ]] && VOICE="the voice"
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

# Error guard: if the result contains an error field, surface it directly.
ERROR_MSG=$(echo "$RESULT" | jq -r '.error // empty' 2>/dev/null)
if [[ -n "$ERROR_MSG" ]]; then
  emit "♪ error: ${ERROR_MSG}" "$RESULT"
  exit 0
fi

if [[ "$TOOL_NAME" == "unmute" ]]; then
  extract_voice "$RESULT"
  PRONOUN=$(voice_pronoun "$VOICE")
  PHRASES=(
    "♪ ${VOICE} has spoken"
    "♪ ${VOICE} said ${PRONOUN} piece"
    "♪ ${VOICE} delivered"
    "♪ heard from ${VOICE}"
  )
  emit "$(pick_random "${PHRASES[@]}")" "$RESULT"
  exit 0
fi

if [[ "$TOOL_NAME" == "record" ]]; then
  COUNT=$(echo "$RESULT" | jq -r 'length' 2>/dev/null || echo "?")
  extract_voice "$RESULT"
  PHRASES=(
    "♪ ${VOICE} recorded ${COUNT} track(s)"
    "♪ ${COUNT} track(s) saved"
  )
  emit "$(pick_random "${PHRASES[@]}")" "$RESULT"
  exit 0
fi

if [[ "$TOOL_NAME" == "vibe" ]]; then
  VIBE_DATA=$(echo "$RESULT" | jq -r '.vibe // empty' 2>/dev/null)
  if [[ -n "$VIBE_DATA" ]]; then
    VIBE_TAGS=$(echo "$VIBE_DATA" | jq -r '.vibe_tags // empty' 2>/dev/null)
    if [[ -n "$VIBE_TAGS" ]]; then
      MSG="♪ vibe shifted to ${VIBE_TAGS}"
    else
      MOOD=$(echo "$VIBE_DATA" | jq -r '.vibe // empty' 2>/dev/null)
      if [[ -n "$MOOD" ]]; then
        MSG="♪ mood: ${MOOD}"
      else
        MSG="♪ vibe updated"
      fi
    fi
  else
    MSG="♪ vibe updated"
  fi
  emit "$MSG" "$RESULT"
  exit 0
fi

if [[ "$TOOL_NAME" == "who" ]]; then
  COUNT=$(echo "$RESULT" | jq -r '.all | length' 2>/dev/null || echo "?")
  PHRASES=(
    "♪ ${COUNT} voices checked in"
    "♪ here's who's around"
  )
  emit "$(pick_random "${PHRASES[@]}")" "$RESULT"
  exit 0
fi

# Fallback: full output in panel
jq -n --arg r "$RESULT" '{
  hookSpecificOutput: {
    hookEventName: "PostToolUse",
    updatedMCPToolOutput: $r
  }
}'
