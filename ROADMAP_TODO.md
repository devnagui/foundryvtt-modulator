# Roadmap TODO (UI Unificada Current + Planning)

## Objetivo

Unificar a UI base de `Current` e `Planning`, reutilizando os mesmos componentes visuais e mantendo no `Planning` apenas o filtro extra de versao de Foundry.

## Lote atual

- [x] Reset do roadmap para foco na unificacao da UI
- [x] Criar componente compartilhado de painel/tabela para abas de update
- [x] Migrar `Current` para usar o componente compartilhado
- [x] Migrar `Planning` para usar o mesmo componente compartilhado
- [x] Manter filtro extra de Foundry apenas em `Planning` (linha acima dos filtros de sistema)
- [x] Consolidar render de linhas de `system` em helper compartilhado (Current + Planning)
- [x] Consolidar render de linhas de `module` em helpers compartilhados para reduzir duplicacao restante
- [x] Reaproveitar barra de acoes entre `Current` e `Planning` com variacoes por props
- [x] Revisar consistencia visual final (`Current` == `Planning`, exceto filtro Foundry)
- [x] Ajustar testes para cobrir o componente compartilhado e regressao visual/funcional
- [ ] Validacao manual completa de fluxo nas duas abas
