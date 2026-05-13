#!/usr/bin/env bash
set -euo pipefail

# Builds a macOS .app using pyinstaller.
# Run on macOS host only.

VERSION="${1:-0.1.0}"
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
DIST_DIR="${ROOT_DIR}/dist/macos"
VENV_DIR="${ROOT_DIR}/.venv-packaging-macos"

python3 -m venv "${VENV_DIR}"
source "${VENV_DIR}/bin/activate"
python -m pip install --upgrade pip pyinstaller
python -m pip install -r "${ROOT_DIR}/backend/requirements.txt"

pushd "${ROOT_DIR}" >/dev/null
python -m PyInstaller \
  --noconfirm \
  --windowed \
  --name "FoundryVTTModulator-${VERSION}" \
  backend/run_fastapi.py
popd >/dev/null

mkdir -p "${DIST_DIR}"
cp -R "${ROOT_DIR}/dist/FoundryVTTModulator-${VERSION}.app" "${DIST_DIR}/"
deactivate
echo "Built: ${DIST_DIR}/FoundryVTTModulator-${VERSION}.app"
