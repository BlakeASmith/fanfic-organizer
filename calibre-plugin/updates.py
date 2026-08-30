# -*- coding: utf-8 -*-
"""Check GitHub Releases for plugin updates and install the plugin zip."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from urllib.parse import urlparse
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from calibre_plugins.fanfic_organizer.runtime import (
    PLUGIN_NAME,
    plugin_version_string,
    zip_has_bundled_ao3kit,
)

GITHUB_OWNER = "BlakeASmith"
GITHUB_REPO = "fanfic-organizer"
_DEFAULT_GITHUB_API = (
    f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/releases"
)


def github_releases_api() -> str:
    override = (os.environ.get("AO3KIT_UPDATE_API") or "").strip()
    return override or _DEFAULT_GITHUB_API
ZIP_ASSET_NAME = "fanfic-organizer.zip"
VERSIONED_ZIP_PREFIX = "FanFicOrganizer-"
LEGACY_PLUGIN_NAMES = ("AO3 Scraper", "Wranglekit")
SEMVER_RE = re.compile(
    r"^v?(?P<major>\d+)\.(?P<minor>\d+)\.(?P<patch>\d+)"
    r"(?:-(?P<pre>[0-9A-Za-z.-]+))?"
    r"(?:\+(?P<build>[0-9A-Za-z.-]+))?$"
)
PREVIEW_TAG_RE = re.compile(r"(?:^|/)v?\d+\.\d+\.\d+-preview(?:\.|$|\+)")
PR_TAG_RE = re.compile(r"(?:^|/)v?\d+\.\d+\.\d+-pr\.\d+")
# https://github.com/OWNER/REPO/actions/runs/RUN/artifacts/ID
_ACTIONS_ARTIFACT_PAGE_RE = re.compile(
    r"^/(?P<owner>[^/]+)/(?P<repo>[^/]+)/actions/runs/\d+/artifacts/(?P<id>\d+)/?$",
    re.IGNORECASE,
)
# https://api.github.com/repos/OWNER/REPO/actions/artifacts/ID[/zip]
_ACTIONS_ARTIFACT_API_RE = re.compile(
    r"^/repos/(?P<owner>[^/]+)/(?P<repo>[^/]+)/actions/artifacts/(?P<id>\d+)(?:/zip)?/?$",
    re.IGNORECASE,
)
USER_AGENT = "Fanfic-Organizer-Plugin-Updater"
RESTART_DELAY_S = 2.0
SHUTDOWN_WAIT_S = 20.0
START_WAIT_S = 3.0


class UpdateError(RuntimeError):
    """Update check, download, or install failed."""


@dataclass(frozen=True)
class ParsedVersion:
    major: int
    minor: int
    patch: int
    prerelease: str | None = None
    build: str | None = None

    @property
    def base(self) -> tuple[int, int, int]:
        return self.major, self.minor, self.patch

    @property
    def is_preview(self) -> bool:
        return bool(self.prerelease and self.prerelease.startswith("preview"))

    @property
    def is_pr_build(self) -> bool:
        return bool(self.prerelease and self.prerelease.startswith("pr."))

    @property
    def is_stable(self) -> bool:
        return not self.prerelease and not self.build

    @property
    def pr_number(self) -> int | None:
        if not self.is_pr_build or not self.prerelease:
            return None
        # ``pr.44`` or ``pr.44.extra``
        part = self.prerelease.split(".", 1)[1] if "." in self.prerelease else ""
        head = part.split(".", 1)[0]
        return int(head) if head.isdigit() else None

    def text(self) -> str:
        value = f"{self.major}.{self.minor}.{self.patch}"
        if self.prerelease:
            value = f"{value}-{self.prerelease}"
        if self.build:
            value = f"{value}+{self.build}"
        return value


@dataclass(frozen=True)
class ReleaseInfo:
    version: tuple[int, int, int]
    version_display: str
    is_preview: bool
    tag: str
    name: str
    published_at: str
    body: str
    html_url: str
    download_url: str
    parsed: ParsedVersion
    asset_size: int | None = None

    @property
    def version_text(self) -> str:
        return self.version_display

    @property
    def is_pr_build(self) -> bool:
        return self.parsed.is_pr_build

    @property
    def pr_number(self) -> int | None:
        return self.parsed.pr_number


def _prerelease_sort_key(prerelease: str | None) -> tuple:
    if not prerelease:
        return (1,)
    parts = prerelease.split(".")
    key: list[tuple[int, int | str]] = []
    for part in parts:
        if part.isdigit():
            key.append((0, int(part)))
        else:
            key.append((1, part))
    return (0, tuple(key))


def version_sort_key(parsed: ParsedVersion) -> tuple:
    return (
        parsed.major,
        parsed.minor,
        parsed.patch,
        _prerelease_sort_key(parsed.prerelease),
    )


def compare_parsed_versions(left: ParsedVersion, right: ParsedVersion) -> int:
    left_key = version_sort_key(left)
    right_key = version_sort_key(right)
    if left_key > right_key:
        return 1
    if left_key < right_key:
        return -1
    return 0


def parse_semver(value: str) -> ParsedVersion:
    match = SEMVER_RE.fullmatch((value or "").strip())
    if not match:
        raise UpdateError(f"release tag is not SemVer: {value!r}")
    return ParsedVersion(
        major=int(match["major"]),
        minor=int(match["minor"]),
        patch=int(match["patch"]),
        prerelease=match["pre"],
        build=match["build"],
    )


def parse_version(value: str) -> tuple[int, int, int]:
    return parse_semver(value).base


def is_preview_tag(tag: str) -> bool:
    text = (tag or "").strip()
    if PREVIEW_TAG_RE.search(text):
        return True
    try:
        return parse_semver(text).is_preview
    except UpdateError:
        return False


def is_pr_tag(tag: str) -> bool:
    text = (tag or "").strip()
    if PR_TAG_RE.search(text):
        return True
    try:
        return parse_semver(text).is_pr_build
    except UpdateError:
        return False


def installed_version_parsed() -> ParsedVersion:
    try:
        return parse_semver(installed_version_text())
    except UpdateError:
        major, minor, patch = installed_version()
        return ParsedVersion(major, minor, patch)


def installed_version() -> tuple[int, int, int]:
    try:
        from calibre_plugins.fanfic_organizer import __version__ as version

        return (int(version[0]), int(version[1]), int(version[2]))
    except Exception:
        return (0, 0, 0)


def installed_version_text() -> str:
    try:
        from calibre_plugins.fanfic_organizer import __version_display__ as display

        if display:
            return str(display)
    except Exception:
        pass
    return plugin_version_string(installed_version())


def _github_request(
    url: str,
    *,
    timeout: float = 30.0,
    token: str | None = None,
) -> Any:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": USER_AGENT,
        "X-GitHub-Api-Version": "2022-11-28",
    }
    request = urllib.request.Request(url, headers=headers)
    auth = github_auth_token(explicit=token)
    if auth:
        request.add_unredirected_header("Authorization", f"Bearer {auth}")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = response.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace").strip()
        raise UpdateError(
            f"GitHub request failed ({exc.code}): {detail or exc.reason}"
        ) from exc
    except urllib.error.URLError as exc:
        raise UpdateError(f"Could not reach GitHub: {exc.reason}") from exc
    try:
        return json.loads(payload.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise UpdateError("GitHub returned invalid JSON") from exc


def _asset_name(asset: dict[str, Any]) -> str:
    return str(asset.get("name") or "")


def _pick_zip_asset(
    assets: Iterable[dict[str, Any]],
    *,
    tag: str = "",
) -> dict[str, Any] | None:
    items = [asset for asset in assets if isinstance(asset, dict)]
    version = tag[1:] if tag.startswith("v") else tag
    wanted = f"{VERSIONED_ZIP_PREFIX}{version}.zip" if version else ""
    for asset in items:
        if wanted and _asset_name(asset) == wanted:
            return asset
    for asset in items:
        if _asset_name(asset) == ZIP_ASSET_NAME:
            return asset
    for asset in items:
        name = _asset_name(asset)
        if name.startswith(VERSIONED_ZIP_PREFIX) and name.endswith(".zip"):
            return asset
    return None


def release_from_api(record: dict[str, Any]) -> ReleaseInfo | None:
    if record.get("draft"):
        return None
    tag = str(record.get("tag_name") or "").strip()
    if not tag:
        return None
    prerelease = bool(record.get("prerelease"))
    if prerelease and not (is_preview_tag(tag) or is_pr_tag(tag)):
        return None
    try:
        parsed = parse_semver(tag)
    except UpdateError:
        return None
    asset = _pick_zip_asset(record.get("assets") or [], tag=tag)
    if asset is None:
        return None
    download_url = str(asset.get("browser_download_url") or "").strip()
    if not download_url:
        return None
    size = asset.get("size")
    return ReleaseInfo(
        version=parsed.base,
        version_display=parsed.text(),
        is_preview=parsed.is_preview,
        tag=tag if tag.startswith("v") else f"v{tag.lstrip('v')}",
        name=str(record.get("name") or tag),
        published_at=str(record.get("published_at") or ""),
        body=str(record.get("body") or "").strip(),
        html_url=str(record.get("html_url") or "").strip(),
        download_url=download_url,
        parsed=parsed,
        asset_size=int(size) if isinstance(size, int) else None,
    )


def fetch_releases(*, per_page: int = 100) -> list[ReleaseInfo]:
    url = f"{github_releases_api()}?per_page={max(1, min(per_page, 100))}"
    payload = _github_request(url)
    if not isinstance(payload, list):
        raise UpdateError("Unexpected GitHub releases response")
    releases: list[ReleaseInfo] = []
    for record in payload:
        if not isinstance(record, dict):
            continue
        parsed = release_from_api(record)
        if parsed is not None:
            releases.append(parsed)
    releases.sort(key=lambda item: version_sort_key(item.parsed), reverse=True)
    return releases


def select_pr_builds(releases: Iterable[ReleaseInfo]) -> list[ReleaseInfo]:
    """Latest PR pre-release per pull-request number (newest first by PR #)."""
    newest: dict[int, ReleaseInfo] = {}
    for item in releases:
        if not item.is_pr_build:
            continue
        number = item.pr_number
        if number is None:
            continue
        existing = newest.get(number)
        if existing is None or compare_parsed_versions(item.parsed, existing.parsed) > 0:
            newest[number] = item
        elif (
            existing is not None
            and compare_parsed_versions(item.parsed, existing.parsed) == 0
            and (item.published_at or "") > (existing.published_at or "")
        ):
            newest[number] = item
    builds = list(newest.values())
    builds.sort(key=lambda item: item.pr_number or 0, reverse=True)
    return builds


def filter_releases(
    releases: Iterable[ReleaseInfo],
    *,
    include_prereleases: bool = False,
) -> list[ReleaseInfo]:
    """Return standard/preview releases for the update picker (never PR builds).

    When ``include_prereleases`` is false, preview GitHub pre-releases are omitted.
    PR pre-releases are always omitted here — use ``select_pr_builds``.
    """
    items = [item for item in releases if not item.is_pr_build]
    if include_prereleases:
        return items
    return [item for item in items if not item.is_preview]


def latest_release(releases: Iterable[ReleaseInfo]) -> ReleaseInfo | None:
    ordered = sorted(
        releases, key=lambda item: version_sort_key(item.parsed), reverse=True
    )
    return ordered[0] if ordered else None


def latest_stable_release(releases: Iterable[ReleaseInfo]) -> ReleaseInfo | None:
    stable = [
        item for item in releases if not item.is_preview and not item.is_pr_build
    ]
    return latest_release(stable)


_PRE_1_0_SECTION_RE = re.compile(
    r"(?ms)^###\s+Pre-1\.0\s*\n.*?(?=^##\s|\Z)"
)


def strip_release_notes_boilerplate(body: str) -> str:
    """Drop the repeated Pre-1.0 disclaimer so the useful changelog stays visible."""
    text = (body or "").strip()
    if not text:
        return ""
    cleaned = _PRE_1_0_SECTION_RE.sub("", text).strip()
    # Collapse leftover blank runs after stripping the disclaimer.
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned


def summarize_release_notes(body: str, *, limit: int = 8000) -> str:
    text = strip_release_notes_boilerplate(body)
    if not text:
        return "No release notes."
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def format_release_changelog_entry(release: ReleaseInfo) -> str:
    """One release's notes with a version heading for the updater pane."""
    published = format_published_at(release.published_at)
    heading = release.version_text
    if release.is_pr_build:
        number = release.pr_number
        heading = (
            f"PR #{number} · {release.version_text}"
            if number is not None
            else f"{release.version_text} (PR build)"
        )
    elif release.is_preview:
        heading += " (preview)"
    if published:
        heading += f" — {published}"
    notes = summarize_release_notes(release.body)
    if notes == "No release notes.":
        return f"{heading}\n\nNo release notes."
    return f"{heading}\n\n{notes}"


def releases_between(
    releases: Iterable[ReleaseInfo],
    *,
    older: ParsedVersion,
    newer: ParsedVersion,
) -> list[ReleaseInfo]:
    """Releases strictly after ``older`` and at or before ``newer``, newest first."""
    items = [
        item
        for item in releases
        if compare_parsed_versions(item.parsed, older) > 0
        and compare_parsed_versions(item.parsed, newer) <= 0
    ]
    items.sort(key=lambda item: version_sort_key(item.parsed), reverse=True)
    return items


def changelog_for_selection(
    releases: Iterable[ReleaseInfo],
    selected: ReleaseInfo,
    *,
    installed: ParsedVersion | None = None,
) -> str:
    """Changelog text for the updater when ``selected`` is chosen.

    When upgrading past the installed build, include every listed release
    between the two so the pane answers “what does this update do?”.
    Downgrades and same-version picks show only the selected release.
    PR builds always show their own notes only.
    """
    if selected.is_pr_build:
        return format_release_changelog_entry(selected)
    current = installed if installed is not None else installed_version_parsed()
    cmp = compare_parsed_versions(selected.parsed, current)
    if cmp > 0:
        between = releases_between(
            releases, older=current, newer=selected.parsed
        )
        if len(between) > 1:
            header = (
                f"What's new since {current.text()} "
                f"({len(between)} releases up to {selected.version_text}):"
            )
            parts = [format_release_changelog_entry(item) for item in between]
            return summarize_release_notes(
                header + "\n\n" + "\n\n———\n\n".join(parts),
                limit=16000,
            )
        if between:
            return format_release_changelog_entry(between[0])
    return format_release_changelog_entry(selected)


def parse_actions_artifact_url(url: str) -> tuple[str, str, int] | None:
    """Parse a GitHub Actions artifact page or API URL."""
    text = (url or "").strip()
    if not text:
        return None
    parsed = urlparse(text)
    host = (parsed.hostname or "").lower()
    path = parsed.path or ""
    if host in {"github.com", "www.github.com"}:
        match = _ACTIONS_ARTIFACT_PAGE_RE.match(path)
    elif host == "api.github.com":
        match = _ACTIONS_ARTIFACT_API_RE.match(path)
    else:
        return None
    if match is None:
        return None
    try:
        artifact_id = int(match.group("id"))
    except ValueError:
        return None
    return match.group("owner"), match.group("repo"), artifact_id


def is_actions_artifact_url(url: str) -> bool:
    return parse_actions_artifact_url(url) is not None


def actions_artifact_zip_api_url(owner: str, repo: str, artifact_id: int) -> str:
    return (
        f"https://api.github.com/repos/{owner}/{repo}/actions/artifacts/"
        f"{int(artifact_id)}/zip"
    )


def _find_gh_binary() -> str | None:
    found = shutil.which("gh")
    if found:
        return found
    for candidate in (
        Path.home() / ".local" / "bin" / "gh",
        Path("/opt/homebrew/bin/gh"),
        Path("/usr/local/bin/gh"),
    ):
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return None


def github_auth_token(*, explicit: str | None = None) -> str | None:
    """Token for Actions artifact downloads.

    Order: explicit argument, ``GITHUB_TOKEN``, ``GH_TOKEN``, then ``gh auth token``.
    """
    for value in (
        explicit,
        os.environ.get("GITHUB_TOKEN"),
        os.environ.get("GH_TOKEN"),
    ):
        text = (value or "").strip()
        if text:
            return text
    gh_bin = _find_gh_binary()
    if not gh_bin:
        return None
    try:
        completed = subprocess.run(
            [gh_bin, "auth", "token"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if completed.returncode != 0:
        return None
    token = (completed.stdout or "").strip()
    return token or None


def download_actions_artifact(
    url: str,
    dest: Path,
    *,
    token: str | None = None,
    timeout: float = 120.0,
) -> Path:
    """Download a GitHub Actions artifact archive (auth required)."""
    parsed = parse_actions_artifact_url(url)
    if parsed is None:
        raise UpdateError(
            "Not a GitHub Actions artifact URL. Paste a link like "
            "https://github.com/…/actions/runs/…/artifacts/…"
        )
    owner, repo, artifact_id = parsed
    auth = github_auth_token(explicit=token)
    if not auth:
        raise UpdateError(
            "GitHub Actions artifacts require authentication. "
            "Paste a personal access token below, set GITHUB_TOKEN / GH_TOKEN, "
            "or run `gh auth login`."
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
    request.add_unredirected_header("Authorization", f"Bearer {auth}")
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = response.read()
    except urllib.error.HTTPError as exc:
        if exc.code in {401, 403}:
            raise UpdateError(
                "GitHub refused the Actions artifact download "
                f"(HTTP {exc.code}). Check the token can read artifacts for "
                f"{owner}/{repo}."
            ) from exc
        if exc.code == 404:
            raise UpdateError(
                f"Actions artifact {artifact_id} was not found "
                f"(expired or wrong id) for {owner}/{repo}."
            ) from exc
        raise UpdateError(
            f"Download failed ({exc.code}): {exc.reason}"
        ) from exc
    except urllib.error.URLError as exc:
        raise UpdateError(f"Download failed: {exc.reason}") from exc
    if not data:
        raise UpdateError(f"Download of Actions artifact {artifact_id} was empty.")
    dest.write_bytes(data)
    if not zip_has_bundled_ao3kit(dest):
        dest.unlink(missing_ok=True)
        raise UpdateError(
            "Downloaded artifact is not a bundled Fanfic Organizer plugin"
        )
    return dest


def download_release(url: str, dest: Path, *, timeout: float = 120.0) -> Path:
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(
        url,
        headers={"User-Agent": USER_AGENT},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = response.read()
    except urllib.error.HTTPError as exc:
        raise UpdateError(f"Download failed ({exc.code}): {exc.reason}") from exc
    except urllib.error.URLError as exc:
        raise UpdateError(f"Download failed: {exc.reason}") from exc
    dest.write_bytes(data)
    if not zip_has_bundled_ao3kit(dest):
        dest.unlink(missing_ok=True)
        raise UpdateError(f"{ZIP_ASSET_NAME} is not a bundled Fanfic Organizer plugin")
    return dest


def download_plugin_url(
    url: str,
    dest: Path,
    *,
    token: str | None = None,
    timeout: float = 120.0,
) -> Path:
    """Download a release asset or Actions artifact into ``dest``."""
    if is_actions_artifact_url(url):
        return download_actions_artifact(
            url, dest, token=token, timeout=timeout
        )
    return download_release(url, dest, timeout=timeout)


def find_calibre_tool(name: str) -> str:
    found = shutil.which(name)
    if found:
        return found
    mac = Path("/Applications/calibre.app/Contents/MacOS") / name
    if mac.is_file():
        return str(mac)
    if os.name == "nt":
        for folder in (
            os.environ.get("PROGRAMFILES", r"C:\Program Files"),
            os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)"),
        ):
            candidate = Path(folder) / "Calibre2" / f"{name}.exe"
            if candidate.is_file():
                return str(candidate)
    raise UpdateError(f"{name} not found. Install Calibre or add it to PATH.")


def find_calibre() -> str:
    return find_calibre_tool("calibre")


def find_calibre_customize() -> str:
    return find_calibre_tool("calibre-customize")


def calibre_config_dir() -> Path:
    override = (os.environ.get("CALIBRE_CONFIG_DIRECTORY") or "").strip()
    if override:
        return Path(override)
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Preferences" / "calibre"
    if os.name == "nt":
        appdata = (os.environ.get("APPDATA") or "").strip()
        if appdata:
            return Path(appdata) / "calibre"
        return Path.home() / "AppData" / "Roaming" / "calibre"
    xdg = (os.environ.get("XDG_CONFIG_HOME") or "").strip()
    if xdg:
        return Path(xdg) / "calibre"
    return Path.home() / ".config" / "calibre"


def _rename_legacy_plugin_value(
    value: Any,
    *,
    legacy: tuple[str, ...],
    current: str,
) -> Any:
    if isinstance(value, str):
        return current if value in legacy else value
    if isinstance(value, list):
        out: list[Any] = []
        seen_current = False
        for item in value:
            replaced = _rename_legacy_plugin_value(
                item, legacy=legacy, current=current
            )
            if replaced == current:
                if seen_current:
                    continue
                seen_current = True
            out.append(replaced)
        return out
    if isinstance(value, dict):
        renamed: dict[Any, Any] = {}
        for key, item in value.items():
            new_key = key
            if isinstance(key, str):
                for old in legacy:
                    new_key = new_key.replace(old, current)
            renamed[new_key] = _rename_legacy_plugin_value(
                item, legacy=legacy, current=current
            )
        return renamed
    return value


def apply_fanfic_organizer_gui_names(
    config_dir: Path | None = None,
    *,
    name: str = PLUGIN_NAME,
    legacy_names: tuple[str, ...] = LEGACY_PLUGIN_NAMES,
) -> bool:
    path = (config_dir or calibre_config_dir()) / "gui.json"
    if not path.is_file():
        return False
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    updated = _rename_legacy_plugin_value(
        data, legacy=legacy_names, current=name
    )
    if updated == data:
        return False
    path.write_text(
        json.dumps(updated, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return True


def remove_legacy_plugins(customize: str) -> None:
    for name in LEGACY_PLUGIN_NAMES:
        subprocess.run(
            [customize, "-r", name],
            check=False,
            timeout=60,
            capture_output=True,
            text=True,
        )


def install_plugin_zip(zip_path: Path) -> None:
    zip_path = Path(zip_path)
    if not zip_path.is_file():
        raise UpdateError(f"Plugin zip not found: {zip_path}")
    if not zip_has_bundled_ao3kit(zip_path):
        raise UpdateError(f"{zip_path.name} is not a bundled Fanfic Organizer plugin")
    customize = find_calibre_customize()
    remove_legacy_plugins(customize)
    completed = subprocess.run(
        [customize, "-a", str(zip_path)],
        check=False,
        timeout=120,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()
        raise UpdateError(
            detail or f"calibre-customize exited {completed.returncode}"
        )
    apply_fanfic_organizer_gui_names()


def _restart_env_prefix() -> str:
    demo_api = (os.environ.get("AO3KIT_UPDATE_API") or "").strip()
    if not demo_api:
        return ""
    escaped = demo_api.replace('"', '\\"')
    return f'AO3KIT_UPDATE_API="{escaped}" '


def _restart_shell_command(calibre_bin: str) -> list[str]:
    env_prefix = _restart_env_prefix()
    if sys.platform == "darwin" and Path("/Applications/calibre.app").exists():
        script = (
            f'sleep {RESTART_DELAY_S}; '
            f'"{calibre_bin}" --shutdown-running-calibre; '
            f'sleep {START_WAIT_S}; '
            f'{env_prefix}open -a calibre'
        )
        return ["sh", "-c", script]
    if os.name == "nt":
        script = (
            f'timeout /t {int(RESTART_DELAY_S)} /nobreak >nul & '
            f'"{calibre_bin}" --shutdown-running-calibre & '
            f'timeout /t {int(START_WAIT_S)} /nobreak >nul & '
            f'{env_prefix}start "" "{calibre_bin}"'
        )
        return ["cmd.exe", "/c", script]
    script = (
        f'sleep {RESTART_DELAY_S}; '
        f'"{calibre_bin}" --shutdown-running-calibre; '
        f'sleep {START_WAIT_S}; '
        f'{env_prefix}"{calibre_bin}"'
    )
    return ["sh", "-c", script]


def spawn_calibre_restart() -> None:
    calibre_bin = find_calibre()
    kwargs: dict[str, Any] = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "close_fds": True,
    }
    if os.name == "nt":
        flags = subprocess.CREATE_NEW_PROCESS_GROUP
        flags |= getattr(subprocess, "DETACHED_PROCESS", 0)
        flags |= getattr(subprocess, "CREATE_NO_WINDOW", 0)
        kwargs["creationflags"] = flags
    else:
        kwargs["start_new_session"] = True
    subprocess.Popen(_restart_shell_command(calibre_bin), **kwargs)


def download_and_install(release: ReleaseInfo) -> Path:
    with tempfile.TemporaryDirectory(prefix="fanfic-organizer-update-") as tmp:
        dest = Path(tmp) / ZIP_ASSET_NAME
        download_release(release.download_url, dest)
        install_plugin_zip(dest)
        return dest


def download_and_install_from_url(
    url: str,
    *,
    token: str | None = None,
) -> Path:
    """Install from a release asset URL or Actions artifact page URL."""
    text = (url or "").strip()
    if not text:
        raise UpdateError("Paste a plugin zip or Actions artifact URL.")
    with tempfile.TemporaryDirectory(prefix="fanfic-organizer-update-") as tmp:
        dest = Path(tmp) / ZIP_ASSET_NAME
        download_plugin_url(text, dest, token=token)
        install_plugin_zip(dest)
        return dest


def download_and_install_selection(item: ReleaseInfo) -> Path:
    return download_and_install(item)


def compare_to_installed(release: ReleaseInfo) -> int:
    return compare_parsed_versions(release.parsed, installed_version_parsed())


def format_published_at(value: str) -> str:
    text = (value or "").strip()
    if not text:
        return ""
    if "T" in text:
        return text.split("T", 1)[0]
    return text[:10]
