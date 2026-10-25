#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# scripts/build_css.sh  ·  Build Tailwind CSS for production
#
# Uses the Tailwind CSS standalone CLI (no Node.js required).
# Downloads the binary on first run and caches it in scripts/.
#
# Usage:
#   chmod +x scripts/build_css.sh
#   ./scripts/build_css.sh
#
# The output file (src/promptcut/ui/static/app.css) is committed so that
# Docker and the live demo work without internet access.
# ─────────────────────────────────────────────────────────────────────────────

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
BIN="$SCRIPT_DIR/tailwindcss"

# ── 1. Detect platform and download binary if missing ─────────────────────────
if [[ ! -f "$BIN" ]]; then
  echo "⬇  Downloading Tailwind standalone CLI…"

  OS="$(uname -s | tr '[:upper:]' '[:lower:]')"
  ARCH="$(uname -m)"

  case "$OS" in
    linux*)
      case "$ARCH" in
        x86_64)  PLATFORM="linux-x64" ;;
        aarch64) PLATFORM="linux-arm64" ;;
        armv7*)  PLATFORM="linux-armv7" ;;
        *) echo "Unsupported arch: $ARCH"; exit 1 ;;
      esac ;;
    darwin*)
      case "$ARCH" in
        x86_64)  PLATFORM="macos-x64" ;;
        arm64)   PLATFORM="macos-arm64" ;;
        *) echo "Unsupported arch: $ARCH"; exit 1 ;;
      esac ;;
    *) echo "Unsupported OS: $OS. Use build_css.ps1 on Windows."; exit 1 ;;
  esac

  URL="https://github.com/tailwindlabs/tailwindcss/releases/download/v3.4.17/tailwindcss-${PLATFORM}"
  curl -fsSL -o "$BIN" "$URL"
  chmod +x "$BIN"
  echo "✓  Binary saved to scripts/tailwindcss"
fi

# ── 2. Build ──────────────────────────────────────────────────────────────────
INPUT="$PROJECT_ROOT/src/promptcut/ui/input.css"
OUTPUT="$PROJECT_ROOT/src/promptcut/ui/static/app.css"
CONFIG="$PROJECT_ROOT/tailwind.config.js"

echo "🔨 Building Tailwind CSS…"
"$BIN" \
  --config "$CONFIG" \
  --input "$INPUT" \
  --output "$OUTPUT" \
  --minify

echo "✓  Built: src/promptcut/ui/static/app.css"
echo "   ($(wc -c < "$OUTPUT" | tr -d ' ') bytes, minified)"
