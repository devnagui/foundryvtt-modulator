# Packaging and Self-Hosting

## Targets

- Linux `.deb`
- Windows `.exe`
- macOS `.app`
- Docker image/compose

## Linux `.deb`

```bash
chmod +x packaging/deb/build_deb.sh
./packaging/deb/build_deb.sh 0.1.0
```

Output:

- `dist/foundryvtt-modulator_0.1.0_all.deb`

## Windows `.exe`

```powershell
.\packaging\windows\build_windows.ps1 -Version 0.1.0
```

Output:

- `dist\windows\foundryvtt-modulator-api-0.1.0.exe`

## macOS `.app`

```bash
chmod +x packaging/macos/build_macos_app.sh
./packaging/macos/build_macos_app.sh 0.1.0
```

Output:

- `dist/macos/FoundryVTTModulator-0.1.0.app`

## Docker

```bash
docker compose -f docker-compose.resolver.yml up -d --build
```

Production-oriented compose (healthcheck + parametrized env):

```bash
export FOUNDRY_DATA_ROOT=/path/to/foundry/root
docker compose -f docker-compose.prod.yml up -d --build
```

## Service install helpers

### Linux (`systemd` via `.deb`)

- package installs `foundryvtt-modulator.service`
- env file: `/etc/foundryvtt-modulator/env.conf`

### Windows

Install service:

```powershell
.\packaging\windows\install_service.ps1
```

Remove service:

```powershell
.\packaging\windows\uninstall_service.ps1
```

### macOS (`launchd`)

Install service:

```bash
chmod +x packaging/macos/install_launchd.sh
./packaging/macos/install_launchd.sh
```

Remove service:

```bash
chmod +x packaging/macos/uninstall_launchd.sh
./packaging/macos/uninstall_launchd.sh
```

## CI/CD release

- `CI` workflow runs tests on push/PR.
- `Release` workflow runs on tags `v*`, builds platform artifacts, generates `SHA256SUMS.txt`, and publishes GitHub Release assets.

## Notes

- Use environment variables for auth and security hardening.
- Prefer HTTPS reverse proxy in production.
