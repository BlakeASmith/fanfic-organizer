"""GitHub release URLs for the Fanfic Organizer Calibre plugin zip."""

from __future__ import annotations

from urllib.parse import quote

GITHUB_REPO = "BlakeASmith/fanfic-organizer"
RELEASE_ZIP_NAME = "fanfic-organizer.zip"
VERSIONED_ZIP_PREFIX = "FanFicOrganizer"
RAW_INSTALL_SH = (
    f"https://raw.githubusercontent.com/{GITHUB_REPO}/main/scripts/install.sh"
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


__all__ = [
    "GITHUB_REPO",
    "RAW_INSTALL_SH",
    "RELEASE_ZIP_NAME",
    "VERSIONED_ZIP_PREFIX",
    "github_asset_url",
    "pick_zip_download_url",
    "release_tag",
    "release_zip_url",
    "release_zip_urls",
    "versioned_zip_name",
]
