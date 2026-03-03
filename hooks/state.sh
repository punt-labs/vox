#!/usr/bin/env bash
# Shared state reader for punt-vox hooks.
#
# All hooks source this file to read notification and speech state
# from .vox/config.md (per-project, YAML frontmatter).
#
# Usage:
#   source "$(dirname "$0")/state.sh"
#   if [[ "$(read_notify)" == "y" ]]; then ...

TTS_STATE_FILE=".vox/config.md"

# Read a YAML frontmatter field from the state file.
# Returns empty string if file doesn't exist or field not found.
_read_field() {
  local field="$1"
  if [[ ! -f "$TTS_STATE_FILE" ]]; then
    echo ""
    return
  fi
  # Match: field: "value" or field: value (with optional quotes)
  grep "^${field}:" "$TTS_STATE_FILE" 2>/dev/null \
    | head -1 \
    | sed 's/^[^:]*: *"\{0,1\}\([^"]*\)"\{0,1\} *$/\1/'
}

# Read notify state: y, c, or n (default: n)
read_notify() {
  local val
  val=$(_read_field "notify")
  case "$val" in
    y|c|n) echo "$val" ;;
    *)     echo "n" ;;
  esac
}

# Read speak state: y or n (default: y)
read_speak() {
  local val
  val=$(_read_field "speak")
  case "$val" in
    y|n) echo "$val" ;;
    *)   echo "y" ;;
  esac
}

# Read vibe_mode: auto, manual, or off (default: auto)
read_vibe_mode() {
  local val
  val=$(_read_field "vibe_mode")
  case "$val" in
    auto|manual|off) echo "$val" ;;
    *)               echo "auto" ;;
  esac
}

# Read vibe_signals accumulator string (raw, may be empty).
read_vibe_signals() {
  _read_field "vibe_signals"
}

# Pick a random element from positional arguments (Bash 3.2 compatible).
pick_random() {
  local idx=$((RANDOM % $#))
  shift "$idx"
  echo "$1"
}

# Infer possessive pronoun for a voice name.
# Human names get gendered pronouns; abstract/unknown names get "their".
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

# Play audio via flock-serialized queue (non-blocking, fire-and-forget).
# Uses `vox play` which acquires LOCK_EX before running afplay.
enqueue_audio() {
  if command -v vox &>/dev/null; then
    nohup vox play "$1" >/dev/null 2>&1 &
    disown
  fi
}

# Play audio via flock-serialized queue (blocking, waits for completion).
# Use when cleanup must happen after playback finishes.
play_audio_blocking() {
  if command -v vox &>/dev/null; then
    vox play "$1" 2>/dev/null
  fi
}
