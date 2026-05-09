# Foundry Module Version Resolver

Ferramenta externa para recomendar a melhor versão de módulos do Foundry VTT para uma versão-alvo do core.

## Licença e uso comercial

- Licença open-source: `AGPL-3.0-or-later` (veja [LICENSE](LICENSE)).
- Avisos de atribuição: veja [NOTICE](NOTICE).
- Se houver uso comercial (produto/serviço), o projeto solicita crédito visível ao autor/projeto.
- Para termos comerciais personalizados, entre em contato com o autor para licenciamento comercial.

## O que faz

- Lê os `module.json` instalados localmente
- Descobre automaticamente a versão do Foundry a partir da raiz de dados
- Lê as versões dos sistemas instalados a partir de `Data/systems`
- Tenta ler `Data/worlds/*/data/settings` via `plyvel` para descobrir módulos realmente habilitados por mundo
- Tenta buscar histórico remoto de releases
- Aplica regras de compatibilidade por `minimum`, `verified` e `maximum`
- Valida compatibilidade de sistema quando a release declara `relationships.systems`
- Valida `relationships.requires` contra outros módulos e propaga recomendações de dependências
- Cria mapa local de dependências diretas e transitivas
- Gera uma recomendação com motivo e nível de confiança
- Suporta log e `dry-run`
- Processa múltiplos módulos em batches com tamanho mínimo de 10
- Usa cache local para respostas HTTP e expansão progressiva de releases
- Salva historico normalizado em JSON por modulo dentro de `.cache/modules`
- Persiste um catalogo local normalizado em SQLite para mapear releases, compatibilidades, dependencias e uso por mundo
- Descobre releases futuras oficiais do Foundry a partir de `https://foundryvtt.com/releases/`
- Suporta `GITHUB_TOKEN` para reduzir rate limit do GitHub
- Mantem o cache sob controle com limite de tamanho, quantidade de arquivos e idade maxima
- Mantem o catalogo SQLite sob controle com retencao automatica dos ultimos scans e compactacao apos limpeza
- Pode aplicar upgrades automaticamente com backup
- Pode validar uma recomendacao fixada usando `--expected-version module_id=versao`
- Pode aplicar uma versao anterior do proprio modulo com `--allow-downgrade`
- Rebaixa a prioridade de releases que exigiriam rollback de dependências, buscando a última versão adequada do próprio módulo
- Pode gerar um relatório HTML consolidado com tabelas separadas para upgrades, módulos sem mudança, dependências e avisos
- Gera tambem um `view model` separado em `reportViews.v2` para a interface HTML mais nova
- Grava por padrao `log`, `json` e `html` em `config/foundryModuleVersioningTool/reports`
- Mantem snapshots diarios por 3 dias e preserva logs de execucoes com `--apply`
- Pode publicar o relatorio `latest` via nginx em HTTPS

## Estrutura

- `resolver/cli.py`: entrada da linha de comando
- `resolver/local.py`: leitura de módulos locais
- `resolver/local.py`: leitura de módulos e sistemas locais
- `resolver/foundry.py`: descoberta da versão do Foundry
- `resolver/sources.py`: coleta de histórico remoto
- `resolver/scoring.py`: validação e ranqueamento de releases
- `resolver/models.py`: modelos de dados
- `resolver/db.py`: persistencia local em SQLite do catalogo normalizado/grafo
- `resolver/report_view_v2.py`: montagem do view model independente da interface HTML
- `resolver/report_v2.py`: renderer HTML da interface v2

## Uso

### Um modulo

```bash
python3 -m resolver.cli \
  --data-root /home/engrenado/foundry/data \
  --module midi-qol \
  --pretty \
  --dry-run
```

### Multiplos modulos

Repita `--module` para cada modulo desejado:

```bash
python3 -m resolver.cli \
  --data-root /home/engrenado/foundry/data \
  --module midi-qol \
  --module crlngn-ui \
  --module dae \
  --pretty \
  --dry-run \
  --batch-size 10
```

### Relatorio HTML

```bash
python3 -m resolver.cli \
  --data-root /home/engrenado/foundry/data \
  --pretty \
  --dry-run \
  --batch-size 10
```

O HTML inclui:

- tabela com releases futuras oficiais do Foundry publicadas depois da versao instalada
- tabela de modulos com upgrade recomendado
- tabela de modulos sem necessidade de atualizacao
- tabela de casos que precisam de revisao manual
- tabela de atualizacoes de dependencias
- tabela de dependencias faltantes
- tabela de avisos coletados durante a resolucao

Arquivos padrao gerados:

- `reports/module-resolver-latest.log`
- `reports/module-resolver-latest.json`
- `reports/module-resolver-latest.html`

Os botoes do HTML copiam comandos prontos para o console com:

