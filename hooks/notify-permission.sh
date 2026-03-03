#!/usr/bin/env bash
# Notification hook: permission-prompt and idle-prompt audio alerts.
#
# Runs async — does not block the permission dialog. Calls the vox CLI
# directly (not MCP) because hooks cannot invoke MCP tools.
#
# When speak=n, plays a chime instead of spoken words.
#
# No `set -euo pipefail` — hooks must degrade gracefully.

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=hooks/state.sh
source "$SCRIPT_DIR/state.sh"

INPUT=$(cat)
NOTIFY=$(read_notify)

# Not enabled — exit silently
if [[ "$NOTIFY" == "n" ]]; then
  exit 0
fi

SPEAK=$(read_speak)
NOTIFICATION_TYPE=$(echo "$INPUT" | jq -r '.notification_type // "unknown"')
MESSAGE=$(echo "$INPUT" | jq -r '.message // "Needs your attention"')

# Chime mode: play attention chime via flock-serialized queue.
if [[ "$SPEAK" == "n" ]]; then
  CHIME="$SCRIPT_DIR/../assets/chime_prompt.mp3"
  if [[ -f "$CHIME" ]]; then
    enqueue_audio "$CHIME"
  fi
  exit 0
fi

# Phrase pools — randomly selected so the voice doesn't repeat itself.
PERMISSION_PHRASES=(
  "Needs your approval."
  "Quick approval needed."
  "Need a green light here."
  "Got a question for you."
  "Your call on this one."
  "Mind taking a look?"
  "Waiting on your go-ahead."
)

IDLE_PHRASES=(
  "Waiting for your input."
  "Ready when you are."
  "Over to you."
  "Standing by."
  "Your turn."
  "What do you think?"
  "Need your thoughts on this."
)

# Voice mode: synthesize and play a short announcement
case "$NOTIFICATION_TYPE" in
  permission_prompt)
    TEXT=$(pick_random "${PERMISSION_PHRASES[@]}")
    ;;
  idle_prompt)
    TEXT=$(pick_random "${IDLE_PHRASES[@]}")
    ;;
  *)
    TEXT="Notification: ${MESSAGE:0:80}"
    ;;
esac

# Voice mode: synthesize to temp file and play via flock queue.
# macOS mktemp requires X's at the end of the template (no suffix allowed).
TMPDIR=$(mktemp -d /tmp/vox_notify_XXXXXX)
OUTPUT="$TMPDIR/notify.mp3"
VOICE=$(_read_field "voice")
if command -v vox &>/dev/null; then
  VOICE_ARGS=()
  if [[ -n "$VOICE" ]]; then
    VOICE_ARGS=(--voice "$VOICE")
  fi
  vox synthesize "$TEXT" "${VOICE_ARGS[@]}" -o "$OUTPUT" >/dev/null 2>&1
  if [[ -f "$OUTPUT" && -s "$OUTPUT" ]]; then
    play_audio_blocking "$OUTPUT"
  fi
fi
rm -rf "$TMPDIR"

exit 0
