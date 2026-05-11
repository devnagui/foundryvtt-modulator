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
mkdir -p "${PKG_DIR}/etc/${PKG_NAME}"

cp -r "${ROOT_DIR}/resolver" "${PKG_DIR}/opt/${PKG_NAME}/resolver"
cp -r "${ROOT_DIR}/backend" "${PKG_DIR}/opt/${PKG_NAME}/backend"
cp -r "${ROOT_DIR}/frontend" "${PKG_DIR}/opt/${PKG_NAME}/frontend"
cp "${ROOT_DIR}/README.md" "${PKG_DIR}/opt/${PKG_NAME}/README.md"

cat > "${PKG_DIR}/etc/${PKG_NAME}/env.conf" <<EOF
RESOLVER_BIND_HOST=0.0.0.0
RESOLVER_BIND_PORT=8787
RESOLVER_DATA_ROOT=/foundry-data
RESOLVER_CACHE_DIR=/var/lib/${PKG_NAME}/cache
RESOLVER_STATE_DIR=/var/lib/${PKG_NAME}/state
RESOLVER_REPORTS_DIR=/var/lib/${PKG_NAME}/reports
RESOLVER_REQUIRE_FOUNDRY_OFFLINE=true
RESOLVER_COOKIE_SECURE=false
EOF

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
ExecStart=/usr/bin/python3 -m uvicorn backend.app.main:app --host 0.0.0.0 --port 8787
Restart=on-failure
EnvironmentFile=-/etc/${PKG_NAME}/env.conf

[Install]
WantedBy=multi-user.target
EOF

cat > "${PKG_DIR}/DEBIAN/postinst" <<EOF
#!/bin/sh
set -e
mkdir -p /var/lib/${PKG_NAME}/cache /var/lib/${PKG_NAME}/state /var/lib/${PKG_NAME}/reports
chmod 755 /var/lib/${PKG_NAME} || true
cd /opt/${PKG_NAME}
/usr/bin/python3 -m pip install --break-system-packages -r backend/requirements.txt || true
systemctl daemon-reload || true
systemctl enable ${PKG_NAME}.service || true
echo "Installed ${PKG_NAME}. Configure /etc/${PKG_NAME}/env.conf and start with: systemctl start ${PKG_NAME}"
EOF
chmod 0755 "${PKG_DIR}/DEBIAN/postinst"

cat > "${PKG_DIR}/DEBIAN/prerm" <<EOF
#!/bin/sh
set -e
systemctl stop ${PKG_NAME}.service || true
systemctl disable ${PKG_NAME}.service || true
systemctl daemon-reload || true
EOF
chmod 0755 "${PKG_DIR}/DEBIAN/prerm"

dpkg-deb --build "${PKG_DIR}" "${ROOT_DIR}/dist/${PKG_NAME}_${VERSION}_all.deb"
echo "Built: ${ROOT_DIR}/dist/${PKG_NAME}_${VERSION}_all.deb"
