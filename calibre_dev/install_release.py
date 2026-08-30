"""Install the Fanfic Organizer plugin from a GitHub release zip."""

from __future__ import annotations

import argparse
import json
import os
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
from calibre_dev.release_urls import (
    GITHUB_REPO,
    RELEASE_ZIP_NAME,
    actions_artifact_zip_api_url,
    is_actions_artifact_url,
    parse_actions_artifact_url,
    pick_zip_download_url,
    release_zip_url,
    release_zip_urls,
)

GITHUB_LATEST_API = (
    f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
)
USER_AGENT = "Fanfic-Organizer-Installer"


def _log(message: str) -> None:
    print(f"fanfic-organizer install: {message}")


def github_auth_token() -> str | None:
    """Token for Actions artifact downloads (``GITHUB_TOKEN``, ``GH_TOKEN``, or ``gh``)."""
    for key in ("GITHUB_TOKEN", "GH_TOKEN"):
        value = (os.environ.get(key) or "").strip()
        if value:
            return value
    try:
        completed = subprocess.run(
            ["gh", "auth", "token"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None
    if completed.returncode != 0:
        return None
    token = (completed.stdout or "").strip()
    return token or None


def latest_zip_candidates() -> list[str]:
    """Resolve the latest standard-release zip, preferring the versioned name."""
    urls: list[str] = []
    try:
        request = urllib.request.Request(
            GITHUB_LATEST_API,
            headers={
                "Accept": "application/vnd.github+json",
                "User-Agent": USER_AGENT,
            },
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError, TimeoutError):
        payload = None
    if isinstance(payload, dict):
        picked = pick_zip_download_url(
            payload.get("assets") or [],
            tag=str(payload.get("tag_name") or ""),
        )
        if picked:
            urls.append(picked)
        tag = str(payload.get("tag_name") or "").strip()
        if tag:
            for candidate in release_zip_urls(tag):
                if candidate not in urls:
                    urls.append(candidate)
    for candidate in release_zip_urls():
        if candidate not in urls:
            urls.append(candidate)
    return urls


def download_actions_artifact_zip(url: str, dest: Path) -> str:
    """Download a GitHub Actions artifact archive (auth required)."""
    parsed = parse_actions_artifact_url(url)
    if parsed is None:
        raise RuntimeError(f"Not a GitHub Actions artifact URL: {url}")
    owner, repo, artifact_id = parsed
    token = github_auth_token()
    if not token:
        raise RuntimeError(
            "GitHub Actions artifacts require authentication. "
            "Set GITHUB_TOKEN or GH_TOKEN, or run `gh auth login`."
        )
    api_url = actions_artifact_zip_api_url(owner, repo, artifact_id)
    # Authorization must not follow the redirect to Azure blob storage.
    request = urllib.request.Request(
        api_url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": USER_AGENT,
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    request.add_unredirected_header("Authorization", f"Bearer {token}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            data = response.read()
    except urllib.error.HTTPError as exc:
        if exc.code in {401, 403}:
            raise RuntimeError(
                "GitHub refused the Actions artifact download "
                f"(HTTP {exc.code}). Check that GITHUB_TOKEN / GH_TOKEN / "
                "`gh auth login` can read artifacts for "
                f"{owner}/{repo}."
            ) from exc
        if exc.code == 404:
            raise RuntimeError(
                f"Actions artifact {artifact_id} was not found "
                f"(expired or wrong id) for {owner}/{repo}."
            ) from exc
        raise RuntimeError(
            f"Could not download Actions artifact {artifact_id} "
            f"(HTTP {exc.code})."
        ) from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(
            f"Could not download Actions artifact {artifact_id}: {exc.reason}."
        ) from exc
    if not data:
        raise RuntimeError(f"Download of Actions artifact {artifact_id} was empty.")
    dest.write_bytes(data)
    return api_url


def download_release_zip(
    dest: Path,
    *,
    version: str | None = None,
    url: str | None = None,
) -> str:
    """Download the plugin zip and return the URL used."""
    if url and is_actions_artifact_url(url):
        return download_actions_artifact_zip(url, dest)
    if url:
        candidates = [url]
    elif version:
        candidates = release_zip_urls(version)
    else:
        candidates = latest_zip_candidates()
    dest.parent.mkdir(parents=True, exist_ok=True)
    last_error: Exception | None = None
    for download_url in candidates:
        try:
            with urllib.request.urlopen(download_url, timeout=120) as response:
                data = response.read()
        except urllib.error.HTTPError as exc:
            last_error = RuntimeError(
                f"Could not download {download_url} (HTTP {exc.code})."
            )
            if exc.code == 404 and len(candidates) > 1:
                continue
            raise last_error from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(
                f"Could not download {download_url}: {exc.reason}."
            ) from exc
        dest.write_bytes(data)
        if not data:
            raise RuntimeError(f"Download from {download_url} was empty.")
        return download_url
    raise RuntimeError("Could not download a plugin zip.")


def install_release_zip(
    zip_path: Path,
    *,
    customize: str | None = None,
    remove_legacy: bool = True,
    apply_gui_names: bool = True,
) -> None:
    """Install a Fanfic Organizer plugin zip with ``calibre-customize -a``."""
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
            if zip_url:
                if is_actions_artifact_url(zip_url):
                    _log(f"Downloading Actions artifact {zip_url}")
                else:
                    _log(f"Downloading {zip_url}")
            elif version:
                _log(f"Downloading {release_zip_url(version)}")
            else:
                _log("Downloading latest GitHub release zip")
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
        if msg.startswith("Could not download") or "Actions artifact" in msg:
            code = "download_failed"
        elif "authentication" in msg.lower() or "refused the Actions" in msg:
            code = "download_failed"
        else:
            code = "calibre_install_failed"
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
        description=(
            "Install Fanfic Organizer from the latest GitHub release, "
            "a release tag, a local zip, or a PR Actions artifact URL."
        ),
    )
    parser.add_argument(
        "--version",
        metavar="TAG",
        help="Install a specific release tag (for example 0.26.1 or v0.26.1).",
    )
    parser.add_argument(
        "--url",
        metavar="URL",
        help=(
            "Download from this URL: a release asset, or a GitHub Actions "
            "artifact page "
            "(…/actions/runs/<run>/artifacts/<id>; needs GITHUB_TOKEN, "
            "GH_TOKEN, or `gh auth login`)."
        ),
    )
    parser.add_argument(
        "--zip",
        type=Path,
        metavar="PATH",
        help="Install an existing plugin zip instead of downloading.",
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
    if args.zip is not None and args.url:
        print("Use either --zip or --url, not both.", file=sys.stderr)
        return 2
    if args.version and args.url:
        print("Use either --version or --url, not both.", file=sys.stderr)
        return 2
    result = run_install(
        version=args.version,
        zip_url=args.url,
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
