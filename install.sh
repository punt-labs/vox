#!/bin/sh
# Install punt-vox — voice for your AI coding assistant.
# Usage: curl -fsSL https://raw.githubusercontent.com/punt-labs/vox/<SHA>/install.sh | sh
set -eu

# --- Colors (disabled when not a terminal) ---
if [ -t 1 ]; then
  BOLD='\033[1m' GREEN='\033[32m' YELLOW='\033[33m' NC='\033[0m'
else
  BOLD='' GREEN='' YELLOW='' NC=''
fi

info() { printf '%b▶%b %s\n' "$BOLD" "$NC" "$1"; }
ok()   { printf '  %b✓%b %s\n' "$GREEN" "$NC" "$1"; }
warn() { printf '  %b!%b %s\n' "$YELLOW" "$NC" "$1"; }
fail() { printf '  %b✗%b %s\n' "$YELLOW" "$NC" "$1"; exit 1; }

MARKETPLACE_REPO="punt-labs/claude-plugins"
MARKETPLACE_NAME="punt-labs"
PLUGIN_NAME="vox"
PACKAGE="punt-vox"
VERSION="4.0.1"
BINARY="vox"

# --- Step 1: Prerequisites ---

info "Checking prerequisites..."

if command -v claude >/dev/null 2>&1; then
  ok "claude CLI found"
else
  fail "'claude' CLI not found. Install Claude Code first: https://docs.anthropic.com/en/docs/claude-code"
fi

if command -v git >/dev/null 2>&1; then
  ok "git found"
else
  fail "'git' not found. Install git first: https://git-scm.com/downloads"
fi

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

# --- Step 3: Python 3.13+ ---

info "Checking Python..."

PYTHON_FLAG=""
HAVE_PYTHON=0
if command -v python3 >/dev/null 2>&1; then
  PY_MAJOR=$(python3 -c 'import sys; print(sys.version_info.major)')
  PY_MINOR=$(python3 -c 'import sys; print(sys.version_info.minor)')
  if [ "$PY_MAJOR" -gt 3 ] || { [ "$PY_MAJOR" -eq 3 ] && [ "$PY_MINOR" -ge 13 ]; }; then
    ok "Python ${PY_MAJOR}.${PY_MINOR}"
    HAVE_PYTHON=1
  fi
fi

if [ "$HAVE_PYTHON" = "0" ]; then
  info "Installing Python 3.13 via uv..."
  uv python install 3.13 || fail "Failed to install Python 3.13"
  ok "Python 3.13 (uv-managed)"
  PYTHON_FLAG="--python 3.13"
fi

# --- Step 3b: System TTS (Linux only) ---
if [ "$(uname -s)" = "Linux" ]; then
  info "Checking system TTS..."
  if command -v espeak-ng >/dev/null 2>&1; then
    ok "espeak-ng found (offline fallback)"
  elif command -v espeak >/dev/null 2>&1; then
    ok "espeak found (offline fallback)"
  else
    warn "espeak-ng not found — install for offline TTS: sudo apt-get install espeak-ng"
    warn "Without it, you'll need a cloud TTS provider (e.g. API key for ElevenLabs/OpenAI or AWS credentials for Polly)"
  fi
fi

# --- Step 4: Install vox CLI ---

info "Installing $PACKAGE..."

# Clean up root-owned __pycache__ left by previous 'sudo vox daemon install'.
# Without this, uv tool install fails with Permission denied on Linux.
_uv_tools="${XDG_DATA_HOME:-$HOME/.local/share}/uv/tools/$PACKAGE"
if [ -d "$_uv_tools" ] && [ -n "$(find "$_uv_tools" -name __pycache__ -user root -print -quit 2>/dev/null)" ]; then
  sudo find "$_uv_tools" -name __pycache__ -user root -exec rm -rf {} + 2>/dev/null || true
fi

# shellcheck disable=SC2086
uv tool install --force $PYTHON_FLAG "$PACKAGE==$VERSION" || fail "Failed to install $PACKAGE==$VERSION"
ok "$PACKAGE installed"

if ! command -v "$BINARY" >/dev/null 2>&1; then
  export PATH="$HOME/.local/bin:$PATH"
  if ! command -v "$BINARY" >/dev/null 2>&1; then
    fail "$PACKAGE installed but '$BINARY' not found on PATH"
  fi
fi

ok "$BINARY $(command -v "$BINARY")"

# --- Step 5: Install daemon ---

info "Installing vox daemon (requires sudo for system service)..."
_vox_path="$(command -v "$BINARY")"
if sudo PYTHONDONTWRITEBYTECODE=1 PATH="$PATH" "$_vox_path" daemon install; then
  ok "vox daemon installed"
else
  warn "Could not install vox daemon (run 'sudo PYTHONDONTWRITEBYTECODE=1 PATH=\$PATH $(command -v vox) daemon install' manually)"
fi

# --- Step 6: Register marketplace ---

info "Registering Punt Labs marketplace..."

if claude plugin marketplace list < /dev/null 2>/dev/null | grep -q "$MARKETPLACE_NAME"; then
  ok "marketplace already registered"
  claude plugin marketplace update "$MARKETPLACE_NAME" < /dev/null 2>/dev/null || true
else
  claude plugin marketplace add "$MARKETPLACE_REPO" < /dev/null || fail "Failed to register marketplace"
  ok "marketplace registered"
fi

# --- Step 7: SSH fallback for plugin install ---

# claude plugin install clones via SSH (git@github.com:...).
# Users without SSH keys need an HTTPS fallback.
NEED_HTTPS_REWRITE=0
cleanup_https_rewrite() {
  if [ "$NEED_HTTPS_REWRITE" = "1" ]; then
    git config --global --unset url."https://github.com/".insteadOf 2>/dev/null || true
    NEED_HTTPS_REWRITE=0
  fi
}
trap cleanup_https_rewrite EXIT INT TERM

if ! ssh -n -o StrictHostKeyChecking=accept-new -o BatchMode=yes -o ConnectTimeout=5 -T git@github.com 2>&1 | grep -q "successfully authenticated"; then
  warn "SSH auth to GitHub unavailable, using HTTPS fallback"
  git config --global url."https://github.com/".insteadOf "git@github.com:"
  NEED_HTTPS_REWRITE=1
fi

# --- Step 8: Install or upgrade plugin ---

info "Installing $PLUGIN_NAME plugin..."

claude plugin uninstall "${PLUGIN_NAME}@${MARKETPLACE_NAME}" < /dev/null 2>/dev/null || true
if ! claude plugin install "${PLUGIN_NAME}@${MARKETPLACE_NAME}" < /dev/null; then
  cleanup_https_rewrite
  fail "Failed to install $PLUGIN_NAME"
fi
if ! claude plugin list < /dev/null 2>/dev/null | grep -q "$PLUGIN_NAME@$MARKETPLACE_NAME"; then
  cleanup_https_rewrite
  fail "$PLUGIN_NAME install reported success but plugin not found"
fi
ok "$PLUGIN_NAME plugin installed"

cleanup_https_rewrite

# --- Step 9: Verify ---

info "Verifying installation..."
printf '\n'
"$BINARY" doctor || true
printf '\n'

# --- Done ---

printf '%b%b%s is ready!%b\n\n' "$GREEN" "$BOLD" "$PLUGIN_NAME" "$NC"
printf 'Restart Claude Code, then:\n'
printf '  /vox y        # hear when tasks complete or need input\n'
printf '  /recap        # spoken summary of what just happened\n\n'
