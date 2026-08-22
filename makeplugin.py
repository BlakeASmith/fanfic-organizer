#!/usr/bin/env python3
"""Build or dev-install the Calibre plugin."""

from __future__ import annotations

import argparse
import json
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PLUGIN_DIR = ROOT / "calibre-plugin"
OUTPUT = ROOT / "AO3Scraper.zip"


def build_zip() -> Path:
    with zipfile.ZipFile(OUTPUT, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(PLUGIN_DIR.glob("*.py")):
            zf.write(path, arcname=path.name)
        import_name = PLUGIN_DIR / "plugin-import-name-ao3_scraper.txt"
        if not import_name.exists():
            raise SystemExit(f"Missing required file: {import_name}")
        zf.write(import_name, arcname=import_name.name)
    print(f"Wrote {OUTPUT}")
    return OUTPUT


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
            "Build AO3Scraper.zip or install the Calibre plugin from "
            "calibre-plugin/. Restart is opt-in and lock-aware."
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
    args = parser.parse_args(argv)
    command = args.command
    if args.install:
        command = "install"
    restart = bool(args.restart)
    if command is None:
        if restart:
            parser.error("--restart requires install (or use: makeplugin.py restart)")
        build_zip()
        return 0
    if command == "zip":
        build_zip()
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
