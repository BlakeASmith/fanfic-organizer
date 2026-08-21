#!/usr/bin/env python3
"""Build or dev-install the Calibre plugin."""

from __future__ import annotations

import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PLUGIN_DIR = ROOT / 'calibre-plugin'
OUTPUT = ROOT / 'AO3Scraper.zip'


def find_calibre_customize() -> str:
    customize = shutil.which('calibre-customize')
    if customize:
        return customize
    mac_path = Path('/Applications/calibre.app/Contents/MacOS/calibre-customize')
    if mac_path.exists():
        return str(mac_path)
    raise SystemExit(
        'calibre-customize not found. Install Calibre or add it to PATH.'
    )


def build_zip() -> Path:
    with zipfile.ZipFile(OUTPUT, 'w', zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(PLUGIN_DIR.glob('*.py')):
            zf.write(path, arcname=path.name)
        import_name = PLUGIN_DIR / 'plugin-import-name-ao3_scraper.txt'
        if not import_name.exists():
            raise SystemExit(f'Missing required file: {import_name}')
        zf.write(import_name, arcname=import_name.name)
    print(f'Wrote {OUTPUT}')
    return OUTPUT


def dev_install() -> None:
    """Zip plugin from calibre-plugin/ and install into Calibre."""
    customize = find_calibre_customize()
    subprocess.run([customize, '-b', str(PLUGIN_DIR)], check=True)
    print('Plugin installed. Restart Calibre to load code changes.')


def main(argv: list[str]) -> None:
    if len(argv) > 1 and argv[1] in ('install', '--install', '-i'):
        dev_install()
        return
    build_zip()


if __name__ == '__main__':
    main(sys.argv)
