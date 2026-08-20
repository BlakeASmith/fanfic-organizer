#!/usr/bin/env python3
"""Build AO3Scraper.zip for installation in Calibre."""

from __future__ import annotations

import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PLUGIN_DIR = ROOT / 'calibre-plugin'
OUTPUT = ROOT / 'AO3Scraper.zip'


def main() -> None:
    with zipfile.ZipFile(OUTPUT, 'w', zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(PLUGIN_DIR.glob('*.py')):
            zf.write(path, arcname=path.name)
        import_name = PLUGIN_DIR / 'plugin-import-name-ao3_scraper.txt'
        if not import_name.exists():
            raise SystemExit(f'Missing required file: {import_name}')
        zf.write(import_name, arcname=import_name.name)
    print(f'Wrote {OUTPUT}')


if __name__ == '__main__':
    main()
