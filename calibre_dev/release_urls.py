"""GitHub release URLs for the Fanfic Organizer Calibre plugin zip."""

from __future__ import annotations

GITHUB_REPO = "BlakeASmith/fanfic-organizer"
RELEASE_ZIP_NAME = "fanfic-organizer.zip"
RAW_INSTALL_SH = (
    f"https://raw.githubusercontent.com/{GITHUB_REPO}/main/scripts/install.sh"
)


def release_tag(version: str) -> str:
    text = version.strip()
    if not text:
        raise ValueError("version is required")
    return text if text.startswith("v") else f"v{text}"


def release_zip_url(version: str | None = None) -> str:
    """Return the GitHub download URL for ``fanfic-organizer.zip``."""
    if version:
        tag = release_tag(version)
        return (
            f"https://github.com/{GITHUB_REPO}/releases/download/"
            f"{tag}/{RELEASE_ZIP_NAME}"
        )
    return (
        f"https://github.com/{GITHUB_REPO}/releases/latest/download/"
        f"{RELEASE_ZIP_NAME}"
    )


__all__ = [
    "GITHUB_REPO",
    "RAW_INSTALL_SH",
    "RELEASE_ZIP_NAME",
    "release_tag",
    "release_zip_url",
]
