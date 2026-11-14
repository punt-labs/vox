#!/usr/bin/env bash
# Verify VOX_RULES (the rules `vox enable` grants in a repo's
# .claude/settings.json) has a scoped Skill(vox:<name>) rule for every
# command in plugin/commands/*.md, and no other (permissions.md § 3 bans the
# bare Skill(<name>) form). Drift here causes unexplained permission
# prompts on first use. Fast (no network; uses standard shell
# utilities only).
set -euo pipefail
shopt -s nullglob

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
RULES="$ROOT/src/punt_vox/permission_rules.py"
COMMANDS_DIR="$ROOT/plugin/commands"

if [[ ! -f "$RULES" ]]; then
  echo "error: $RULES not found" >&2
  exit 2
fi
if [[ ! -d "$COMMANDS_DIR" ]]; then
  echo "error: $COMMANDS_DIR not found" >&2
  exit 2
fi

# Commands deployed to ~/.claude/commands (skip *-dev.md, matching the
# hook's deployment logic). Uses `while read` for Bash 3.2 compatibility
# (stock macOS) — mapfile is a Bash 4+ builtin.
COMMANDS=()
while IFS= read -r name; do
  COMMANDS+=("$name")
done < <(
  for f in "$COMMANDS_DIR"/*.md; do
    name="$(basename "$f" .md)"
    [[ "$name" == *-dev ]] && continue
    printf '%s\n' "$name"
  done | sort
)

if [[ ${#COMMANDS[@]} -eq 0 ]]; then
  echo "error: no *.md files found in $COMMANDS_DIR — expected at least one command" >&2
  exit 2
fi

# Skill() rules: one Skill(vox:<name>) per name in the _COMMANDS tuple of
# src/punt_vox/permission_rules.py.
ALLOWED=()
while IFS= read -r allow; do
  ALLOWED+=("vox:$allow")
done < <(
  sed -n '/^_COMMANDS: /,/^)$/p' "$RULES" \
    | grep -oE '"[^"]+"' \
    | tr -d '"' \
    | sort -u
)

missing=()
for cmd in "${COMMANDS[@]}"; do
  want="vox:$cmd"
  found=0
  if [[ ${#ALLOWED[@]} -gt 0 ]]; then
    for allow in "${ALLOWED[@]}"; do
      [[ "$want" == "$allow" ]] && { found=1; break; }
    done
  fi
  [[ $found -eq 0 ]] && missing+=("$cmd (expected Skill($want))")
done

extra=()
if [[ ${#ALLOWED[@]} -gt 0 ]]; then
  for allow in "${ALLOWED[@]}"; do
    found=0
    for cmd in "${COMMANDS[@]}"; do
      want="vox:$cmd"
      [[ "$want" == "$allow" ]] && { found=1; break; }
    done
    [[ $found -eq 0 ]] && extra+=("$allow")
  done
fi

status=0
if [[ ${#missing[@]} -gt 0 ]]; then
  echo "error: plugin/commands/*.md has no matching Skill() rule in VOX_RULES (src/punt_vox/permission_rules.py):" >&2
  for m in "${missing[@]}"; do echo "  - $m" >&2; done
  status=1
fi
if [[ ${#extra[@]} -gt 0 ]]; then
  echo "error: VOX_RULES grants Skill() for commands that do not exist:" >&2
  for e in "${extra[@]}"; do echo "  - $e" >&2; done
  status=1
fi

if [[ $status -eq 0 ]]; then
  echo "skill-permissions: ${#COMMANDS[@]} commands, ${#ALLOWED[@]} Skill() rules — in sync"
fi
exit $status
