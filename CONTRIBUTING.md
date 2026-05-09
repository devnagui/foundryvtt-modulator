# Contributing

## Fluxo basico

1. Crie uma branch com nome descritivo.
2. Implemente mudancas pequenas e revisaveis.
3. Inclua testes para cada alteracao funcional.
4. Rode a suite local antes de abrir PR.
5. Abra PR usando o template do repositorio.

## Padrao de mudancas

- Priorize retrocompatibilidade dos payloads JSON.
- Evite acoplamento forte entre `service/` e `resolver/`.
- Para acoes destrutivas, mantenha garantias de lock e rastreabilidade.

## Convencoes

- Linguagem: Python.
- Testes: `unittest` em `tests/`.
- Logs: informativos, sem expor credenciais.

## Checklist antes do PR

- Codigo compila/roda sem erro.
- Testes novos e existentes passando.
- Documentacao atualizada quando necessario.
- Sem segredos hardcoded.
