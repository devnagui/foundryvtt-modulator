# Roadmap TODO

## Status geral (2026-05-11)

- Concluido:
  - Itens 1-5 iniciais (IA docs, auth/hardening, action engine, packaging, CI/CD)
  - Migracao principal para UI React (`/app/report`) e remocao de V2
  - Planning com score/semaforo, recomendacao automatica e impacto por target
  - Historico de apply/snapshots na UI + `batchSnapshot` pre/post versions
  - Rollback plan por `scanRunId` (backend + acao na UI)
  - Fluxo de dependencias sem URL com `Find Source` + `Set URL` + catalogo local
  - Confiabilidade inicial de `Used in world` com fallback `binary-id-scan`

- Parcial (fase atual):
  - Workflow oficial de upgrade (orientacao/UI pronta; automacao end-to-end ainda pendente)
  - Backup/snapshot transacional (metadados e historico prontos; rollback automatico ainda pendente)
  - Limpeza de legado/stale (varios itens removidos; auditoria final ainda pendente)
  - Module Health Check (endpoint + acao na UI + gate inicial no apply entregues; validacoes profundas finais ainda pendentes)
  - Migracao FastAPI/Clean (API principal em `backend/app` entregue; desacoplamento direto de `service/server.py` no backend concluido; desligamento final do legado ainda pendente)

- Pendente principal:
  - Automacao completa de rollback por lote
  - Module Health Check completo + validacoes profundas de pacote
  - Revisao final de licenca/compliance Foundry + limpeza final de legado
  - Migracao arquitetural completa para estrutura FastAPI/Clean sugerida

- [x] 1. Criar instrucoes para IA (`copilot-instructions`) e arquivos de colaboracao
- [x] 2. Adicionar autenticacao e hardening da API
- [x] 3. Implementar motor de execucao de acoes (catalogo, fila, status, auditoria, concorrencia, progresso)
- [x] 4. Adicionar empacotamento e self-hosting multiplataforma (`.deb`, Windows, Docker, `.app`)
- [x] 5. Configurar pipeline CI/CD (testes, build, release)

## Novo plano: Migracao de UI (API-first)

- [ ] Arquitetura alvo
  - Backend (Python): FastAPI
  - Frontend (UI): React + TypeScript (Vite)
  - Contrato entre camadas: JSON versionado (`/api/v1/...`)
  - Auth: cookie HttpOnly + CSRF
  - Decisao: nao usar JSF (stack Java fora do foco atual)

- [ ] Modelo de camadas (MVC + Clean/Hexagonal na borda)
  - Model: entidades e regras de dominio (Foundry, modulos, versoes, dependencias)
  - Controller: rotas HTTP (FastAPI routers)
  - View: React (componentes, paginas, estado de UI)
  - Services/Use Cases: regras de negocio entre controller e model

- [ ] Estrutura de diretorios sugerida
  - `backend/app/api/`
  - `backend/app/services/`
  - `backend/app/domain/`
  - `backend/app/repositories/`
  - `frontend/src/pages/`
  - `frontend/src/components/`
  - `frontend/src/features/report-v3/`
  - `frontend/src/services/api.ts`

- [ ] Estrategia de migracao sem quebra
  - [x] Congelar novo HTML no Python (somente manutencao corretiva)
  - [x] Extrair endpoints JSON para tudo que o `report_v3` usa
  - [x] Subir React com login + report_v3 primeiro
  - [x] Feature flag `USE_NEW_UI=true` para alternar UI antiga/nova
  - [x] Migrar telas restantes por fatias (config, actions, add module, status)
  - Remover renderizacao HTML legacy no backend ao atingir paridade

- [ ] Ferramentas recomendadas
  - Backend: FastAPI, Pydantic, Uvicorn
  - Frontend: React, TypeScript, TanStack Query, React Router
  - Qualidade: pytest, Playwright (E2E), Ruff, mypy
  - Contrato API: OpenAPI + geracao de client TypeScript

- [ ] Antes da migracao: limpeza de legado/stale
  - Mapear e remover pontes antigas e codigo morto sem uso em runtime/testes
  - Validar cada remocao com suite de testes
  - Revisar arquivo de licenca e tags/headers extras para normalizacao
  - Revisar compliance legal com licenca/termos do Foundry VTT para integracoes e distribuicao
  - [x] Entregue (fase atual):
    - remocao de componente UI legado nao utilizado (`DashboardPage.tsx`)

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

## Seguranca operacional extra

- deteccao de Foundry online aprimorada no `service/server.py`:
  - tcp probe + process probe no Windows
  - bloqueio de manutencao quando qualquer sinal indica Foundry em execucao
- testes adicionados em `tests/test_foundry_online_detection.py`
- [x] hardening adicional de auth/login:
  - usuario + senha (nao apenas senha)
  - lockout por tentativas com orientacao de recuperacao
  - protecao CSRF para endpoints autenticados `POST` (`X-CSRF-Token`)
  - rate limit global por IP (`RESOLVER_REQUEST_RATE_LIMIT_PER_MINUTE`)
  - auditoria inclui `userAgent` e `origin`

## Planning UI e Workflow (novo)

