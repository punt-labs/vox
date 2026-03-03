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
# shellcheck source=hooks/state.sh
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

# No signals accumulated = no meaningful work to summarize.
# Prevents empty recaps after trivial commands like /say hello.
SIGNALS=$(read_vibe_signals)
if [[ -z "$SIGNALS" ]]; then
  exit 0
fi

SPEAK=$(read_speak)

# Chime mode: play audio tone via flock-serialized queue, let Claude stop.
if [[ "$SPEAK" == "n" ]]; then
  CHIME="$SCRIPT_DIR/../assets/chime_done.mp3"
  if [[ -f "$CHIME" ]]; then
    enqueue_audio "$CHIME"
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

REASON=$(pick_random "${SUMMARY_PHRASES[@]}")

# Voice mode: block the stop, ask Claude to summarize and speak.
# Vibe data (vibe_mode, vibe_signals, vibe) lives in
# .vox/config.md — Claude reads it via the Read file tool, while
# set_config is used only for writing. Keeping the reason clean
# avoids debug data leaking into the user-visible hook error display.
jq -n --arg reason "$REASON" '{
  decision: "block",
  reason: $reason
}'
