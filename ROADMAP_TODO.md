# Roadmap TODO (Reset)

## Sprint atual (rebuild da aba Planning)

- [x] Remover implementacao atual da aba `Planning` (UI + estado + regras locais antigas)
- [x] Reutilizar a estrutura da aba `Current` como base unica da nova `Planning`
- [x] Posicionar filtro de versao do Foundry em uma linha acima do filtro de sistema
- [x] Adicionar filtro unico de versao de Foundry no topo da `Planning` (selecao unica)
- [x] Popular filtro com versoes disponiveis de Foundry em ordem crescente
- [x] Ao trocar Foundry no filtro, recalcular sugestoes de update de sistemas
- [x] Ao trocar Foundry no filtro, recalcular sugestoes de update de modulos
- [x] Garantir que classificacao/status siga as mesmas regras canonicas da `Current`
- [x] Garantir que `Update Path` reflita a versao alvo derivada do Foundry selecionado
- [x] Reaproveitar badges/tooltips/acoes visuais da `Current` sem divergencia
- [x] Ajustar backend payload/modelo, se necessario, para suportar `Planning` por Foundry alvo
- [ ] Cobrir cenarios com testes de apresentacao (planning com foundry atual/futuro)
- [ ] Cobrir cenarios com testes de regra de negocio (compatibilidade foundry + sistema + modulo)
- [x] Otimizar carregamento da `Planning` para tempo minimo de resposta (cache/memoizacao/preprocessamento)
- [x] Validar performance ao trocar filtro (sem re-scan completo e sem travas perceptiveis)
- [ ] Validacao manual final: comparativo `Current` vs `Planning` para garantir consistencia
- [x] Review pipelines for mac and ubuntu, both failing at github, packaging never executed
- [x] Recreate simple README focused on modulator core business and step-by-step usage guide, including troubleshooting and CLI usage
- [x] Review tooltip behavior on mobile and provide interaction alternative
- [ ] Do a full test:
  - [ ] Review update logics
  - [ ] Workflow of updating Foundry version (systems/modules move to compatible targets for new Foundry)
  - [ ] Update a system (dependent modules move to the selected system version)
  - [ ] Update a module (module remains compatible with system + Foundry constraints)
  - [ ] Snapshot backup flow (JSON baseline + optional zip backup + intended target versions visibility)
  - [x] Export modules snapshot (JSON with installed modules/systems for current Foundry) via `POST /api/v1/report/v3/export-snapshot`
