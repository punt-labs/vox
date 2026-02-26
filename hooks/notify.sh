#!/usr/bin/env bash
# Stop hook: task-completion notification.
#
# When notifications are enabled (/notify y or /notify c), this hook
# blocks Claude from stopping and asks it to generate a brief spoken
# summary via the TTS synthesize tool.
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

# Voice mode: block the stop, ask Claude to summarize and speak
jq -n '{
  decision: "block",
  reason: "The user has /notify enabled. You MUST: (1) Write a 1-2 sentence summary of what you just completed. (2) Call the TTS synthesize tool with that summary text, ephemeral=true, auto_play=true. (3) Do nothing else — no extra commentary, no questions. Just the summary and the tool call."
}'
