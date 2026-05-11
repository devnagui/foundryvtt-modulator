# Copilot Instructions

## Contexto do projeto

Este repositorio implementa um resolvedor de versoes de modulos para Foundry VTT com dois pontos de entrada:

- CLI: `python -m resolver.cli`
- API HTTP: `python -m service.server`
- Frontend UI: `frontend/` (React + TypeScript + Vite + TailwindCSS + daisyUI)

O objetivo principal e recomendar versoes compativeis com o Foundry instalado, com foco em seguranca operacional para acoes destrutivas.

## Regras tecnicas

- Priorize Python da biblioteca padrao.
- Preserve compatibilidade com o fluxo atual da CLI e da API.
- Evite alterar contratos JSON existentes sem necessidade.
- Sempre considerar o impacto em `reports/`, `state/resolver.db` e cache.
- Preferir `/api/v1/*` para novas integracoes de frontend (mantendo backward compatibility em `/api/*`).
- A UI React e servida por feature flag:
  - `USE_NEW_UI=true`
  - opcional `RESOLVER_UI_DIST_DIR`
  - opcional `RESOLVER_DISABLE_LEGACY_REPORT_UI=true`

## Seguranca operacional

- Acoes destrutivas devem respeitar janelas de manutencao e lock.
- Nunca remover/modificar arquivos fora do escopo de `Data/modules` e `Backups/modules`.
- Sempre manter trilha de auditoria para acoes sensiveis.
- Quando Foundry path nao estiver validado, UI deve bloquear scans/acoes e orientar configuracao.

## Testes

- Toda mudanca funcional deve incluir testes unitarios e/ou integracao.
- Preferir testes hermeticos com diretorios temporarios e mocks de rede.
- Em mudancas de frontend, garantir `npm run build` em `frontend/`.

## Mudancas esperadas por area

- `resolver/`: regras de compatibilidade, resolucao, relatorios, persistencia.
- `service/`: autenticacao, sessao, lock, endpoints, execucao de jobs.
- `frontend/`: login, report por abas, modais (settings/add-module), i18n basico, dark/light.
- `scripts/`: operacao diaria e manutencao.
