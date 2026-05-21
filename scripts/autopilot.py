#!/usr/bin/env python3
from __future__ import annotations

import runpy
import sys
from pathlib import Path


def _kit_root() -> Path:
    return Path(__file__).resolve().parents[1]


def main() -> int:
    kit = _kit_root() / "scripts" / "omc.py"
    if not kit.exists():
        raise FileNotFoundError(kit)
    sys.argv = [str(kit), "autopilot", *sys.argv[1:]]
    runpy.run_path(str(kit), run_name="__main__")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
