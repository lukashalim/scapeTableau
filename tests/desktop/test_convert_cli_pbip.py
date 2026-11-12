"""Desktop sidecar writes an unpacked Power BI project, not only a zip."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SAMPLE = ROOT / "samples" / "superstore_calcs.twb"


def test_pbip_cli_writes_openable_folder(tmp_path: Path) -> None:
    dest = tmp_path / "project"
    proc = subprocess.run(
        [
            sys.executable,
            str(ROOT / "desktop" / "convert_cli.py"),
            "pbip",
            str(SAMPLE),
            str(dest),
        ],
        capture_output=True,
        text=True,
        cwd=ROOT,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr or proc.stdout
    payload = json.loads(proc.stdout.strip().splitlines()[-1])
    assert payload["ok"] is True
    assert payload["format"] == "pbip"
    out = Path(payload["output"])
    assert out.suffix == ".pbip"
    assert out.is_file()
    assert (out.parent / f"{out.stem}.Report").is_dir()
    assert (out.parent / f"{out.stem}.SemanticModel").is_dir()
