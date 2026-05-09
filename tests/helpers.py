import os
import shutil
import tempfile
from pathlib import Path


FIXTURES_ROOT = Path(__file__).resolve().parent / "fixtures"
MINIMAL_FOUNDRY_FIXTURE = FIXTURES_ROOT / "foundry_data_root_minimal"


def clone_fixture_tree(source: Path) -> tuple[tempfile.TemporaryDirectory, Path]:
    tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
    dst = Path(tmp.name) / source.name
    shutil.copytree(source, dst)
    return tmp, dst


def should_run_real_foundry_tests() -> bool:
    return str(os.environ.get("RUN_REAL_FOUNDRY_TESTS", "")).strip() == "1"


def real_foundry_data_root() -> str:
    return str(os.environ.get("REAL_FOUNDRY_DATA_ROOT", "")).strip()
