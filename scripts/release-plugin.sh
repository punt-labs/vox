#!/usr/bin/env bash
set -euo pipefail

# Prepare plugin for release: swap name to prod, revert MCP server to
# installed binary, and remove -dev commands. The tagged commit has only
# prod artifacts; the marketplace cache clones from it.
#
# Why MCP server swap matters: dev uses "uv run tts serve" to exercise the
# working tree source; prod uses "tts serve" (the installed binary).
# Marketplace users don't have uv — shipping "uv run" would silently break
# the MCP server.

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PLUGIN_JSON="${REPO_ROOT}/.claude-plugin/plugin.json"
COMMANDS_DIR="${REPO_ROOT}/commands"

# Swap plugin name from *-dev to prod AND revert MCP server to prod binary
current_name="$(python3 -c "import json; print(json.load(open('${PLUGIN_JSON}'))['name'])")"
prod_name="${current_name%-dev}"

if [[ "$current_name" == "$prod_name" ]]; then
  echo "Plugin name is already '${prod_name}' (no -dev suffix)" >&2
  exit 1
fi

echo "Swapping plugin name: ${current_name} → ${prod_name}"
echo "Reverting MCP server: uv run tts serve → tts serve"
python3 -c "
import json, pathlib
p = pathlib.Path('${PLUGIN_JSON}')
d = json.loads(p.read_text())
d['name'] = '${prod_name}'
# Revert MCP server from dev (uv run) to prod (installed binary)
for server in d.get('mcpServers', {}).values():
    if server.get('command') == 'uv' and server.get('args', [])[:1] == ['run']:
        binary = server['args'][1]  # e.g. 'tts'
        remaining = server['args'][2:]  # e.g. ['serve']
        server['command'] = binary
        server['args'] = remaining
p.write_text(json.dumps(d, indent=2) + '\n')
"

# Remove -dev commands
dev_files=()
while IFS= read -r -d '' f; do
  dev_files+=("$f")
done < <(find "$COMMANDS_DIR" -name '*-dev.md' -print0)

if [[ ${#dev_files[@]} -eq 0 ]]; then
  echo "No -dev commands found in ${COMMANDS_DIR}" >&2
  exit 1
fi

for f in "${dev_files[@]}"; do
  echo "Removing: $(basename "$f")"
done

git -C "$REPO_ROOT" add "$PLUGIN_JSON"
git -C "$REPO_ROOT" rm "${dev_files[@]}"
git -C "$REPO_ROOT" commit --no-verify -m "chore: prepare plugin for release [skip ci]"
