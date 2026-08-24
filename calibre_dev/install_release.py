"""Install the Fanfic Organizer plugin from a GitHub release zip."""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

from calibre_dev.calibre_install import ensure_calibre_customize
from calibre_dev.plugin_install import (
    apply_fanfic_organizer_gui_names,
    find_calibre,
    find_calibre_customize,
    list_calibre_gui_pids,
    remove_legacy_calibre_plugins,
    start_calibre_gui,
)
from calibre_dev.release_urls import RELEASE_ZIP_NAME, release_zip_url


def _log(message: str) -> None:
    print(f"fanfic-organizer install: {message}")


def download_release_zip(
    dest: Path,
    *,
    version: str | None = None,
    url: str | None = None,
) -> str:
    """Download ``fanfic-organizer.zip`` and return the URL used."""
    download_url = url or release_zip_url(version)
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        with urllib.request.urlopen(download_url, timeout=120) as response:
            data = response.read()
    except urllib.error.HTTPError as exc:
        raise RuntimeError(
            f"Could not download {download_url} (HTTP {exc.code})."
        ) from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Could not download {download_url}: {exc.reason}.") from exc
    dest.write_bytes(data)
    if not data:
        raise RuntimeError(f"Download from {download_url} was empty.")
    return download_url


def install_release_zip(
    zip_path: Path,
    *,
    customize: str | None = None,
    remove_legacy: bool = True,
    apply_gui_names: bool = True,
) -> None:
    """Install ``fanfic-organizer.zip`` with ``calibre-customize -a``."""
    customize_bin = customize or find_calibre_customize()
    if remove_legacy:
        remove_legacy_calibre_plugins(customize_bin)
    subprocess.run(
        [customize_bin, "-a", str(zip_path)],
        check=True,
        capture_output=True,
        text=True,
        timeout=120,
    )
    if apply_gui_names:
        apply_fanfic_organizer_gui_names()


def post_install_message(
    *,
    calibre_bin: str | None = None,
    running: bool | None = None,
    started: bool = False,
) -> str:
    binary = calibre_bin or find_calibre()
    is_running = running if running is not None else bool(list_calibre_gui_pids())
    if is_running:
        return (
            "Fanfic Organizer is installed. Calibre is already running — "
            "quit Calibre completely and open it again so the plugin loads."
        )
    if started:
        return "Fanfic Organizer is installed and Calibre was started."
    return (
        "Fanfic Organizer is installed. Start Calibre to use the plugin "
        f"({binary})."
    )


def run_install(
    *,
    version: str | None = None,
    zip_url: str | None = None,
    zip_path: Path | None = None,
    start_if_not_running: bool = True,
    customize: str | None = None,
    install_calibre_if_missing: bool = True,
) -> dict[str, object]:
    """Download (unless ``zip_path`` is set), install, and start or prompt."""
    cleanup_dir: Path | None = None
    installed_from = zip_path
    download_url = zip_url
    try:
        customize_bin = customize
        if customize_bin is None:
            if install_calibre_if_missing:
                from calibre_dev.calibre_install import try_find_calibre_tool

                if try_find_calibre_tool("calibre-customize") is None:
                    _log("Calibre not found — installing")
            customize_bin = ensure_calibre_customize(
                install_if_missing=install_calibre_if_missing,
            )
        if installed_from is None:
            cleanup_dir = Path(tempfile.mkdtemp(prefix="fanfic-organizer-install-"))
            installed_from = cleanup_dir / RELEASE_ZIP_NAME
            download_url = release_zip_url(version)
            _log(f"Downloading {download_url}")
            download_url = download_release_zip(
                installed_from,
                version=version,
                url=zip_url,
            )
        _log("Removing legacy plugins (if present)")
        install_release_zip(installed_from, customize=customize_bin)
        _log("Installed Fanfic Organizer")
        calibre_bin = find_calibre()
        was_running = bool(list_calibre_gui_pids())
        started = False
        if start_if_not_running and not was_running:
            _log("Starting Calibre")
            start_calibre_gui(calibre_bin)
            started = True
        elif was_running:
            _log(
                "Calibre is running — quit completely and reopen so the plugin loads"
            )
        message = post_install_message(
            calibre_bin=calibre_bin,
            running=was_running,
            started=started,
        )
        return {
            "ok": True,
            "zip_path": str(installed_from),
            "download_url": download_url,
            "was_running": was_running,
            "started": started,
            "message": message,
        }
    except FileNotFoundError as exc:
        return {"ok": False, "error": "not_found", "message": str(exc)}
    except RuntimeError as exc:
        msg = str(exc)
        code = (
            "download_failed"
            if msg.startswith("Could not download")
            else "calibre_install_failed"
        )
        return {"ok": False, "error": code, "message": msg}
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or str(exc)).strip()
        return {
            "ok": False,
            "error": "install_failed",
            "message": detail or f"calibre-customize exited {exc.returncode}",
        }
    finally:
        if cleanup_dir is not None:
            shutil.rmtree(cleanup_dir, ignore_errors=True)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Install Fanfic Organizer from the latest GitHub release.",
    )
    parser.add_argument(
        "--version",
        metavar="TAG",
        help="Install a specific release tag (for example 0.26.1 or v0.26.1).",
    )
    parser.add_argument(
        "--zip",
        type=Path,
        metavar="PATH",
        help="Install an existing fanfic-organizer.zip instead of downloading.",
    )
    parser.add_argument(
        "--no-install-calibre",
        action="store_true",
        help="Do not download and install Calibre when calibre-customize is missing.",
    )
    parser.add_argument(
        "--no-start",
        action="store_true",
        help="Do not start Calibre when it is not already running.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    result = run_install(
        version=args.version,
        zip_path=args.zip,
        start_if_not_running=not args.no_start,
        install_calibre_if_missing=not args.no_install_calibre,
    )
    message = str(result.get("message") or "Install finished.")
    if result.get("ok"):
        print(message)
        return 0
    print(message, file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
