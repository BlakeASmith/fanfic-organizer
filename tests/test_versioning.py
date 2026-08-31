"""Preview / PR SemVer, zip names, and read-only Unreleased notes."""

from __future__ import annotations

from pathlib import Path

import pytest

from calibre_dev.changelog import CHANGELOG_PATH, ChangelogError, next_0x_version
from calibre_dev.versioning import (
    PREVIEW_EMPTY_FALLBACK,
    apply_version_to_package_init,
    apply_version_to_plugin_init,
    format_pr_version,
    format_preview_version,
    github_download_path,
    included_in_release_notice,
    is_pr_tag,
    is_preview_tag,
    is_stable_tag,
    parse_semver,
    plugin_zip_name,
    pr_release_notes,
    pr_zip_name,
    prepend_included_notice,
    preview_release_notes,
    release_tag_name,
    rewrite_zip_entry,
    unreleased_notes,
)

SAMPLE = """# Changelog

Intro.

## [Unreleased]

### Features

- New search filter.

## [0.26.0] - 2026-08-23

### Features

- First public zip.
"""


def test_format_preview_version_matches_semver_template():
    assert (
        format_preview_version((0, 31, 0), 452, "7a4f9b2deadbeef")
        == "0.31.0-preview.452+7a4f9b2"
    )
    parsed = parse_semver("0.31.0-preview.452+7a4f9b2")
    assert parsed.base == (0, 31, 0)
    assert parsed.prerelease == "preview.452"
    assert parsed.build == "7a4f9b2"
    assert parsed.is_preview
    assert not parsed.is_stable


def test_format_preview_version_rejects_bad_build_number():
    with pytest.raises(ChangelogError):
        format_preview_version((0, 31, 0), 0, "abc1234")


def test_pr_and_zip_names():
    assert format_pr_version("0.31.0", 12, "abcdef1") == "0.31.0-pr.12+abcdef1"
    parsed = parse_semver("0.31.0-pr.12+abcdef1")
    assert parsed.is_pr_build
    assert not parsed.is_preview
    assert plugin_zip_name("0.31.0-preview.452+7a4f9b2") == (
        "FanFicOrganizer-0.31.0-preview.452+7a4f9b2.zip"
    )
    assert plugin_zip_name("0.31.0-pr.12+abcdef1") == (
        "FanFicOrganizer-0.31.0-pr.12+abcdef1.zip"
    )
    assert plugin_zip_name("0.31.0") == "FanFicOrganizer-0.31.0.zip"
    assert pr_zip_name(12, "abcdef1xyz") == "FanFicOrganizer-PR-12-abcdef1.zip"
    assert release_tag_name("0.31.0-preview.1+abc1234") == "v0.31.0-preview.1+abc1234"
    assert release_tag_name("0.31.0-pr.12+abcdef1") == "v0.31.0-pr.12+abcdef1"


def test_preview_and_stable_tag_detection():
    assert is_preview_tag("v0.31.0-preview.12+7a4f9b2")
    assert is_preview_tag("0.31.0-preview.1")
    assert not is_preview_tag("v0.31.0")
    assert is_pr_tag("v0.31.0-pr.12+abcdef1")
    assert is_pr_tag("0.31.0-pr.1")
    assert not is_pr_tag("v0.31.0-preview.1+abc")
    assert not is_pr_tag("v0.31.0")
    assert is_stable_tag("v0.31.0")
    assert not is_stable_tag("v0.31.0-preview.1+abc")


def test_github_download_path_encodes_plus():
    path = github_download_path(
        "v0.31.0-preview.452+7a4f9b2",
        "FanFicOrganizer-0.31.0-preview.452+7a4f9b2.zip",
    )
    assert "+" not in path
    assert "%2B" in path
    pr_path = github_download_path(
        "v0.32.0-pr.46+94ce2ba",
        "FanFicOrganizer-0.32.0-pr.46+94ce2ba.zip",
    )
    assert "+" not in pr_path
    assert "%2B" in pr_path
    assert "pr.46" in pr_path


def test_inject_preview_version_into_plugin_and_package():
    plugin = "__version__ = (0, 30, 0)\n__version_display__ = \"0.30.0\"\n"
    package = '__version__ = "0.30.0"\n'
    version = "0.31.0-preview.2+abc1234"
    updated_plugin = apply_version_to_plugin_init(plugin, version)
    updated_package = apply_version_to_package_init(package, version)
    assert "__version__ = (0, 31, 0)" in updated_plugin
    assert '__version_display__ = "0.31.0-preview.2+abc1234"' in updated_plugin
    assert '__version__ = "0.31.0-preview.2+abc1234"' in updated_package
    rewritten = rewrite_zip_entry("__init__.py", plugin.encode(), version).decode()
    assert rewritten == updated_plugin
    assert rewrite_zip_entry("ao3_plugin.py", b"keep", version) == b"keep"


def test_inject_adds_display_line_when_missing():
    plugin = "__version__ = (0, 26, 0)\n"
    updated = apply_version_to_plugin_init(plugin, "0.27.0")
    assert "__version__ = (0, 27, 0)" in updated
    assert '__version_display__ = "0.27.0"' in updated


def test_unreleased_notes_readonly_fallback(tmp_path: Path):
    empty = "# Changelog\n\n## [Unreleased]\n\n### Features\n"
    notes = unreleased_notes(empty, fallback="See commit history for details")
    assert "See commit history for details" in notes
    assert "0.x releases are not rigorously tested" in notes
    filled = unreleased_notes(SAMPLE)
    assert "New search filter." in filled
    assert "First public zip." not in filled


def test_preview_release_notes_scrape_unreleased_without_writing():
    before = CHANGELOG_PATH.read_text(encoding="utf-8")
    notes = preview_release_notes(
        SAMPLE,
        git_hash="7a4f9b2",
        latest_release_url="https://github.com/BlakeASmith/fanfic-organizer/releases/latest",
    )
    assert "automated **preview**" in notes
    assert "New search filter." in notes
    assert CHANGELOG_PATH.read_text(encoding="utf-8") == before
    empty = preview_release_notes(
        "# Changelog\n\n## [Unreleased]\n",
        git_hash="7a4f9b2dead",
        latest_release_url="https://example.com/latest",
    )
    assert "7a4f9b2" in empty
    assert PREVIEW_EMPTY_FALLBACK in empty


def test_pr_release_notes_scrape_unreleased_without_writing():
    before = CHANGELOG_PATH.read_text(encoding="utf-8")
    notes = pr_release_notes(
        SAMPLE,
        pr_number=12,
        git_hash="abcdef1",
        pr_url="https://github.com/BlakeASmith/fanfic-organizer/pull/12",
        latest_release_url="https://github.com/BlakeASmith/fanfic-organizer/releases/latest",
    )
    assert "automated **PR**" in notes
    assert "#12" in notes
    assert "New search filter." in notes
    assert CHANGELOG_PATH.read_text(encoding="utf-8") == before


def test_included_notice_is_idempotent():
    notice = included_in_release_notice("0.31.0", "BlakeASmith/fanfic-organizer")
    assert "v0.31.0" in notice
    once = prepend_included_notice("### Features\n- Hi\n", notice)
    assert once.startswith("> ⚠️")
    twice = prepend_included_notice(once, notice)
    assert twice == once


def test_next_0x_major_is_blocked_at_1_0():
    with pytest.raises(ChangelogError, match="1.0"):
        next_0x_version((0, 30, 0), bump="major")
    assert next_0x_version((0, 30, 0), bump="minor") == (0, 31, 0)
    assert next_0x_version((0, 30, 0), bump="patch") == (0, 30, 1)
