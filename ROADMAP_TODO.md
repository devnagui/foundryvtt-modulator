# Roadmap TODO

- [x] 1. Criar instrucoes para IA (`copilot-instructions`) e arquivos de colaboracao
- [x] 2. Adicionar autenticacao e hardening da API
- [x] 3. Implementar motor de execucao de acoes (catalogo, fila, status, auditoria, concorrencia, progresso)
- [x] 4. Adicionar empacotamento e self-hosting multiplataforma (`.deb`, Windows, Docker, `.app`)
- [x] 5. Configurar pipeline CI/CD (testes, build, release)

## Entregues no Item 1

- `.github/copilot-instructions.md`
- `CONTRIBUTING.md`
- `.github/pull_request_template.md`
- `docs/ai-prompts.md`

## Entregues no Item 2

- lockout de login por tentativas falhas (`429 too_many_attempts`)
- limites configuraveis por env:
  - `RESOLVER_AUTH_MAX_FAILED_ATTEMPTS`
  - `RESOLVER_AUTH_LOCKOUT_MINUTES`
  - `RESOLVER_MAX_SESSIONS`
- auditoria em JSONL:
  - `RESOLVER_AUDIT_FILE`
  - eventos de auth e execucao de acoes
- testes de integracao cobrindo fluxo protegido e lockout

## Entregues no Item 5

- workflow CI (`.github/workflows/ci.yml`) com matriz Linux/Windows/macOS para testes
- workflow Release (`.github/workflows/release.yml`) em tags `v*` com:
  - build de artefatos por plataforma
  - upload de artefatos intermediarios
  - consolidacao e publicacao de release assets
  - geracao de `SHA256SUMS.txt`

## Entregues no Item 3

- endpoint assíncrono de submissao: `POST /api/actions/submit`
- consulta de fila e jobs:
  - `GET /api/actions/jobs`
  - `GET /api/actions/jobs/{jobId}`
- estados de job:
  - `pending`, `running`, `success`, `failed`
- progresso de job (0-100)
- worker em background com lock de manutencao para acoes destrutivas
- auditoria de enfileiramento e execucao de job
- fallback de primeiro acesso em `/api/report/v3`:
  - quando o v3 nao existe, exibe tela com botao para gerar o primeiro dry-run
  - acompanha status do job e redireciona automaticamente para o v3 ao concluir

## Qualidade e robustez

- `--data-root` agora aceita automaticamente a pasta `Data` e normaliza para a raiz Foundry
- testes adicionados para:
  - normalizacao de `data-root`
  - fallback bootstrap do `report_v3`

## Entregues no Item 4

- scripts base de empacotamento adicionados:
  - `packaging/deb/build_deb.sh`
  - `packaging/windows/build_windows.ps1`
  - `packaging/macos/build_macos_app.sh`
- scripts de instalacao de servico adicionados:
  - `packaging/windows/install_service.ps1`
  - `packaging/windows/uninstall_service.ps1`
  - `packaging/macos/install_launchd.sh`
  - `packaging/macos/uninstall_launchd.sh`
- compose de producao adicionado:
  - `docker-compose.prod.yml`
- guia inicial de release/self-hosting:
  - `docs/release/packaging.md`
