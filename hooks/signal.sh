#!/usr/bin/env bash
# PostToolUse hook: accumulate vibe signals from Bash tool execution.
#
# Appends a signal token to vibe_signals in .tts/config.md.
# Signals serve two purposes: vibe tag selection (auto mode) and
# stop hook gating (notify=y/c skips recap when no signals present).
#
# No `set -euo pipefail` — hooks must degrade gracefully.

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=hooks/state.sh
source "$SCRIPT_DIR/state.sh"

# Fast gate: no config = not a TTS-enabled project
[[ -f "$TTS_STATE_FILE" ]] || exit 0

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
elif echo "$OUTPUT" | grep -qiE '^\[.+\] .+|^create mode'; then
  SIGNAL="git-commit"
elif echo "$OUTPUT" | grep -qiE 'pull/[0-9]+|created pull request'; then
  SIGNAL="pr-created"
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
  sed "s|^vibe_signals:.*|vibe_signals: \"${NEW}\"|" "$TTS_STATE_FILE" \
    > "${TTS_STATE_FILE}.tmp" && mv "${TTS_STATE_FILE}.tmp" "$TTS_STATE_FILE"
else
  # Insert before the LAST --- only (not the opening fence).
  awk -v field="vibe_signals: \"${NEW}\"" '
    { lines[NR] = $0; if ($0 == "---") last = NR }
    END {
      if (last > 1) {
        for (i=1; i<=NR; i++) {
          if (i == last) print field
          print lines[i]
        }
      } else {
        for (i=1; i<=NR; i++) print lines[i]
      }
    }
  ' "$TTS_STATE_FILE" > "${TTS_STATE_FILE}.tmp" && mv "${TTS_STATE_FILE}.tmp" "$TTS_STATE_FILE"
fi

exit 0
