from __future__ import annotations

import os

import uvicorn


def main() -> None:
    host = os.environ.get("RESOLVER_BIND_HOST", "0.0.0.0")
    port = int(os.environ.get("RESOLVER_BIND_PORT", "8787"))
    uvicorn.run("backend.app.main:app", host=host, port=port)


if __name__ == "__main__":
    main()
