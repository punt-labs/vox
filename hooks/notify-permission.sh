#!/usr/bin/env bash
# Notification hook: permission-prompt and idle-prompt audio alerts.
#
# Runs async — does not block the permission dialog. Calls the tts CLI
# directly (not MCP) because hooks cannot invoke MCP tools.
#
# When speak=n, plays a chime instead of spoken words.
#
# No `set -euo pipefail` — hooks must degrade gracefully.

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
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

# Chime mode: play attention chime
if [[ "$SPEAK" == "n" ]]; then
  CHIME="$SCRIPT_DIR/../assets/chime_prompt.mp3"
  if [[ -f "$CHIME" ]]; then
    afplay "$CHIME" &
  fi
  exit 0
fi

# Voice mode: synthesize and play a short announcement
case "$NOTIFICATION_TYPE" in
  permission_prompt)
    TEXT="Needs your approval."
    ;;
  idle_prompt)
    TEXT="Waiting for your input."
    ;;
  *)
    TEXT="Notification: ${MESSAGE:0:80}"
    ;;
esac

# Voice mode: synthesize to temp file and play.
# The CLI doesn't have --ephemeral/--auto-play, so we handle it here.
OUTPUT=$(mktemp /tmp/tts_notify_XXXXXX.mp3)
if command -v tts &>/dev/null; then
  tts synthesize "$TEXT" -o "$OUTPUT" 2>/dev/null
  if [[ -f "$OUTPUT" && -s "$OUTPUT" ]]; then
    afplay "$OUTPUT" 2>/dev/null
  fi
  rm -f "$OUTPUT"
fi

exit 0
