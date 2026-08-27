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

    Latest (no version) uses the stable ``fanfic-organizer.zip`` alias.
    A specific version prefers ``FanFicOrganizer-<version>.zip``.
    """
    if version:
        tag = release_tag(version)
        return github_asset_url(tag, versioned_zip_name(version))
    return (
        f"https://github.com/{GITHUB_REPO}/releases/latest/download/"
        f"{RELEASE_ZIP_NAME}"
    )


def release_zip_urls(version: str | None = None) -> list[str]:
    """Candidate download URLs, newest naming first, then the stable alias."""
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
    "release_tag",
    "release_zip_url",
    "release_zip_urls",
    "versioned_zip_name",
]
