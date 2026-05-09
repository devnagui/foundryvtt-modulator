#!/usr/bin/env bash
set -euo pipefail

# Builds a macOS .app using pyinstaller.
# Run on macOS host only.

VERSION="${1:-0.1.0}"
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
DIST_DIR="${ROOT_DIR}/dist/macos"

python3 -m pip install --upgrade pip pyinstaller

pushd "${ROOT_DIR}" >/dev/null
python3 -m PyInstaller \
  --noconfirm \
  --windowed \
  --name "FoundryVTTModulator-${VERSION}" \
  service/server.py
popd >/dev/null

mkdir -p "${DIST_DIR}"
cp -R "${ROOT_DIR}/dist/FoundryVTTModulator-${VERSION}.app" "${DIST_DIR}/"
echo "Built: ${DIST_DIR}/FoundryVTTModulator-${VERSION}.app"
