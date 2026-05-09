#!/usr/bin/env bash
set -euo pipefail

# Builds a minimal .deb package for resolver-api.
# Usage:
#   ./packaging/deb/build_deb.sh 0.1.0

VERSION="${1:-0.1.0}"
PKG_NAME="foundryvtt-modulator"
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
BUILD_DIR="${ROOT_DIR}/dist/deb-build"
PKG_DIR="${BUILD_DIR}/${PKG_NAME}_${VERSION}"

rm -rf "${BUILD_DIR}"
mkdir -p "${PKG_DIR}/DEBIAN"
mkdir -p "${PKG_DIR}/opt/${PKG_NAME}"
mkdir -p "${PKG_DIR}/etc/systemd/system"

cp -r "${ROOT_DIR}/resolver" "${PKG_DIR}/opt/${PKG_NAME}/resolver"
cp -r "${ROOT_DIR}/service" "${PKG_DIR}/opt/${PKG_NAME}/service"
cp "${ROOT_DIR}/README.md" "${PKG_DIR}/opt/${PKG_NAME}/README.md"

cat > "${PKG_DIR}/DEBIAN/control" <<EOF
Package: ${PKG_NAME}
Version: ${VERSION}
Section: utils
Priority: optional
Architecture: all
Maintainer: FoundryVTT Modulator
Depends: python3 (>= 3.11)
Description: Foundry VTT module version resolver API and CLI
EOF

cat > "${PKG_DIR}/etc/systemd/system/${PKG_NAME}.service" <<EOF
[Unit]
Description=FoundryVTT Modulator API
After=network.target

[Service]
Type=simple
WorkingDirectory=/opt/${PKG_NAME}
ExecStart=/usr/bin/python3 -m service.server
Restart=on-failure
Environment=RESOLVER_BIND_HOST=0.0.0.0
Environment=RESOLVER_BIND_PORT=8787

[Install]
WantedBy=multi-user.target
EOF

dpkg-deb --build "${PKG_DIR}" "${ROOT_DIR}/dist/${PKG_NAME}_${VERSION}_all.deb"
echo "Built: ${ROOT_DIR}/dist/${PKG_NAME}_${VERSION}_all.deb"
