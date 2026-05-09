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

## Notes

- Use environment variables for auth and security hardening.
- Prefer HTTPS reverse proxy in production.
