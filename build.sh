#!/usr/bin/env bash
set -euo pipefail

# Build a standalone macOS binary with PyInstaller.
# Output: dist/location-tracker (single directory bundle)

info() { printf '\033[1;34m==>\033[0m %s\n' "$*"; }

info "Installing build dependencies..."
uv pip install pyinstaller

info "Building standalone binary..."
uv run pyinstaller \
    --name location-tracker \
    --onedir \
    --console \
    --noconfirm \
    --clean \
    --hidden-import locationsharinglib \
    --hidden-import flask \
    --hidden-import folium \
    --hidden-import pandas \
    --hidden-import playwright \
    --collect-all locationsharinglib \
    --collect-all flask \
    --collect-all folium \
    main.py

info "Build complete: dist/location-tracker/"
info "Distribute as a zip: cd dist && zip -r location-tracker-macos.zip location-tracker/"
echo
info "Users run: ./location-tracker/location-tracker setup"
