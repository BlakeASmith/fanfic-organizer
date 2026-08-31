"""Fat plugin zip layout (no pip vendor)."""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

import makeplugin
from ao3kit import __version__ as ao3kit_version


def test_iter_zip_entries_includes_ao3kit_and_launcher():
    entries = makeplugin.iter_zip_entries(vendor_dir=None)
    names = {arc for _path, arc in entries}
    assert "__init__.py" in names
    assert "ao3_plugin.py" in names
    assert "run_ao3kit.py" in names
    assert "plugin-import-name-fanfic_organizer.txt" in names
    assert "images/icon.png" in names
    assert "images/open-in-ao3.png" in names
    assert "sources/__init__.py" in names
    assert "sources/ao3.py" in names
    assert "sources/wikipedia/__init__.py" in names
    assert "sources/wikipedia/source.py" in names
    assert "sources/wikipedia/dialog.py" in names
    assert "ao3kit/__init__.py" in names
    assert "ao3kit/cli.py" in names
    assert "ao3kit/htmlsoup.py" in names
    assert "plugin_version.py" in names
    assert f"resources/koreader/fanficcollections.koplugin/main.lua" in names
    assert not any(name.startswith("vendor/") for name in names)
    assert "dev_project.json" not in names


def test_build_zip_no_vendor(tmp_path: Path):
    dest = tmp_path / "fanfic-organizer.zip"
    makeplugin.build_zip(dest, vendor=False)
    with zipfile.ZipFile(dest) as zf:
        names = set(zf.namelist())
    assert "ao3kit/__init__.py" in names
    assert "run_ao3kit.py" in names
    assert "ao3_plugin.py" in names
    assert "sources/__init__.py" in names
    assert "sources/wikipedia/dialog.py" in names
    assert "images/icon.png" in names
    assert "images/open-in-ao3.png" in names
    assert not any(name.endswith(".so") for name in names)


def test_makeplugin_zip_no_vendor(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    dest = tmp_path / "fanfic-organizer.zip"
    monkeypatch.setattr(makeplugin, "OUTPUT", dest)
    assert makeplugin.main(["zip", "--no-vendor"]) == 0
    assert dest.is_file()


def test_build_zip_excludes_dev_project_stamp(tmp_path: Path):
    stamp = makeplugin.PLUGIN_DIR / "dev_project.json"
    previous = stamp.read_text(encoding="utf-8") if stamp.is_file() else None
    stamp.write_text('{"project": "/not/this/machine"}\n', encoding="utf-8")
    try:
        dest = tmp_path / "fanfic-organizer.zip"
        makeplugin.build_zip(dest, vendor=False)
        with zipfile.ZipFile(dest) as zf:
            assert "dev_project.json" not in zf.namelist()
    finally:
        if previous is None:
            stamp.unlink(missing_ok=True)
        else:
            stamp.write_text(previous, encoding="utf-8")


def test_build_zip_injects_version_without_touching_source(tmp_path: Path):
    plugin_init = Path(__file__).resolve().parents[1] / "calibre-plugin" / "__init__.py"
    package_init = Path(__file__).resolve().parents[1] / "ao3kit" / "__init__.py"
    before_plugin = plugin_init.read_text(encoding="utf-8")
    before_package = package_init.read_text(encoding="utf-8")
    version = "0.31.0-preview.452+7a4f9b2"
    dest = tmp_path / f"FanFicOrganizer-{version}.zip"
    makeplugin.build_zip(dest, vendor=False, version=version)
    assert plugin_init.read_text(encoding="utf-8") == before_plugin
    assert package_init.read_text(encoding="utf-8") == before_package
    with zipfile.ZipFile(dest) as zf:
        plugin_text = zf.read("__init__.py").decode("utf-8")
        package_text = zf.read("ao3kit/__init__.py").decode("utf-8")
    assert '__version_display__ = "0.31.0-preview.452+7a4f9b2"' in plugin_text
    assert "__version__ = (0, 31, 0)" in plugin_text
    assert '__version__ = "0.31.0-preview.452+7a4f9b2"' in package_text


def test_makeplugin_zip_set_version_output(tmp_path: Path):
    dest = tmp_path / "custom.zip"
    assert (
        makeplugin.main(
            [
                "zip",
                "--no-vendor",
                "--set-version",
                "0.31.0-pr.12+abcdef1",
                "--output",
                str(dest),
            ]
        )
        == 0
    )
    assert dest.is_file()
    with zipfile.ZipFile(dest) as zf:
        plugin_text = zf.read("__init__.py").decode("utf-8")
    assert '__version_display__ = "0.31.0-pr.12+abcdef1"' in plugin_text


def test_plugin_version_matches_ao3kit():
    text = (
        Path(__file__).resolve().parents[1] / "calibre-plugin" / "__init__.py"
    ).read_text(encoding="utf-8")
    marker = "__version__ = ("
    start = text.index(marker) + len(marker)
    end = text.index(")", start)
    parts = tuple(int(p.strip()) for p in text[start:end].split(","))
    assert ".".join(str(p) for p in parts) == ao3kit_version
    assert f'__version_display__ = "{ao3kit_version}"' in text


def test_vendor_requirement_lines_skip_native_packages():
    lines = makeplugin.vendor_requirement_lines(
        "requests>=2.31\nlxml>=5.0\npillow>=10.0\nbeautifulsoup4>=4.12\n"
    )
    assert lines == ["requests>=2.31", "beautifulsoup4>=4.12"]


def test_vendor_requirement_lines_match_requirements_txt():
    names = {makeplugin._requirement_name(line) for line in makeplugin.vendor_requirement_lines()}
    assert "requests" in names
    assert "pyyaml" in names
    assert "lxml" not in names
    assert "pillow" not in names
