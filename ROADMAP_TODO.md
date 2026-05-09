# Roadmap TODO

- [x] 1. Criar instrucoes para IA (`copilot-instructions`) e arquivos de colaboracao
- [x] 2. Adicionar autenticacao e hardening da API
- [x] 3. Implementar motor de execucao de acoes (catalogo, fila, status, auditoria, concorrencia, progresso)
- [ ] 4. Adicionar empacotamento e self-hosting multiplataforma (`.deb`, Windows, Docker, `.app`)
- [ ] 5. Configurar pipeline CI/CD (testes, build, release)

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

## Item 5 (parcial)

- workflow inicial de CI criado em `.github/workflows/ci.yml` para rodar `unittest` em Linux, Windows e macOS

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

## Item 4 (parcial)

- scripts base de empacotamento adicionados:
  - `packaging/deb/build_deb.sh`
  - `packaging/windows/build_windows.ps1`
  - `packaging/macos/build_macos_app.sh`
- guia inicial de release/self-hosting:
  - `docs/release/packaging.md`
