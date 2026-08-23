#!/usr/bin/env python3
"""Build or dev-install the Calibre plugin.

``zip`` writes a self-contained ``AO3Scraper.zip`` for GitHub Releases
(plugin UI + ao3kit + vendored pure-Python deps). ``install`` still uses
``calibre-customize -b`` from ``calibre-plugin/`` for fast UI iteration.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PLUGIN_DIR = ROOT / "calibre-plugin"
AO3KIT_DIR = ROOT / "ao3kit"
OUTPUT = ROOT / "AO3Scraper.zip"
VENDOR_CACHE = ROOT / ".cache" / "plugin-vendor"
PLUGIN_REQUIREMENTS = ROOT / "requirements-plugin.txt"
NATIVE_SUFFIXES = {".so", ".pyd", ".dylib", ".dll"}
SKIP_DIR_NAMES = {
    "__pycache__",
    ".git",
    "bin",
    "tests",
}


def _purge_native_extensions(root: Path) -> None:
    for path in root.rglob("*"):
        if path.is_file() and path.suffix.lower() in NATIVE_SUFFIXES:
            path.unlink()


def vendor_requirements_hash() -> str:
    return hashlib.sha256(PLUGIN_REQUIREMENTS.read_bytes()).hexdigest()


def ensure_vendor(*, force: bool = False) -> Path:
    """pip-install plugin runtime deps into ``.cache/plugin-vendor``."""
    stamp = VENDOR_CACHE / ".requirements.sha256"
    req_hash = vendor_requirements_hash()
    if (
        not force
        and (VENDOR_CACHE / "requests" / "__init__.py").is_file()
        and (VENDOR_CACHE / "yaml" / "__init__.py").is_file()
        and stamp.is_file()
        and stamp.read_text(encoding="utf-8").strip() == req_hash
    ):
        return VENDOR_CACHE
    if VENDOR_CACHE.exists():
        shutil.rmtree(VENDOR_CACHE)
    VENDOR_CACHE.mkdir(parents=True, exist_ok=True)
    subprocess.check_call(
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            "--no-compile",
            "-q",
            "-r",
            str(PLUGIN_REQUIREMENTS),
            "--target",
            str(VENDOR_CACHE),
        ]
    )
    _purge_native_extensions(VENDOR_CACHE)
    stamp.write_text(req_hash + "\n", encoding="utf-8")
    return VENDOR_CACHE


def _should_skip(path: Path, root: Path) -> bool:
    rel_parts = path.relative_to(root).parts
    if any(part in SKIP_DIR_NAMES for part in rel_parts):
        return True
    if path.suffix in {".pyc", ".pyo"}:
        return True
    if path.suffix.lower() in NATIVE_SUFFIXES:
        return True
    return False


def iter_zip_entries(
    *,
    plugin_dir: Path = PLUGIN_DIR,
    ao3kit_dir: Path = AO3KIT_DIR,
    vendor_dir: Path | None = None,
) -> list[tuple[Path, str]]:
    """``(filesystem path, zip arcname)`` for a release plugin zip."""
    entries: list[tuple[Path, str]] = []
    import_name = plugin_dir / "plugin-import-name-ao3_scraper.txt"
    if not import_name.exists():
        raise SystemExit(f"Missing required file: {import_name}")
    for path in sorted(plugin_dir.glob("*.py")):
        entries.append((path, path.name))
    entries.append((import_name, import_name.name))
    images_dir = plugin_dir / "images"
    if images_dir.is_dir():
        for path in sorted(images_dir.rglob("*")):
            if not path.is_file() or path.suffix.lower() != ".png":
                continue
            entries.append((path, path.relative_to(plugin_dir).as_posix()))
    if not (ao3kit_dir / "__init__.py").is_file():
        raise SystemExit(f"Missing ao3kit package: {ao3kit_dir}")
    for path in sorted(ao3kit_dir.rglob("*")):
        if not path.is_file() or _should_skip(path, ao3kit_dir):
            continue
        rel = path.relative_to(ao3kit_dir).as_posix()
        entries.append((path, f"ao3kit/{rel}"))
    if vendor_dir is not None:
        for path in sorted(vendor_dir.rglob("*")):
            if not path.is_file() or _should_skip(path, vendor_dir):
                continue
            if path.name.startswith("."):
                continue
            rel = path.relative_to(vendor_dir).as_posix()
            entries.append((path, f"vendor/{rel}"))
    return entries


def build_zip(
    output: Path | None = None,
    *,
    vendor: bool = True,
    force_vendor: bool = False,
) -> Path:
    dest = output or OUTPUT
    vendor_dir = ensure_vendor(force=force_vendor) if vendor else None
    entries = iter_zip_entries(vendor_dir=vendor_dir)
    dest.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as zf:
        written: set[str] = set()
        for path, arcname in entries:
            if arcname in written:
                continue
            zf.write(path, arcname=arcname)
            written.add(arcname)
    print(f"Wrote {dest} ({dest.stat().st_size} bytes, {len(written)} files)")
    return dest


def _print_result(result: dict) -> int:
    message = str(result.get("message") or "")
    if message:
        print(message)
    if not result.get("ok"):
        extra = {
            key: result[key]
            for key in ("error", "holder", "agent_id", "pid", "started_at")
            if result.get(key) not in (None, "")
        }
        if extra:
            print(json.dumps(extra, indent=2))
        return 1
    return 0


def _ctl():
    from calibre_dev.calibre import CalibreCtl

    return CalibreCtl(plugin_dir=PLUGIN_DIR)


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = argparse.ArgumentParser(
        prog="makeplugin.py",
        description=(
            "Build a self-contained AO3Scraper.zip for GitHub Releases, or "
            "install the Calibre plugin from calibre-plugin/. Restart is "
            "opt-in and lock-aware."
        ),
    )
    parser.add_argument(
        "command",
        nargs="?",
        choices=("zip", "install", "restart", "status"),
        help="zip (default), install, restart, or status.",
    )
    parser.add_argument(
        "-i",
        "--install",
        action="store_true",
        help="Install the plugin (legacy; same as the install command).",
    )
    parser.add_argument(
        "-r",
        "--restart",
        action="store_true",
        help="With install: also quit and start Calibre (host-wide restart lock).",
    )
    parser.add_argument(
        "--lock-timeout",
        type=float,
        default=15.0,
        help="Seconds to wait for the restart lock (default 15). MCP default is 0.",
    )
    parser.add_argument(
        "--agent-id",
        default="",
        help="Label written to the restart lock (who is restarting).",
    )
    parser.add_argument(
        "--no-vendor",
        action="store_true",
        help="Zip plugin + ao3kit only (no pip vendor). For tests.",
    )
    parser.add_argument(
        "--force-vendor",
        action="store_true",
        help="Re-run pip install into .cache/plugin-vendor before zipping.",
    )
    args = parser.parse_args(argv)
    command = args.command
    if args.install:
        command = "install"
    restart = bool(args.restart)
    if command is None:
        if restart:
            parser.error("--restart requires install (or use: makeplugin.py restart)")
        build_zip(vendor=not args.no_vendor, force_vendor=args.force_vendor)
        return 0
    if command == "zip":
        build_zip(vendor=not args.no_vendor, force_vendor=args.force_vendor)
        return 0
    if command == "status":
        result = _ctl().status()
        print(json.dumps(result, indent=2))
        return 0 if result.get("ok") else 1
    if command == "install":
        result = _ctl().install(
            restart=restart,
            agent_id=args.agent_id,
            lock_timeout=args.lock_timeout,
            holder="makeplugin:install",
        )
        return _print_result(result)
    if command == "restart":
        result = _ctl().restart(
            agent_id=args.agent_id,
            lock_timeout=args.lock_timeout,
            holder="makeplugin:restart",
        )
        return _print_result(result)
    parser.error(f"Unknown command: {command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
