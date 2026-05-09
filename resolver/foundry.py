from __future__ import annotations

import json
import re
from pathlib import Path


def detect_foundry_version(data_root: str) -> tuple[str, str]:
    root = Path(data_root)
    diagnostics_path = root / "Logs" / "diagnostics.json"
    if diagnostics_path.exists():
        with diagnostics_path.open("r", encoding="utf-8") as handle:
            diagnostics = json.load(handle)
        foundry = diagnostics.get("foundry") or {}
        generation = foundry.get("generation")
        build = foundry.get("build")
        if generation is not None and build is not None:
            return f"{generation}.{build}", str(diagnostics_path)

    cache_root = root / "container_cache"
    if cache_root.exists():
        for entry in sorted(cache_root.iterdir(), reverse=True):
            match = re.fullmatch(r"foundryvtt-(\d+\.\d+)\.zip", entry.name)
            if match:
                return match.group(1), str(entry)

    raise ValueError(
        "Could not detect the Foundry version from the provided data root. "
        "Expected version metadata in Logs/diagnostics.json or container_cache/foundryvtt-*.zip."
    )
