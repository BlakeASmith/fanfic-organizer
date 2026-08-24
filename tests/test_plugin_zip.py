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
    assert "ao3kit/__init__.py" in names
    assert "ao3kit/cli.py" in names
    assert "ao3kit/htmlsoup.py" in names
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
    assert "images/icon.png" in names
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


def test_build_zip_survives_pre_1980_mtime(tmp_path: Path):
    victim = makeplugin.PLUGIN_DIR / "ao3_plugin.py"
    original_mtime = victim.stat().st_mtime
    import os

    os.utime(victim, (0, 0))
    try:
        dest = tmp_path / "fanfic-organizer.zip"
        makeplugin.build_zip(dest, vendor=False)
        with zipfile.ZipFile(dest) as zf:
            info = zf.getinfo("ao3_plugin.py")
        assert info.date_time == makeplugin.ZIP_ENTRY_DATE_TIME
    finally:
        os.utime(victim, (original_mtime, original_mtime))


def test_plugin_version_matches_ao3kit():
    text = (
        Path(__file__).resolve().parents[1] / "calibre-plugin" / "__init__.py"
    ).read_text(encoding="utf-8")
    marker = "__version__ = ("
    start = text.index(marker) + len(marker)
    end = text.index(")", start)
    parts = tuple(int(p.strip()) for p in text[start:end].split(","))
    assert ".".join(str(p) for p in parts) == ao3kit_version


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
