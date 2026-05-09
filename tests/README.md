# Test Strategy

## Camadas

- Unitarios: validam regras isoladas (`foundry`, `local`, `dependencies`).
- Integracao mockada: usam fixture local `foundry_data_root_minimal` e mocks de rede.
- Integracao real (opcional): usam uma instalacao Foundry local.

## Execucao padrao

```powershell
python -m unittest discover -s tests -v
```

## Execucao com Foundry real (opcional)

Defina as variaveis:

- `RUN_REAL_FOUNDRY_TESTS=1`
- `REAL_FOUNDRY_DATA_ROOT=<caminho do seu data-root Foundry>`

Exemplo PowerShell:

```powershell
$env:RUN_REAL_FOUNDRY_TESTS = "1"
$env:REAL_FOUNDRY_DATA_ROOT = "D:\\foundry\\userdata"
python -m unittest tests.test_cli_integration.TestCliIntegration.test_real_foundry_data_root_dry_run_smoke -v
```
