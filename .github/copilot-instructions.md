# Copilot Instructions

## Contexto do projeto

Este repositorio implementa um resolvedor de versoes de modulos para Foundry VTT com dois pontos de entrada:

- CLI: `python -m resolver.cli`
- API HTTP: `python -m service.server`

O objetivo principal e recomendar versoes compativeis com o Foundry instalado, com foco em seguranca operacional para acoes destrutivas.

## Regras tecnicas

- Priorize Python da biblioteca padrao.
- Preserve compatibilidade com o fluxo atual da CLI.
- Evite alterar contratos JSON existentes sem necessidade.
- Sempre considerar o impacto em `reports/`, `state/resolver.db` e cache.

## Seguranca operacional

- Acoes destrutivas devem respeitar janelas de manutencao e lock.
- Nunca remover/modificar arquivos fora do escopo de `Data/modules` e `Backups/modules`.
- Sempre manter trilha de auditoria para acoes sensiveis.

## Testes

- Toda mudanca funcional deve incluir testes unitarios e/ou integracao.
- Preferir testes hermeticos com diretorios temporarios e mocks de rede.

## Mudancas esperadas por area

- `resolver/`: regras de compatibilidade, resolucao, relatorios, persistencia.
- `service/`: autenticacao, sessao, lock, endpoints, execucao de jobs.
- `scripts/`: operacao diaria e manutencao.
