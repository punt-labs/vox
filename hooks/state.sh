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

# Classify vibe string into a mood family: bright, neutral, or dark.
# Mirrors MOOD_FAMILIES from src/punt_vox/mood.py.
classify_mood() {
  local vibe
  vibe=$(_read_field "vibe")
  vibe=$(echo "$vibe" | tr '[:upper:]' '[:lower:]')
  case "$vibe" in
    *happy*|*excited*|*satisfied*|*warm*|*playful*|*cheerful*|*joyful*|*energetic*|*triumphant*)
      echo "bright" ;;
    *frustrated*|*tense*|*tired*|*concerned*|*annoyed*|*stressed*|*anxious*|*overwhelmed*)
      echo "dark" ;;
    *)
      echo "neutral" ;;
  esac
}

# Resolve mood-aware chime path for a signal.
# Falls back: mood-specific signal → neutral signal → mood-specific done → done.
resolve_chime() {
  local signal="$1"
  local mood
  mood=$(classify_mood)

  local base_dir
  base_dir="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)/../assets"

  if [[ "$mood" != "neutral" ]]; then
    local mood_file="$base_dir/chime_${signal}_${mood}.mp3"
    if [[ -f "$mood_file" ]]; then
      echo "$mood_file"
      return
    fi
  fi

  local neutral_file="$base_dir/chime_${signal}.mp3"
  if [[ -f "$neutral_file" ]]; then
    echo "$neutral_file"
    return
  fi

  if [[ "$mood" != "neutral" ]]; then
    local mood_done="$base_dir/chime_done_${mood}.mp3"
    if [[ -f "$mood_done" ]]; then
      echo "$mood_done"
      return
    fi
  fi

  echo "$base_dir/chime_done.mp3"
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
