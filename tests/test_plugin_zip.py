"""Fat plugin zip layout (no pip vendor)."""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

import makeplugin


def test_iter_zip_entries_includes_ao3kit_and_launcher():
    entries = makeplugin.iter_zip_entries(vendor_dir=None)
    names = {arc for _path, arc in entries}
    assert "__init__.py" in names
    assert "ao3_plugin.py" in names
    assert "run_ao3kit.py" in names
    assert "plugin-import-name-wranglekit.txt" in names
    assert "images/icon.png" in names
    assert "ao3kit/__init__.py" in names
    assert "ao3kit/cli.py" in names
    assert "ao3kit/htmlsoup.py" in names
    assert not any(name.startswith("vendor/") for name in names)


def test_build_zip_no_vendor(tmp_path: Path):
    dest = tmp_path / "wranglekit.zip"
    makeplugin.build_zip(dest, vendor=False)
    with zipfile.ZipFile(dest) as zf:
        names = set(zf.namelist())
    assert "ao3kit/__init__.py" in names
    assert "run_ao3kit.py" in names
    assert "ao3_plugin.py" in names
    assert "images/icon.png" in names
    assert not any(name.endswith(".so") for name in names)


def test_makeplugin_zip_no_vendor(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    dest = tmp_path / "wranglekit.zip"
    monkeypatch.setattr(makeplugin, "OUTPUT", dest)
    assert makeplugin.main(["zip", "--no-vendor"]) == 0
    assert dest.is_file()
