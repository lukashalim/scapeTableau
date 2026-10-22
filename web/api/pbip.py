"""Production POST /api/pbip — Power BI Project zip (default download)."""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve()
web_root = HERE.parents[1]
for candidate in (
    web_root / "bundled-engine",
    HERE.parents[2],
    web_root,
):
    if (candidate / "engine" / "__init__.py").exists():
        path = str(candidate)
        if path not in sys.path:
            sys.path.insert(0, path)
        break

from engine.vercel_fn import handler, pbip_payload

__all__ = ["handler", "pbip_payload"]
