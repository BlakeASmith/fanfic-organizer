"""Keep a Changelog cut + version bump for wranglekit releases."""

from __future__ import annotations

from pathlib import Path

import pytest

from calibre_dev.changelog import (
    ChangelogError,
    PRE_1_0_DISCLAIMER,
    cut_unreleased,
    format_version,
    is_pre_1_0,
    notes_for_version,
    parse_version,
    prepare_release,
    read_plugin_version,
    set_package_version,
    set_plugin_version,
)

SAMPLE = """# Changelog

Intro.

## [Unreleased]

### Features

- New search filter.

### Bug Fixes

### Documentation

- Mention the zip layout.

## [0.26.0] - 2026-08-23

### Features

- First public zip.
"""


def test_parse_version_strips_v_prefix():
    assert parse_version("v1.2.3") == (1, 2, 3)
    assert format_version((1, 2, 3)) == "1.2.3"
    with pytest.raises(ChangelogError):
        parse_version("1.2")


def test_notes_for_unreleased_drops_empty_groups():
    notes = notes_for_version(SAMPLE)
    assert "New search filter." in notes
    assert "Mention the zip layout." in notes
    assert "### Bug Fixes" not in notes
    assert "First public zip." not in notes
    assert "0.x releases are not rigorously tested" in notes


def test_pre_1_0_disclaimer_omitted_after_1_0():
    text = SAMPLE + "\n## [1.0.0] - 2028-01-01\n\n### Features\n\n- Stable line.\n"
    notes = notes_for_version(text, "1.0.0")
    assert "Stable line." in notes
    assert "not rigorously tested" not in notes
    assert is_pre_1_0("0.26.0")
    assert is_pre_1_0(None)
    assert not is_pre_1_0("1.0.0")
    assert "Prefer a release marked **Stable**" in PRE_1_0_DISCLAIMER


def test_notes_for_released_version():
    notes = notes_for_version(SAMPLE, "0.26.0")
    assert "First public zip." in notes
    assert "New search filter." not in notes
    assert "0.x releases are not rigorously tested" in notes


def test_notes_missing_version():
    with pytest.raises(ChangelogError, match="0.99.0"):
        notes_for_version(SAMPLE, "0.99.0")


def test_cut_unreleased_moves_bullets():
    updated = cut_unreleased(SAMPLE, "0.27.0", "2026-08-24")
    assert notes_for_version(updated, "0.27.0").startswith("### Features")
    with pytest.raises(ChangelogError, match="Unreleased"):
        notes_for_version(updated)
    assert "First public zip." in notes_for_version(updated, "0.26.0")


def test_cut_unreleased_rejects_duplicate_and_empty():
    filled = cut_unreleased(SAMPLE, "0.27.0", "2026-08-24")
    with pytest.raises(ChangelogError, match="already has"):
        cut_unreleased(filled, "0.27.0", "2026-08-25")
    empty = "# Changelog\n\n## [Unreleased]\n\n### Features\n"
    with pytest.raises(ChangelogError, match="no entries"):
        cut_unreleased(empty, "0.27.0", "2026-08-24")


def test_prepare_release_writes_files(tmp_path: Path):
    changelog = tmp_path / "CHANGELOG.md"
    changelog.write_text(SAMPLE, encoding="utf-8")
    plugin = tmp_path / "plugin.py"
    plugin.write_text("__version__ = (0, 26, 0)\n", encoding="utf-8")
    package = tmp_path / "pkg.py"
    package.write_text('__version__ = "0.26.0"\n', encoding="utf-8")

    notes = prepare_release(
        "0.27.0",
        release_date="2026-08-24",
        changelog_path=changelog,
        plugin_init=plugin,
        package_init=package,
    )
    assert "New search filter." in notes
    assert read_plugin_version(plugin) == (0, 27, 0)
    assert '__version__ = "0.27.0"' in package.read_text(encoding="utf-8")
    assert "## [0.27.0] - 2026-08-24" in changelog.read_text(encoding="utf-8")
    with pytest.raises(ChangelogError, match="Unreleased"):
        notes_for_version(changelog.read_text(encoding="utf-8"))


def test_prepare_release_rejects_non_increasing_version(tmp_path: Path):
    plugin = tmp_path / "plugin.py"
    plugin.write_text("__version__ = (0, 26, 0)\n", encoding="utf-8")
    package = tmp_path / "pkg.py"
    package.write_text('__version__ = "0.26.0"\n', encoding="utf-8")
    changelog = tmp_path / "CHANGELOG.md"
    changelog.write_text(SAMPLE, encoding="utf-8")
    with pytest.raises(ChangelogError, match="greater"):
        prepare_release(
            "0.26.0",
            changelog_path=changelog,
            plugin_init=plugin,
            package_init=package,
            write=False,
        )


def test_set_versions_round_trip(tmp_path: Path):
    plugin = tmp_path / "plugin.py"
    plugin.write_text("# head\n__version__ = (0, 1, 0)\n", encoding="utf-8")
    package = tmp_path / "pkg.py"
    package.write_text('"""pkg"""\n__version__ = "0.1.0"\n', encoding="utf-8")
    set_plugin_version(plugin, (1, 0, 0))
    set_package_version(package, (1, 0, 0))
    assert read_plugin_version(plugin) == (1, 0, 0)
    assert '__version__ = "1.0.0"' in package.read_text(encoding="utf-8")


def test_repo_changelog_has_unreleased_and_first_release():
    from calibre_dev.changelog import CHANGELOG_PATH

    text = CHANGELOG_PATH.read_text(encoding="utf-8")
    assert "Keep a Changelog" in notes_for_version(text)
    assert "wranglekit.zip" in notes_for_version(text, "0.26.0")


def test_makeplugin_changelog_stdout(capsys):
    import makeplugin

    assert makeplugin.main(["changelog"]) == 0
    out = capsys.readouterr().out
    assert "### Documentation" in out
    assert makeplugin.main(["changelog", "0.26.0"]) == 0
    shipped = capsys.readouterr().out
    assert "First GitHub Release" in shipped
    assert "0.x releases are not rigorously tested" in shipped


def test_makeplugin_release_dry_run_does_not_write(capsys):
    import makeplugin
    from calibre_dev.changelog import CHANGELOG_PATH, PLUGIN_INIT

    before_log = CHANGELOG_PATH.read_text(encoding="utf-8")
    before_plugin = PLUGIN_INIT.read_text(encoding="utf-8")
    assert makeplugin.main(["release", "0.27.0", "--dry-run", "--date", "2026-08-24"]) == 0
    out = capsys.readouterr()
    assert "Keep a Changelog" in out.out
    assert "dry-run" in out.err
    assert CHANGELOG_PATH.read_text(encoding="utf-8") == before_log
    assert PLUGIN_INIT.read_text(encoding="utf-8") == before_plugin
