"""SemVer helpers for plugin zips, preview tags, and Calibre version injection."""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import quote

from calibre_dev.changelog import (
    CHANGELOG_PATH,
    ChangelogError,
    PACKAGE_VERSION_RE,
    PLUGIN_VERSION_RE,
    PRE_1_0_DISCLAIMER,
    compact_section_body,
    format_version,
    has_changelog_items,
    is_pre_1_0,
    parse_changelog,
    parse_version,
    section_by_title,
)

STABLE_ZIP_NAME = "fanfic-organizer.zip"
VERSIONED_ZIP_PREFIX = "FanFicOrganizer"
SEMVER_RE = re.compile(
    r"^v?(?P<major>\d+)\.(?P<minor>\d+)\.(?P<patch>\d+)"
    r"(?:-(?P<pre>[0-9A-Za-z.-]+))?"
    r"(?:\+(?P<build>[0-9A-Za-z.-]+))?$"
)
PLUGIN_DISPLAY_RE = re.compile(
    r"^(__version_display__\s*=\s*)(?P<q>['\"])[^'\"]*(?P=q)\s*$",
    re.M,
)
PREVIEW_TAG_RE = re.compile(r"(?:^|/)v?\d+\.\d+\.\d+-preview(?:\.|$|\+)")
INCLUDED_NOTICE_MARK = "included in standard release"
PREVIEW_EMPTY_FALLBACK = "See commit history for details."


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
    def base_text(self) -> str:
        return format_version(self.base)

    @property
    def is_stable(self) -> bool:
        return not self.prerelease and not self.build

    @property
    def is_preview(self) -> bool:
        return bool(self.prerelease and self.prerelease.startswith("preview"))

    @property
    def is_pr_build(self) -> bool:
        return bool(self.prerelease and self.prerelease.startswith("pr."))

    def text(self) -> str:
        value = self.base_text
        if self.prerelease:
            value = f"{value}-{self.prerelease}"
        if self.build:
            value = f"{value}+{self.build}"
        return value


def parse_semver(value: str) -> ParsedVersion:
    match = SEMVER_RE.fullmatch((value or "").strip())
    if not match:
        raise ChangelogError(f"version must be SemVer, got {value!r}")
    return ParsedVersion(
        major=int(match["major"]),
        minor=int(match["minor"]),
        patch=int(match["patch"]),
        prerelease=match["pre"],
        build=match["build"],
    )


def short_sha(value: str, length: int = 7) -> str:
    text = (value or "").strip()
    if not text:
        raise ChangelogError("git hash is required")
    return text[:length]


def format_preview_version(
    base: tuple[int, int, int] | str,
    build_number: int | str,
    git_hash: str,
) -> str:
    """``X.Y.Z-preview.<build>+<sha>`` (SemVer pre-release + build metadata)."""
    parts = parse_version(base) if isinstance(base, str) else base
    number = int(build_number)
    if number < 1:
        raise ChangelogError(f"build number must be >= 1, got {build_number!r}")
    return f"{format_version(parts)}-preview.{number}+{short_sha(git_hash)}"


def format_pr_version(
    base: tuple[int, int, int] | str,
    pr_number: int | str,
    git_hash: str,
) -> str:
    parts = parse_version(base) if isinstance(base, str) else base
    number = int(pr_number)
    if number < 1:
        raise ChangelogError(f"PR number must be >= 1, got {pr_number!r}")
    return f"{format_version(parts)}-pr.{number}+{short_sha(git_hash)}"


def release_tag_name(version: str) -> str:
    parsed = parse_semver(version)
    text = parsed.text()
    return text if text.startswith("v") else f"v{text}"


def plugin_zip_name(version: str) -> str:
    return f"{VERSIONED_ZIP_PREFIX}-{parse_semver(version).text()}.zip"


def pr_zip_name(pr_number: int | str, git_hash: str) -> str:
    return f"{VERSIONED_ZIP_PREFIX}-PR-{int(pr_number)}-{short_sha(git_hash)}.zip"


def github_download_path(tag: str, filename: str) -> str:
    """URL path segments with ``+`` encoded so GitHub download links resolve."""
    return f"{quote(tag, safe='v./-')}/{quote(filename, safe='.-')}"


def is_preview_tag(tag: str) -> bool:
    text = (tag or "").strip()
    if PREVIEW_TAG_RE.search(text):
        return True
    try:
        return parse_semver(text).is_preview
    except ChangelogError:
        return False


def is_stable_tag(tag: str) -> bool:
    try:
        return parse_semver(tag).is_stable
    except ChangelogError:
        return False