- `cd` para a pasta da ferramenta
- `--module` para os modulos da tabela
- `--expected-version` para fixar a recomendacao exibida no relatorio
- `--allow-downgrade` quando a tabela representa uma versao anterior compativel

### Cache

Por padrao o cache:

- usa `.cache/`
- limita o total a `512 MB`
- limita a `5000` arquivos
- remove arquivos com mais de `30` dias

Voce pode ajustar isso na CLI:

```bash
python3 -m resolver.cli \
  --data-root /home/engrenado/foundry/data \
  --dry-run \
  --cache-max-mb 256 \
  --cache-max-files 3000 \
  --cache-max-age-days 14
```

### Banco local

Por padrao a ferramenta tambem grava um banco SQLite local em:

- `state/resolver.db`

Esse banco guarda:

- snapshots de execucao
- modulos e sistemas instalados
- mundos e modulos habilitados por mundo
- releases normalizadas
- arestas de compatibilidade com Foundry e sistemas
- arestas de dependencias

Politica padrao do banco:

- mantem os ultimos `20` scans
- remove releases do catalogo que nao aparecem mais nessa janela retida
- executa `VACUUM` quando houver limpeza real para recuperar espaco em disco

Voce pode alterar o caminho:

```bash
python3 -m resolver.cli \
  --data-root /home/engrenado/foundry/data \
  --dry-run \
  --db-max-scan-runs 20 \
  --database-path /home/engrenado/config/foundryModuleVersioningTool/state/resolver.db
```

### Todos os modulos

Nao passe `--module` para analisar tudo que estiver em `Data/modules`:

```bash
python3 -m resolver.cli \
  --data-root /home/engrenado/foundry/data \
  --pretty \
  --dry-run \
  --batch-size 10
```

### Dry-run diario agendado

Script diario:

```bash
/home/engrenado/config/foundryModuleVersioningTool/scripts/run_full_dry_run.sh
```

Agendamento instalado no `crontab` do usuario:

```bash
15 4 * * * /home/engrenado/config/foundryModuleVersioningTool/scripts/run_full_dry_run.sh
```

Retencao:

- snapshots diarios em `reports/daily/` por 3 dias
- `reports/module-resolver-latest.*` sempre apontam para a ultima execucao diaria
- execucoes com `--apply` geram arquivos arquivados em `reports/applied/`

Publicacao HTTPS:

- HTML: `https://engrenado.brazilsouth.cloudapp.azure.com/module-resolver/`
- JSON: `https://engrenado.brazilsouth.cloudapp.azure.com/module-resolver/report.json`

### Aplicar upgrades

```bash
python3 -m resolver.cli \
  --data-root /home/engrenado/foundry/data \
  --module crlngn-ui \
  --apply \
  --batch-size 10
```

## Observações

- Usa apenas biblioteca padrão do Python
- Para leitura nativa de `LevelDB` dos mundos, instale `plyvel`; sem ela a ferramenta usa fallback por leitura binária e pode deixar alguns mundos como unresolved
- Atualmente suporta coleta remota via GitHub e GitLab
- Se a rede ou o histórico remoto falharem, cai para o manifesto local
- Detecta a versão do Foundry primeiro por `Logs/diagnostics.json` e depois por `container_cache/foundryvtt-*.zip`

## Resolver API local (senha + Docker)

Agora a ferramenta inclui um serviço HTTP local em `service/server.py` com:

- senha local com hash PBKDF2 (`state/auth.json`)
- sessão por cookie HttpOnly (`mm_session`)
- pré-condição de manutenção: Foundry deve estar offline
- lock de manutenção (`state/maintenance.lock.json`) para impedir ações destrutivas concorrentes

### Endpoints principais

- `GET /api/health`
- `GET /api/auth/status`
- `POST /api/auth/setup`
- `POST /api/auth/login`
- `POST /api/auth/logout`
- `POST /api/actions/dry-run`
- `POST /api/actions/apply`
- `POST /api/actions/force-compat`
- `POST /api/actions/cleanup-backups`
- `GET /api/report/latest`

### Rodando em Docker (porta local para reverse proxy)

```bash
cd /home/engrenado/config/foundryModuleVersioningTool
docker compose -f docker-compose.resolver.yml up -d --build
```

A API/UI fica publicada apenas localmente no host:

- `http://127.0.0.1:8787/`

Para acesso externo, faça publish pelo seu nginx/reverse proxy.
Em outros servidores, cada usuário decide como expor.

### Variáveis de ambiente

Veja `.env.resolver.example`. As mais importantes:

- `RESOLVER_DATA_ROOT` (ex: `/foundry-data`)
- `RESOLVER_REQUIRE_FOUNDRY_OFFLINE=true`
- `RESOLVER_FOUNDRY_HOST` e `RESOLVER_FOUNDRY_PORT`
- `RESOLVER_COOKIE_SECURE` (use `true` quando estiver atrás de HTTPS)
