#!/usr/bin/env bash
# Format vox MCP tool output for the UI panel.
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

# Infer possessive pronoun for a voice name.
voice_pronoun() {
  local name
  name=$(echo "$1" | tr '[:upper:]' '[:lower:]')
  case "$name" in
    matilda|joanna|jessica|rachel|emily|aria|lily|charlotte|alice|domi|elli|freya|gigi|serena|glinda|mimi|nicole)
      echo "her" ;;
    adam|josh|sam|matthew|daniel|james|clyde|ethan|fin|harry|liam|patrick|thomas|michael|george|callum)
      echo "his" ;;
    *)
      echo "their" ;;
  esac
}

# Pick a random element from positional arguments (Bash 3.2 compatible).
pick_random() {
  local idx=$((RANDOM % $#))
  shift "$idx"
  echo "$1"
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

if [[ "$TOOL_NAME" == "notify" ]]; then
  MODE=$(echo "$RESULT" | jq -r '.notify.notify // empty' 2>/dev/null)
  case "$MODE" in
    y) MSG="♪ vox enabled" ;;
    n) MSG="♪ vox disabled" ;;
    c) MSG="♪ continuous mode on" ;;
    *) MSG="♪ notify updated" ;;
  esac
  emit "$MSG" "$RESULT"
  exit 0
fi

if [[ "$TOOL_NAME" == "speak" ]]; then
  MODE=$(echo "$RESULT" | jq -r '.speak // empty' 2>/dev/null)
  VOICE=$(echo "$RESULT" | jq -r '.voice // empty' 2>/dev/null)
  case "$MODE" in
    y)
      if [[ -n "$VOICE" ]]; then
        MSG="♪ ${VOICE} at the mic"
      else
        MSG="♪ voice on"
      fi
      ;;
    n) MSG="♪ chimes only" ;;
    *) MSG="♪ speak updated" ;;
  esac
  emit "$MSG" "$RESULT"
  exit 0
fi

if [[ "$TOOL_NAME" == "status" ]]; then
  VOICE=$(echo "$RESULT" | jq -r '.voice // "unknown"' 2>/dev/null)
  NOTIFY=$(echo "$RESULT" | jq -r '.notify // "?"' 2>/dev/null)
  emit "♪ ${VOICE} · notify=${NOTIFY}" "$RESULT"
  exit 0
fi

if [[ "$TOOL_NAME" == "who" ]]; then
  COUNT=$(echo "$RESULT" | jq -r '.all | length' 2>/dev/null || echo "?")
  PHRASES=(
    "♪ ${COUNT} agents standing by"
    "♪ here's who's around"
  )
  emit "$(pick_random "${PHRASES[@]}")" "$RESULT"
  exit 0
fi

if [[ "$TOOL_NAME" == "music" ]]; then
  STATUS=$(echo "$RESULT" | jq -r '.status // empty' 2>/dev/null)
  STYLE=$(echo "$RESULT" | jq -r '.style // empty' 2>/dev/null)
  case "$STATUS" in
    generating)
      if [[ -n "$STYLE" ]]; then
        PHRASES=("♪ dropping a ${STYLE} beat" "♪ ${STYLE} in the booth" "♪ cueing up ${STYLE}" "♪ ${STYLE} on deck")
      else
        PHRASES=("♪ next track loading" "♪ beat incoming" "♪ stepping up to the decks")
      fi
      ;;
    playing)
      NAME=$(echo "$RESULT" | jq -r '.name // empty' 2>/dev/null)
      if [[ -n "$NAME" ]]; then
        PHRASES=("♪ now spinning: ${NAME}" "♪ ${NAME} on the decks" "♪ ${NAME} on repeat")
      else
        PHRASES=("♪ and we're live" "♪ track on loop" "♪ the set continues")
      fi
      ;;
    stopped)
      PHRASES=("♪ fading out" "♪ set over" "♪ decks off" "♪ last call")
      ;;
    *)
      PHRASES=("♪ music updated")
      ;;
  esac
  emit "$(pick_random "${PHRASES[@]}")" "$RESULT"
  exit 0
fi

if [[ "$TOOL_NAME" == "music_play" ]]; then
  NAME=$(echo "$RESULT" | jq -r '.name // "track"' 2>/dev/null)
  PHRASES=("♪ bringing back ${NAME}" "♪ ${NAME} encore" "♪ ${NAME} from the vault")
  emit "$(pick_random "${PHRASES[@]}")" "$RESULT"
  exit 0
fi

if [[ "$TOOL_NAME" == "music_list" ]]; then
  COUNT=$(echo "$RESULT" | jq -r '.tracks | length' 2>/dev/null || echo "?")
  PHRASES=("♪ ${COUNT} track(s) in the crate" "♪ your crate: ${COUNT} track(s)")
  emit "$(pick_random "${PHRASES[@]}")" "$RESULT"
  exit 0
fi

if [[ "$TOOL_NAME" == "show_vox" ]]; then
  emit "♪ vox display updated" "$RESULT"
  exit 0
fi

# Generic message fallback: if the result has a "message" field, use it.
# Safety net for any tool that returns human-readable text but doesn't
# have a dedicated formatter above.
MSG_FIELD=$(echo "$RESULT" | jq -r '.message // empty' 2>/dev/null)
if [[ -n "$MSG_FIELD" ]]; then
  emit "$MSG_FIELD" "$RESULT"
  exit 0
fi

# Fallback: full output in panel
jq -n --arg r "$RESULT" '{
  hookSpecificOutput: {
    hookEventName: "PostToolUse",
    updatedMCPToolOutput: $r
  }
}'
