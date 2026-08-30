"""GitHub release URLs for the Fanfic Organizer Calibre plugin zip."""

from __future__ import annotations

import re
from urllib.parse import quote, urlparse

GITHUB_REPO = "BlakeASmith/fanfic-organizer"
RELEASE_ZIP_NAME = "fanfic-organizer.zip"
VERSIONED_ZIP_PREFIX = "FanFicOrganizer"
RAW_INSTALL_SH = (
    f"https://raw.githubusercontent.com/{GITHUB_REPO}/main/scripts/install.sh"
)

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


def release_tag(version: str) -> str:
    text = version.strip()
    if not text:
        raise ValueError("version is required")
    return text if text.startswith("v") else f"v{text}"


def versioned_zip_name(version: str) -> str:
    text = version.strip()
    if not text:
        raise ValueError("version is required")
    if text.startswith("v") and text[1:2].isdigit():
        text = text[1:]
    return f"{VERSIONED_ZIP_PREFIX}-{text}.zip"


def github_asset_url(tag: str, filename: str) -> str:
    encoded = f"{quote(tag, safe='v./-')}/{quote(filename, safe='.-')}"
    return f"https://github.com/{GITHUB_REPO}/releases/download/{encoded}"


def release_zip_url(version: str | None = None) -> str:
    """Download URL for a release zip.

    A specific version uses ``FanFicOrganizer-<version>.zip``.
    Latest (no version) falls back to the historical ``fanfic-organizer.zip``
    alias used by older GitHub releases.
    """
    if version:
        tag = release_tag(version)
        return github_asset_url(tag, versioned_zip_name(version))
    return (
        f"https://github.com/{GITHUB_REPO}/releases/latest/download/"
        f"{RELEASE_ZIP_NAME}"
    )


def pick_zip_download_url(assets: list[dict], tag: str = "") -> str | None:
    """Prefer ``FanFicOrganizer-<version>.zip``, then any versioned zip, then the old alias."""
    items = [asset for asset in assets if isinstance(asset, dict)]
    version = tag[1:] if str(tag).startswith("v") else str(tag)
    wanted = versioned_zip_name(version) if version else ""

    def _url_for(name: str) -> str | None:
        for asset in items:
            if str(asset.get("name") or "") != name:
                continue
            url = str(asset.get("browser_download_url") or "").strip()
            if url:
                return url
        return None

    if wanted:
        found = _url_for(wanted)
        if found:
            return found
    for asset in items:
        name = str(asset.get("name") or "")
        if name.startswith(f"{VERSIONED_ZIP_PREFIX}-") and name.endswith(".zip"):
            url = str(asset.get("browser_download_url") or "").strip()
            if url:
                return url
    return _url_for(RELEASE_ZIP_NAME)


def release_zip_urls(version: str | None = None) -> list[str]:
    """Candidate download URLs: versioned name first, then the historical alias."""
    if not version:
        return [release_zip_url()]
    tag = release_tag(version)
    return [
        github_asset_url(tag, versioned_zip_name(version)),
        github_asset_url(tag, RELEASE_ZIP_NAME),
    ]


def parse_actions_artifact_url(url: str) -> tuple[str, str, int] | None:
    """Parse a GitHub Actions artifact page or API URL.

    Returns ``(owner, repo, artifact_id)`` or ``None`` when the URL is not an
    Actions artifact link.
    """
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


def actions_artifact_zip_api_url(owner: str, repo: str, artifact_id: int) -> str:
    """API URL that returns the artifact archive (requires GitHub auth)."""
    return (
        f"https://api.github.com/repos/{owner}/{repo}/actions/artifacts/"
        f"{int(artifact_id)}/zip"
    )


def is_actions_artifact_url(url: str) -> bool:
    return parse_actions_artifact_url(url) is not None


__all__ = [
    "GITHUB_REPO",
    "RAW_INSTALL_SH",
    "RELEASE_ZIP_NAME",
    "VERSIONED_ZIP_PREFIX",
    "actions_artifact_zip_api_url",
    "github_asset_url",
    "is_actions_artifact_url",
    "parse_actions_artifact_url",
    "pick_zip_download_url",
    "release_tag",
    "release_zip_url",
    "release_zip_urls",
    "versioned_zip_name",
]
