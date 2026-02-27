#!/usr/bin/env bash
# Format tts MCP tool output for the UI panel.
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
  VOICE=$(echo "$data" | jq -r '.voice // empty' 2>/dev/null)
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

if [[ "$TOOL_NAME" == "speak" ]]; then
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

if [[ "$TOOL_NAME" == "chorus" ]]; then
  COUNT=$(echo "$RESULT" | jq -r 'length' 2>/dev/null || echo "?")
  FIRST=$(echo "$RESULT" | jq -r '.[0]' 2>/dev/null)
  extract_voice "$FIRST"
  PHRASES=(
    "♪♪ ${VOICE} sang ${COUNT} tracks"
    "♪♪ ${COUNT} tracks from ${VOICE}"
    "♪♪ ${VOICE} performed ${COUNT} pieces"
  )
  emit "$(pick_random "${PHRASES[@]}")" "$RESULT"
  exit 0
fi

if [[ "$TOOL_NAME" == "duet" ]]; then
  extract_voice "$RESULT"
  PHRASES=(
    "♪ ${VOICE} paired them up"
    "♪ ${VOICE} stitched a duet"
    "♪ a duet from ${VOICE}"
  )
  emit "$(pick_random "${PHRASES[@]}")" "$RESULT"
  exit 0
fi

if [[ "$TOOL_NAME" == "ensemble" ]]; then
  COUNT=$(echo "$RESULT" | jq -r 'length' 2>/dev/null || echo "?")
  FIRST=$(echo "$RESULT" | jq -r '.[0]' 2>/dev/null)
  extract_voice "$FIRST"
  PHRASES=(
    "♪♪ ${VOICE} performed ${COUNT} pairs"
    "♪♪ ${COUNT} pairs from ${VOICE}"
    "♪♪ ${VOICE} delivered ${COUNT} duets"
  )
  emit "$(pick_random "${PHRASES[@]}")" "$RESULT"
  exit 0
fi

if [[ "$TOOL_NAME" == "set_config" ]]; then
  # Batch mode: updates dict present
  UPDATES=$(echo "$RESULT" | jq -r '.updates // empty' 2>/dev/null)
  if [[ -n "$UPDATES" ]]; then
    VIBE_TAGS=$(echo "$UPDATES" | jq -r '.vibe_tags // empty' 2>/dev/null)
    if [[ -n "$VIBE_TAGS" ]]; then
      MSG="♪ vibe shifted to ${VIBE_TAGS}"
    elif echo "$UPDATES" | jq -e 'has("vibe_tags")' >/dev/null 2>&1; then
      # vibe_tags present but empty → cleared
      MSG="♪ vibe cleared"
    else
      COUNT=$(echo "$UPDATES" | jq 'length' 2>/dev/null || echo "?")
      MSG="♪ config: ${COUNT} fields updated"
    fi
    emit "$MSG" "$RESULT"
    exit 0
  fi

  # Single mode: key/value
  KEY=$(echo "$RESULT" | jq -r '.key // empty' 2>/dev/null)
  VALUE=$(echo "$RESULT" | jq -r '.value // empty' 2>/dev/null)
  if [[ "$KEY" == "vibe_tags" ]]; then
    if [[ -z "$VALUE" ]]; then
      MSG="♪ vibe cleared"
    else
      MSG="♪ vibe shifted to ${VALUE}"
    fi
  elif [[ "$KEY" == "vibe_signals" ]]; then
    # Signal clearing is silent — no panel noise
    exit 0
  else
    MSG="♪ ${KEY} → ${VALUE}"
  fi
  emit "$MSG" "$RESULT"
  exit 0
fi

# Fallback: full output in panel
jq -n --arg r "$RESULT" '{
  hookSpecificOutput: {
    hookEventName: "PostToolUse",
    updatedMCPToolOutput: $r
  }
}'
