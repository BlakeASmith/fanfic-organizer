#!/usr/bin/env python3
"""Run bundled ao3kit from a plugin extract or ``calibre-debug -e``.

Calibre::

    calibre-debug -e run_ao3kit.py -- scrape -o results.jsonl

System Python::

    python3 run_ao3kit.py scrape -o results.jsonl
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VENDOR = ROOT / 'vendor'
if VENDOR.is_dir():
    sys.path.insert(0, str(VENDOR))
sys.path.insert(0, str(ROOT))

from ao3kit.cli import main

if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1:]))
