"""Vercel Python function at /api/engine when the project root is web/."""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve()
for candidate in (HERE.parents[2], HERE.parents[1], HERE.parents[1] / "bundled-engine"):
    if (candidate / "engine" / "__init__.py").exists():
        path = str(candidate)
        if path not in sys.path:
            sys.path.insert(0, path)
        break

from engine.vercel_fn import handler, transpile_payload

__all__ = ["handler", "transpile_payload"]
