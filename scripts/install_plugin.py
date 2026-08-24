#!/usr/bin/env python3
"""Bootstrap for the Fanfic Organizer curl installer.

From a git checkout this delegates to ``calibre_dev.install_release``. When the
script is downloaded alone (curl pipe), it fetches a small stdlib-only module
bundle from GitHub and runs the same installer (no ao3kit dependency).
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import urllib.request
from pathlib import Path

REPO = "BlakeASmith/fanfic-organizer"
REF = os.environ.get("FANFIC_ORGANIZER_INSTALL_REF", "main").strip() or "main"
MODULE_URLS = {
    "calibre_dev/release_urls.py": (
        f"https://raw.githubusercontent.com/{REPO}/{REF}/calibre_dev/release_urls.py"
    ),
    "calibre_dev/plugin_install.py": (
        f"https://raw.githubusercontent.com/{REPO}/{REF}/calibre_dev/plugin_install.py"
    ),
    "calibre_dev/install_release.py": (
        f"https://raw.githubusercontent.com/{REPO}/{REF}/calibre_dev/install_release.py"
    ),
}


def _run_from_checkout() -> int | None:
    root = Path(__file__).resolve().parents[1]
    if not (root / "calibre_dev" / "install_release.py").is_file():
        return None
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    from calibre_dev.install_release import main

    return main()


def _download_modules(dest_root: Path) -> None:
    package_dir = dest_root / "calibre_dev"
    package_dir.mkdir(parents=True, exist_ok=True)
    (package_dir / "__init__.py").write_text(
        '"""Curl installer bundle (stdlib-only)."""\n',
        encoding="utf-8",
    )
    for rel_path, url in MODULE_URLS.items():
        target = dest_root / rel_path
        target.parent.mkdir(parents=True, exist_ok=True)
        with urllib.request.urlopen(url, timeout=120) as response:
            data = response.read()
        if not data:
            raise RuntimeError(f"Download from {url} was empty.")
        target.write_bytes(data)


def _run_from_downloaded_bundle() -> int:
    bundle_dir = Path(tempfile.mkdtemp(prefix="fanfic-organizer-installer-"))
    try:
        _download_modules(bundle_dir)
        if str(bundle_dir) not in sys.path:
            sys.path.insert(0, str(bundle_dir))
        from calibre_dev.install_release import main

        return main()
    finally:
        shutil.rmtree(bundle_dir, ignore_errors=True)


def main() -> int:
    checkout_code = _run_from_checkout()
    if checkout_code is not None:
        return checkout_code
    return _run_from_downloaded_bundle()


if __name__ == "__main__":
    raise SystemExit(main())
