#!/usr/bin/env sh
set -e

# ── Color helpers ──────────────────────────────────────────
if [ -t 1 ]; then
    GREEN='\033[0;32m'
    RED='\033[0;31m'
    YELLOW='\033[0;33m'
    BOLD='\033[1m'
    RESET='\033[0m'
else
    GREEN=''
    RED=''
    YELLOW=''
    BOLD=''
    RESET=''
fi

# ── Utility functions ──────────────────────────────────────
info()  { printf '%s[info]%s  %s\n' "$BOLD" "$RESET" "$1"; }
ok()    { printf '%s[ok]%s    %s\n' "$GREEN" "$RESET" "$1"; }
warn()  { printf '%s[warn]%s  %s\n' "$YELLOW" "$RESET" "$1"; }
fail()  { printf '%s[fail]%s  %s\n' "$RED" "$RESET" "$1"; exit 1; }

# ── OS detection ───────────────────────────────────────────
detect_os() {
    case "$(uname -s)" in
        Darwin) OS="macos" ;;
        Linux)
            if [ -f /etc/os-release ]; then
                # shellcheck disable=SC1091
                . /etc/os-release
                case "$ID" in
                    ubuntu|debian) OS="ubuntu" ;;
                    *) OS="linux-other" ;;
                esac
            else
                OS="linux-other"
            fi
            ;;
        *) fail "Unsupported OS: $(uname -s). AutoSkillit supports macOS and Ubuntu." ;;
    esac
    info "Detected OS: $OS"
}

# ── Python 3.11+ ──────────────────────────────────────────
ensure_python() {
    if command -v python3 >/dev/null 2>&1; then
        py_version=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
        py_major=$(echo "$py_version" | cut -d. -f1)
        py_minor=$(echo "$py_version" | cut -d. -f2)
        if [ "$py_major" -ge 3 ] && [ "$py_minor" -ge 11 ]; then
            ok "Python $py_version"
            return
        fi
        warn "Python $py_version found but 3.11+ is required"
    else
        warn "Python 3 not found"
    fi

    info "Installing Python 3.12..."
    case "$OS" in
        macos)
            if ! command -v brew >/dev/null 2>&1; then
                fail "Homebrew is required to install Python on macOS. Install it from https://brew.sh"
            fi
            brew install python@3.12
            ;;
        ubuntu)
            sudo apt-get update -qq
            sudo apt-get install -y python3.12 python3.12-venv
            ;;
        *)
            fail "Please install Python 3.11+ manually and re-run this script."
            ;;
    esac

    # Re-verify
    if command -v python3 >/dev/null 2>&1; then
        py_version=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
        ok "Python $py_version installed"
    else
        fail "Python installation failed. Please install Python 3.11+ manually."
    fi
}

# ── uv ─────────────────────────────────────────────────────
ensure_uv() {
    if command -v uv >/dev/null 2>&1; then
        ok "uv $(uv --version 2>/dev/null | head -1)"
        return
    fi

    info "Installing uv..."
    curl -LsSf https://astral.sh/uv/install.sh | sh

    # Source the env file if it exists
    if [ -f "$HOME/.local/bin/env" ]; then
        # shellcheck disable=SC1091
        . "$HOME/.local/bin/env"
    fi
    export PATH="$HOME/.local/bin:$PATH"

    if command -v uv >/dev/null 2>&1; then
        ok "uv installed"
    else
        fail "uv installation failed. See https://docs.astral.sh/uv/getting-started/installation/"
    fi
}

# ── Claude Code ────────────────────────────────────────────
ensure_claude() {
    if command -v claude >/dev/null 2>&1; then
        ok "Claude Code found"
        return
    fi

    fail "Claude Code is not installed. Install it first:
  https://docs.anthropic.com/en/docs/claude-code/overview

Then re-run this script."
}

# ── AutoSkillit ────────────────────────────────────────────
install_autoskillit() {
    info "Installing AutoSkillit from stable branch..."
    uv tool install "git+https://github.com/TalonT-Org/AutoSkillit.git@stable" 2>/dev/null \
        || uv tool install --force "git+https://github.com/TalonT-Org/AutoSkillit.git@stable" 2>/dev/null \
        || fail "Failed to install AutoSkillit. Check your network connection."
    autoskillit install
    ok "AutoSkillit installed and registered with Claude Code"
}

# ── Main ───────────────────────────────────────────────────
main() {
    printf '\n%sAutoSkillit Installer%s\n\n' "$BOLD" "$RESET"
    detect_os
    ensure_python
    ensure_uv
    ensure_claude
    install_autoskillit
    printf '\n%s✓ All done!%s\n\n' "$GREEN" "$RESET"
    printf 'Next steps:\n'
    printf '  cd your-project\n'
    printf '  autoskillit init\n'
    printf '  autoskillit cook implementation\n\n'
}

main
