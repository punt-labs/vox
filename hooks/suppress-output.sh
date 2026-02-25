#!/usr/bin/env bash
# Format tts MCP tool output for the UI panel.
#
# updatedMCPToolOutput sets the text displayed in the tool-result panel.
# additionalContext passes the full tool data to the model separately.
#
# Note: no `set -euo pipefail` — hooks must degrade gracefully on
# malformed input rather than failing the tool call.

INPUT=$(cat)
TOOL=$(echo "$INPUT" | jq -r '.tool_name')
TOOL_NAME="${TOOL##*__}"
RESULT=$(echo "$INPUT" | jq -r '.tool_response' | jq -r '.result // .')

if [[ "$TOOL_NAME" == "synthesize" ]]; then
  # Extract text preview for panel
  TEXT=$(echo "$RESULT" | jq -r '.text // empty' 2>/dev/null || echo "$RESULT")
  PREVIEW="${TEXT:0:40}"
  if [[ ${#TEXT} -gt 40 ]]; then
    PREVIEW="${PREVIEW}..."
  fi
  jq -n --arg summary "Speaking: $PREVIEW" --arg ctx "$RESULT" '{
    hookSpecificOutput: {
      hookEventName: "PostToolUse",
      updatedMCPToolOutput: $summary,
      additionalContext: $ctx
    }
  }'
  exit 0
fi

if [[ "$TOOL_NAME" == "synthesize_batch" ]]; then
  # Count results for panel summary
  COUNT=$(echo "$RESULT" | jq -r 'length' 2>/dev/null || echo "?")
  jq -n --arg summary "Synthesized $COUNT items" --arg ctx "$RESULT" '{
    hookSpecificOutput: {
      hookEventName: "PostToolUse",
      updatedMCPToolOutput: $summary,
      additionalContext: $ctx
    }
  }'
  exit 0
fi

if [[ "$TOOL_NAME" == "synthesize_pair" ]]; then
  TEXT=$(echo "$RESULT" | jq -r '.text // empty' 2>/dev/null || echo "$RESULT")
  PREVIEW="${TEXT:0:40}"
  if [[ ${#TEXT} -gt 40 ]]; then
    PREVIEW="${PREVIEW}..."
  fi
  jq -n --arg summary "Pair: $PREVIEW" --arg ctx "$RESULT" '{
    hookSpecificOutput: {
      hookEventName: "PostToolUse",
      updatedMCPToolOutput: $summary,
      additionalContext: $ctx
    }
  }'
  exit 0
fi

if [[ "$TOOL_NAME" == "synthesize_pair_batch" ]]; then
  COUNT=$(echo "$RESULT" | jq -r 'length' 2>/dev/null || echo "?")
  jq -n --arg summary "Synthesized $COUNT pairs" --arg ctx "$RESULT" '{
    hookSpecificOutput: {
      hookEventName: "PostToolUse",
      updatedMCPToolOutput: $summary,
      additionalContext: $ctx
    }
  }'
  exit 0
fi

# Fallback: full output in panel
jq -n --arg r "$RESULT" '{
  hookSpecificOutput: {
    hookEventName: "PostToolUse",
    updatedMCPToolOutput: $r
  }
}'
