#!/usr/bin/env bash
# PostToolUse hook: accumulate vibe signals from Bash tool execution.
#
# Appends a signal token to vibe_signals in .tts/config.md when
# vibe_mode=auto. Fast gate: exits immediately if config doesn't
# exist or mode isn't auto.
#
# No `set -euo pipefail` — hooks must degrade gracefully.

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "$SCRIPT_DIR/state.sh"

# Fast gate: no config = not a TTS-enabled project
[[ -f "$TTS_STATE_FILE" ]] || exit 0

# Fast gate: only accumulate in auto mode
VIBE_MODE=$(read_vibe_mode)
[[ "$VIBE_MODE" == "auto" ]] || exit 0

INPUT=$(cat)
EXIT_CODE=$(echo "$INPUT" | jq -r '.tool_response.exit_code // empty' 2>/dev/null)
OUTPUT=$(echo "$INPUT" | jq -r '.tool_response.stdout // empty' 2>/dev/null | head -c 500)

# Classify the signal from output patterns
SIGNAL=""

if echo "$OUTPUT" | grep -qiE 'passed|tests? ok|✓.*passed'; then
  SIGNAL="tests-pass"
elif echo "$OUTPUT" | grep -qiE 'FAILED|AssertionError|ERRORS?\b'; then
  SIGNAL="tests-fail"
elif echo "$OUTPUT" | grep -qiE 'Found [0-9]+ error'; then
  SIGNAL="lint-fail"
elif echo "$OUTPUT" | grep -qiE '0 errors'; then
  SIGNAL="lint-pass"
elif echo "$OUTPUT" | grep -qi 'CONFLICT'; then
  SIGNAL="merge-conflict"
elif echo "$OUTPUT" | grep -qiE 'Everything up-to-date|->.*main'; then
  SIGNAL="git-push-ok"
fi

# Generic failure if nothing matched but exit code is non-zero
if [[ -z "$SIGNAL" && "$EXIT_CODE" != "0" && -n "$EXIT_CODE" ]]; then
  SIGNAL="cmd-fail"
fi

[[ -z "$SIGNAL" ]] && exit 0

# Append signal with timestamp to vibe_signals field.
TIMESTAMP=$(date +%H:%M)
TOKEN="${SIGNAL}@${TIMESTAMP}"

CURRENT=$(read_vibe_signals)
if [[ -n "$CURRENT" ]]; then
  NEW="${CURRENT},${TOKEN}"
else
  NEW="$TOKEN"
fi

# Write via sed: update existing field or insert before closing ---
# macOS sed -i requires '' extension argument.
if grep -q "^vibe_signals:" "$TTS_STATE_FILE" 2>/dev/null; then
  sed -i '' "s|^vibe_signals:.*|vibe_signals: \"${NEW}\"|" "$TTS_STATE_FILE"
else
  # Insert before the LAST --- only (not the opening fence).
  # Use awk: print all lines, but when we see the last --- replace it
  # with the new field + ---.
  awk -v field="vibe_signals: \"${NEW}\"" '
    { lines[NR] = $0; if ($0 == "---") last = NR }
    END { for (i=1; i<=NR; i++) {
      if (i == last) print field
      print lines[i]
    }}
  ' "$TTS_STATE_FILE" > "${TTS_STATE_FILE}.tmp" && mv "${TTS_STATE_FILE}.tmp" "$TTS_STATE_FILE"
fi

exit 0
