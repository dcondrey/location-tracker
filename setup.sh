#!/usr/bin/env bash
set -euo pipefail

# Location Tracker - one-command setup
# Usage: ./setup.sh  or  curl -sSL <url>/setup.sh | bash

MIN_PYTHON="3.13"

info()  { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
warn()  { printf '\033[1;33m==>\033[0m %s\n' "$*"; }
error() { printf '\033[1;31m==>\033[0m %s\n' "$*"; exit 1; }

check_python() {
    if command -v python3 &>/dev/null; then
        local ver
        ver=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
        if python3 -c "import sys; exit(0 if sys.version_info >= (3,13) else 1)" 2>/dev/null; then
            info "Python $ver found."
            return 0
        else
            error "Python $ver found but $MIN_PYTHON+ is required. Install from https://python.org"
        fi
    else
        error "Python not found. Install Python $MIN_PYTHON+ from https://python.org"
    fi
}

install_uv() {
    if command -v uv &>/dev/null; then
        info "uv already installed."
    else
        info "Installing uv package manager..."
        curl -LsSf https://astral.sh/uv/install.sh | sh
        export PATH="$HOME/.local/bin:$PATH"
        info "uv installed."
    fi
}

main() {
    info "Location Tracker Setup"
    echo

    check_python
    install_uv

    info "Installing dependencies..."
    uv sync

    info "Set your Google email first:"
    echo "  uv run location-tracker config --email you@gmail.com"
    echo
    info "Then run setup (handles everything):"
    echo "  uv run location-tracker setup"
}

main "$@"
