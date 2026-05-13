# Copilot Instructions

## Contexto do projeto

Este repositorio implementa um resolvedor de versoes de modulos para Foundry VTT com dois pontos de entrada:

- CLI: `python -m resolver.cli`
- API HTTP (padrao): `uvicorn backend.app.main:app --host 0.0.0.0 --port 8787`
- Frontend UI: `frontend/` (React + TypeScript + Vite + TailwindCSS + daisyUI)

O objetivo principal e recomendar versoes compativeis com o Foundry instalado, com foco em seguranca operacional para acoes destrutivas.

## Regras tecnicas

- Priorize Python da biblioteca padrao.
- Preserve compatibilidade com o fluxo atual da CLI e da API.
- Evite alterar contratos JSON existentes sem necessidade.
- Sempre considerar o impacto em `reports/`, `state/resolver.db` e cache.
- Antes de iniciar qualquer implementacao, ler toda a queue/TODO/roadmap atual (itens abertos e pendentes) para evitar retrabalho e regressao de contexto.
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
- Nao expor `/api/v1/*` publicamente sem protecao explicita (allowlist IP, VPN, ou auth adicional no proxy).
- Em self-hosting, preferir `docker-compose.selfhost.yml` + `deploy/nginx/modulator.conf` (API restrita por default).

## Testes

- Toda mudanca funcional deve incluir testes unitarios e/ou integracao.
- Preferir testes hermeticos com diretorios temporarios e mocks de rede.
- Em mudancas de frontend, garantir `npm run build` em `frontend/`.
- Regra operacional da sessao: a cada lote de mudancas, executar tambem os testes relevantes antes de concluir.
- Fluxo minimo esperado por lote:
  - Backend alterado: `python -m unittest ...` (ou suite alvo) + `python -m py_compile ...`.
  - Frontend alterado: `npm run test` e `npm run build` em `frontend/`.
- Nao considerar tarefa concluida sem validacao de testes/build no mesmo lote (salvo bloqueio explicito de ambiente).

## Mudancas esperadas por area

- `resolver/`: regras de compatibilidade, resolucao, relatorios, persistencia.
- `backend/app/`: autenticacao, sessao, lock, endpoints, execucao de jobs (FastAPI/Clean).
- `frontend/`: login, report por abas, modais (settings/add-module), i18n basico, dark/light.
- `scripts/`: operacao diaria e manutencao.

## Regras visuais e UX (consolidadas da sessao)

- A tabela deve priorizar ordenacao por status: `missing` > `blocked` > `update` > `ready`.
- Coluna de status deve usar badges iconicos com tooltip; evitar texto redundante.
- Badges de compatibilidade devem ser separados:
  - `F*` para Foundry.
  - `S*` para System.
  - Cores: verde=valido, vermelho=incompativel, amarelo=incerto.
- Regra de tooltip de compatibilidade:
  - "incompatible" somente quando houver quebra explicita de `min/max/verified`.
  - Quando faltar metadata, usar "uncertain", nunca "incompatible".
- Tooltip de dependencia faltante deve seguir o formato:
  - `missing dependency: <missing>`.
- Se o modulo estiver `Ready`, `Update Path` deve mostrar apenas versao atual.
- Se houver resolucao pendente de versao/URL, pode mostrar loading, mas evitar `- -> -`.
- Botao de busca de source deve ser icone de lupa (`🔍`) e alinhado na mesma linha de `Set URL` quando aplicavel.
- Rotulo de acao principal da linha de falta deve ser `Install` (amarelo).
- Botao agregado deve ser `Fix All` (nao `Update All`) e atuar apenas em modulos com source configurada.
- Botao `Ready` e somente estado visual (nao clicavel).
- Em tabelas, remover ruido visual:
  - sem coluna `Used In` no layout atual.
  - sem label textual "Actions" quando o botao por si ja representa a acao.
- Coluna de acoes deve ficar alinhada a direita em desktop e mobile.
- Botao `Add Module` deve ficar alinhado a direita.
- Em mobile, evitar forcar largura total de botoes de acoes da tabela.
- Botoes com icone + texto (ex.: Scan, Add Module) devem manter icone centralizado/alinhado verticalmente com o texto.
- Filtros de versao/sistema:
  - Devem aparecer acima dos filtros de modulo.
  - Devem iniciar com selecao default no sistema/versao corrente.
  - Em `Current`, exibir versoes futuras e ate 2 versoes anteriores para cada sistema.
  - Um pill por sistema, com cabecalho usando o id correto do sistema (ex.: `dnd5e`).
  - Em cada pill, usar seletor unico de versao do sistema.
- `Add Module` deve abrir em modal (nao expandir form inline abaixo da tabela).
- Sempre replicar padroes visuais equivalentes entre abas `Current` e `Planning`.