- [ ] Melhorar a tela de Planning (acoes por fases)
  - Exibir "stability score" por target Foundry com semaforo:
    - verde: alta cobertura / poucos bloqueios
    - amarelo: cobertura media / dependencias faltantes
    - vermelho: muitos bloqueios / risco alto
  - Unificar visual com Current:
    - cores iguais para `blocked`, `update`, `ready`, `missing`
    - coluna de acao orientada por botao (sem labels duplicadas)
  - Adicionar comparativo por versao alvo:
    - total de modulos atualizaveis
    - total bloqueado
    - total com missing dependency
    - percentual de cobertura esperado
  - Adicionar recomendacao automatica de "best target version":
    - criterio ponderado por cobertura, bloqueios, missing, e confianca
  - Adicionar explicacao de impacto:
    - "por que essa versao e recomendada"
    - "quais modulos/sistemas impedem targets superiores"
  - [x] Entregue (fase atual):
    - score por target com semaforo no view-model e UI
    - recomendacao automatica de melhor target com justificativa
    - filtros e tabela Planning alinhados ao padrao Current

- [ ] Definir workflow oficial de upgrade (produto)
  - Fluxo recomendado: atualizar modulos/sistemas primeiro, atualizar Foundry depois
  - Passo 1: dry-run e classificacao (current/planning)
  - Passo 2: aplicar updates de modulos/sistemas no target atual
  - Passo 3: criar snapshot/backup completo antes da troca de Foundry
  - Passo 4: atualizar Foundry para versao alvo escolhida
  - Passo 5: rodar novo scan e validar regressao pos-upgrade
  - Passo 6: opcao de rollback guiado usando backups gerados
  - [x] Entregue (fase atual):
    - workflow recomendado exibido na UI de Planning
    - painel de impacto por target (bloqueios e principais modulos bloqueadores)

- [ ] Backup/snapshot para mudancas em lote
  - Criar snapshot transacional antes de `Update All`
  - Registrar manifest/versionamento pre e pos por modulo
  - Expor restauracao por lote (rollback do batch inteiro)
  - Exibir historico de snapshots na UI (data, alvo, resultado)
  - [x] Entregue (fase atual):
    - historico de applies/snapshots exibido na aba Backups (quando, alvo, modulos alterados, backups criados)
    - endpoint/modelo inclui `applyHistory` a partir de `scan_runs` (SQLite)
    - payload de apply inclui `batchSnapshot` com pre/post versions e changedModules
    - endpoint de `rollback plan` por `scanRunId` + botao na UI para o lote mais recente

- [ ] Instalacao de modulos + higiene Foundry (novo)
  - Validar pacote baixado antes de aplicar:
    - verificar arquivos declarados no manifest (`styles`, `esmodules`, `scripts`)
    - abortar apply se arquivos obrigatorios nao existirem
  - Bloquear instalacao de manifests legados/incompativeis:
    - sinalizar `minimumCoreVersion`/`compatibleCoreVersion` como legado
    - preferir releases com `compatibility` valido para a versao alvo
  - Higiene automatica de diretorios em `Data/modules`:
    - ignorar e opcionalmente limpar `_backup_*` e placeholders `{{...}}`
  - Criar tela/acao de "Module Health Check":
    - detectar modulos invalidos para o Foundry atual
    - sugerir corrigir, reinstalar, remover ou restaurar backup
  - [x] Entregue (fase atual):
    - endpoint `GET /api/v1/actions/module-health`
    - botao de execucao na aba Backups com resumo imediato (`total/invalid/warnings`)
    - gate inicial no `apply`: preflight/postflight bloqueando quando houver `missing_file` e `missing_dependency`
    - validacao profunda de pacote no `apply_recommendation`:
      - protecao contra zip-slip (paths inseguros no zip)
      - bloqueio por `module.json` invalido/ausente
      - bloqueio por `id`/`version` inconsistentes com recomendacao
      - bloqueio por campos legados de core compatibility
      - bloqueio por arquivos declarados ausentes (`styles/scripts/esmodules`)

- [ ] Confiabilidade de "Used in world" (world moduleConfiguration)
  - Expandir parser de `core.moduleConfiguration` para variantes adicionais do LevelDB
  - Validar leitura com worlds reais onde `enabledModules` vinha vazio
  - Adicionar fallback por arquivos/indices auxiliares quando payload estiver binario/compactado
  - Cobrir com testes de regressao para `midi-qol`, Monk family e sistemas ativos
  - [x] Entregue (fase atual):
    - fallback `binary-id-scan` com ids conhecidos para worlds com payload LevelDB opaco
    - testes unitarios de regressao para parser tolerante e fallback binario

- [ ] Fluxo para dependencias faltantes sem URL conhecida
  - Na tabela Current, trocar acao `Get` por `Find Source` quando faltar `release/manifest URL`
  - `Find Source` abre nova aba com busca pronta (Google/GitHub) usando nome + id do modulo
  - Adicionar modal `Paste URL` para usuario colar `manifest/module.json/release URL`
  - Backend valida URL informada (manifest valido + compatibilidade + dependencias)
  - Salvar URL validada em catalogo local para reuso
  - Atualizar linha automaticamente: de `Blocked` para `Get/Update` quando houver metadata valida
  - [x] Entregue (fase atual):
    - `Find Source` + `Set URL` na tabela Current quando URL estiver ausente
    - endpoint backend para validar/salvar fonte em catalogo local (`/api/config/module-sources`)
    - reutilizacao de URL salva na renderizacao para habilitar acao na linha
    - recomendacao imediata apos salvar URL validada (feedback no fluxo)
