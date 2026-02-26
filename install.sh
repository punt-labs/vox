#!/bin/sh
# Install punt-tts — voice for your AI coding assistant.
# Usage: curl -fsSL https://raw.githubusercontent.com/punt-labs/tts/main/install.sh | sh
set -eu

# --- Colors (disabled when not a terminal) ---
if [ -t 1 ]; then
  BOLD='\033[1m' GREEN='\033[32m' YELLOW='\033[33m' NC='\033[0m'
else
  BOLD='' GREEN='' YELLOW='' NC=''
fi

info() { printf '%b==>%b %s\n' "$BOLD" "$NC" "$1"; }
ok()   { printf '  %b✓%b %s\n' "$GREEN" "$NC" "$1"; }
fail() { printf '  %b✗%b %s\n' "$YELLOW" "$NC" "$1"; exit 1; }

PACKAGE="punt-tts"
BINARY="tts"

# --- Step 1: Python ---

info "Checking Python..."

if command -v python3 >/dev/null 2>&1; then
  PYTHON=python3
elif command -v python >/dev/null 2>&1; then
  PYTHON=python
else
  fail "Python not found. Install Python 3.13+ from https://python.org"
fi

PY_MAJOR=$("$PYTHON" -c 'import sys; print(sys.version_info.major)')
PY_MINOR=$("$PYTHON" -c 'import sys; print(sys.version_info.minor)')

if [ "$PY_MAJOR" -lt 3 ] || { [ "$PY_MAJOR" -eq 3 ] && [ "$PY_MINOR" -lt 13 ]; }; then
  fail "Python ${PY_MAJOR}.${PY_MINOR} found, but 3.13+ is required"
fi

ok "Python ${PY_MAJOR}.${PY_MINOR}"

# --- Step 2: uv ---

info "Checking uv..."

if command -v uv >/dev/null 2>&1; then
  ok "uv already installed"
else
  info "Installing uv..."
  curl -LsSf https://astral.sh/uv/install.sh | sh
  if [ -f "$HOME/.local/bin/env" ]; then
    # shellcheck source=/dev/null
    . "$HOME/.local/bin/env"
  elif [ -f "$HOME/.cargo/env" ]; then
    # shellcheck source=/dev/null
    . "$HOME/.cargo/env"
  fi
  export PATH="$HOME/.local/bin:$PATH"
  if ! command -v uv >/dev/null 2>&1; then
    fail "uv install succeeded but 'uv' not found on PATH. Restart your shell and re-run."
  fi
  ok "uv installed"
fi

# --- Step 3: Claude Code CLI ---

info "Checking Claude Code..."

if command -v claude >/dev/null 2>&1; then
  ok "claude CLI found"
else
  fail "'claude' CLI not found. Install Claude Code first: https://docs.anthropic.com/en/docs/claude-code"
fi

# --- Step 4: punt-tts ---

info "Installing $PACKAGE..."

# --force: overwrites existing binary (may exist from prior install)
uv tool install --force "$PACKAGE" || fail "Failed to install $PACKAGE"
ok "$PACKAGE installed"

if ! command -v "$BINARY" >/dev/null 2>&1; then
  export PATH="$HOME/.local/bin:$PATH"
  if ! command -v "$BINARY" >/dev/null 2>&1; then
    fail "$PACKAGE installed but '$BINARY' not found on PATH"
  fi
fi

ok "$BINARY $(command -v "$BINARY")"

# --- Step 5: tts install (MCP server registration) ---

info "Setting up Claude Code plugin..."

# claude plugin install clones via SSH (git@github.com:...).
# Users without SSH keys need an HTTPS fallback.
NEED_HTTPS_REWRITE=0
if ! ssh -o StrictHostKeyChecking=accept-new -T git@github.com 2>&1 | grep -q "successfully authenticated"; then
  printf '  ℹ SSH auth to GitHub unavailable, using HTTPS fallback\n'
  git config --global url."https://github.com/".insteadOf "git@github.com:"
  NEED_HTTPS_REWRITE=1
fi

"$BINARY" install
INSTALL_EXIT=$?

# Clean up the HTTPS rewrite regardless of install outcome.
if [ "$NEED_HTTPS_REWRITE" = "1" ]; then
  git config --global --unset url."https://github.com/".insteadOf 2>/dev/null || true
fi

if [ "$INSTALL_EXIT" -ne 0 ]; then
  fail "Plugin install failed"
fi

# --- Step 6: tts doctor ---

info "Verifying installation..."
printf '\n'
"$BINARY" doctor
printf '\n'

# --- Done ---

printf '%b%b%s is ready!%b\n\n' "$GREEN" "$BOLD" "$PACKAGE" "$NC"
printf 'Restart Claude Code, then:\n'
printf '  /notify y     # hear when tasks complete or need input\n'
printf '  /recap        # spoken summary of what just happened\n\n'
