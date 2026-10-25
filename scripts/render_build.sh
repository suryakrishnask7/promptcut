#!/usr/bin/env bash
# scripts/render_build.sh
# Build script for Render native Python runtime (if not using Docker)
set -o errexit

echo "==> Upgrading pip and build tools..."
pip install --upgrade pip hatchling

echo "==> Installing dependencies..."
pip install pytest pytest-cov pyyaml boto3 httpx uvicorn

echo "==> Installing PromptCut in editable mode..."
pip install -e .

# Check if ffmpeg is available
if ! command -v ffmpeg &> /dev/null; then
    echo "==> ffmpeg not found in PATH. Downloading static build for Linux..."
    mkdir -p bin
    curl -L https://johnvansickle.com/ffmpeg/releases/ffmpeg-release-amd64-static.tar.xz | tar -xJ --strip-components=1 -C bin
    export PATH="$PWD/bin:$PATH"
    echo "export PATH=\"$PWD/bin:\$PATH\"" >> ~/.bashrc
fi

echo "==> PromptCut build completed successfully."
