#!/usr/bin/env bash
# Stop hook: task-completion notification.
#
# When notifications are enabled (/notify y or /notify c), this hook
# blocks Claude from stopping and asks it to generate a brief spoken
# summary via the TTS speak tool.
#
# Loop guard: stop_hook_active=true means Claude is already continuing
# from a previous Stop hook. Let it stop to prevent infinite loops.
#
# No `set -euo pipefail` — hooks must degrade gracefully on malformed
# input rather than failing the tool call.

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "$SCRIPT_DIR/state.sh"

INPUT=$(cat)
STOP_ACTIVE=$(echo "$INPUT" | jq -r '.stop_hook_active // false')
NOTIFY=$(read_notify)

# Not enabled — let Claude stop normally
if [[ "$NOTIFY" == "n" ]]; then
  exit 0
fi

# Already continuing from a previous Stop hook — let it stop
if [[ "$STOP_ACTIVE" == "true" ]]; then
  exit 0
fi

SPEAK=$(read_speak)

# Chime mode: play audio tone directly, let Claude stop
if [[ "$SPEAK" == "n" ]]; then
  CHIME="$SCRIPT_DIR/../assets/chime_done.mp3"
  if [[ -f "$CHIME" ]]; then
    afplay "$CHIME" &
  fi
  exit 0
fi

# Phrase pool — each phrase is playful for the user AND primes
# Claude to speak a summary (every phrase implies vocalization).
SUMMARY_PHRASES=(
  "♪ Speaking my thoughts..."
  "♪ Putting my thoughts into words..."
  "♪ Summing it up aloud..."
  "♪ Saying my piece..."
  "♪ Voicing my closing remarks..."
  "♪ Letting you hear how it went..."
  "♪ Telling you what I did..."
)

# Pick a random element from positional arguments (Bash 3.2 compatible).
pick_random() {
  local idx=$((RANDOM % $#))
  shift "$idx"
  echo "$1"
}

REASON=$(pick_random "${SUMMARY_PHRASES[@]}")

# Voice mode: block the stop, ask Claude to summarize and speak.
jq -n --arg reason "$REASON" '{
  decision: "block",
  reason: $reason
}'
