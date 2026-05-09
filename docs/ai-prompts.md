# AI Prompt Templates

## Bugfix

```
Contexto:
- Arquivo(s): <listar>
- Comportamento atual: <descrever>
- Comportamento esperado: <descrever>

Tarefa:
- Corrija o problema sem quebrar contratos JSON existentes.
- Adicione testes para reproduzir e validar o fix.
- Liste riscos e impacto.
```

## Refactor

```
Contexto:
- Modulo alvo: <listar>
- Objetivo: reduzir complexidade sem alterar comportamento externo.

Tarefa:
- Refatore incrementalmente.
- Mantenha assinaturas publicas.
- Garanta cobertura de testes para fluxo principal.
```

## Nova funcionalidade

```
Contexto:
- Requisito: <descrever>
- Restricoes: seguranca operacional, lock de manutencao, compatibilidade.

Tarefa:
- Implemente em etapas pequenas.
- Inclua testes unitarios e integracao.
- Atualize documentacao.
```

## Revisao de seguranca

```
Analise o diff com foco em:
- autenticacao/sessao
- validacao de input
- acoes destrutivas em disco
- logs sensiveis

Retorne:
- riscos por severidade
- recomendacao de mitigacao
```