def apply_version_to_plugin_init(text: str, version: str) -> str:
    parsed = parse_semver(version)
    display = parsed.text()
    updated, count = PLUGIN_VERSION_RE.subn(
        rf"\g<1>({parsed.major}, {parsed.minor}, {parsed.patch})",
        text,
        count=1,
    )
    if count != 1:
        raise ChangelogError("could not write plugin version tuple")
    replaced, display_count = PLUGIN_DISPLAY_RE.subn(
        rf'\g<1>"{display}"',
        updated,
        count=1,
    )
    if display_count == 1:
        return replaced
    return PLUGIN_VERSION_RE.sub(
        lambda match: f'{match.group(0)}\n__version_display__ = "{display}"',
        updated,
        count=1,
    )


def apply_version_to_package_init(text: str, version: str) -> str:
    display = parse_semver(version).text()
    updated, count = PACKAGE_VERSION_RE.subn(
        rf'\g<1>"{display}"',
        text,
        count=1,
    )
    if count != 1:
        raise ChangelogError("could not write package version")
    return updated


def rewrite_zip_entry(arcname: str, data: bytes, version: str | None) -> bytes:
    if not version:
        return data
    name = arcname.replace("\\", "/")
    if name == "__init__.py":
        return apply_version_to_plugin_init(data.decode("utf-8"), version).encode("utf-8")
    if name == "ao3kit/__init__.py":
        return apply_version_to_package_init(data.decode("utf-8"), version).encode("utf-8")
    return data


def unreleased_notes(
    text: str,
    *,
    fallback: str | None = None,
    include_disclaimer: bool = True,
) -> str:
    """Read-only ``## [Unreleased]`` body. Does not modify CHANGELOG.md."""
    try:
        _preamble, sections = parse_changelog(text)
        section = section_by_title(sections, "Unreleased")
    except ChangelogError:
        if fallback is None:
            raise
        body = fallback.strip() + "\n"
    else:
        compacted = compact_section_body(section.body)
        if not has_changelog_items(compacted):
            if fallback is None:
                raise ChangelogError("CHANGELOG.md [Unreleased] has no entries")
            body = fallback.strip() + "\n"
        else:
            body = compacted.strip() + "\n"
    if include_disclaimer and is_pre_1_0(None):
        body = body.rstrip() + "\n\n" + PRE_1_0_DISCLAIMER.strip() + "\n"
    return body


def preview_fallback_notes(git_hash: str) -> str:
    sha = short_sha(git_hash)
    return (
        f"Development preview build for commit {sha}.\n\n{PREVIEW_EMPTY_FALLBACK}"
    )


def preview_release_notes(
    text: str | None = None,
    *,
    git_hash: str,
    latest_release_url: str,
) -> str:
    changelog = CHANGELOG_PATH.read_text(encoding="utf-8") if text is None else text
    notes = unreleased_notes(
        changelog,
        fallback=preview_fallback_notes(git_hash),
        include_disclaimer=True,
    )
    banner = (
        "This is an automated **preview** build for testers and enthusiasts. "
        f"Prefer the [latest standard release]({latest_release_url}) for daily use."
    )
    return banner + "\n\n" + notes.strip() + "\n"


def included_in_release_notice(version: str, repo: str) -> str:
    tag = release_tag_name(version)
    url = f"https://github.com/{repo}/releases/tag/{quote(tag, safe='v./-')}"
    return (
        f"> ⚠️ **Notice:** Changes from this preview are included in "
        f"standard release [{tag}]({url})."
    )


def prepend_included_notice(body: str, notice: str) -> str:
    text = body or ""
    if INCLUDED_NOTICE_MARK in text:
        return text
    notice = notice.strip()
    if not text.strip():
        return notice + "\n"
    return notice + "\n\n" + text.lstrip()


__all__ = [
    "INCLUDED_NOTICE_MARK",
    "PREVIEW_EMPTY_FALLBACK",
    "ParsedVersion",
    "STABLE_ZIP_NAME",
    "VERSIONED_ZIP_PREFIX",
    "apply_version_to_package_init",
    "apply_version_to_plugin_init",
    "format_pr_version",
    "format_preview_version",
    "github_download_path",
    "included_in_release_notice",
    "is_preview_tag",
    "is_stable_tag",
    "plugin_zip_name",
    "pr_zip_name",
    "prepend_included_notice",
    "preview_fallback_notes",
    "preview_release_notes",
    "parse_semver",
    "release_tag_name",
    "rewrite_zip_entry",
    "short_sha",
    "unreleased_notes",
]
