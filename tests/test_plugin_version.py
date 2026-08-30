"""CalibreVersion display string used in Preferences → Plugins."""

from __future__ import annotations

import importlib.util
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[1] / "calibre-plugin"


def load_plugin_version():
    spec = importlib.util.spec_from_file_location(
        "fanfic_organizer_plugin_version", PLUGIN / "plugin_version.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_calibre_version_displays_preview_string_and_compares_as_tuple():
    mod = load_plugin_version()
    version = mod.CalibreVersion(
        (0, 31, 0), "0.31.0-preview.452+7a4f9b2"
    )
    assert ".".join(map(str, version)) == "0.31.0-preview.452+7a4f9b2"
    assert str(version) == "0.31.0-preview.452+7a4f9b2"
    assert version[0] == 0
    assert version[1] == 31
    assert version[2] == 0
    assert version[:3] == (0, 31, 0)
    assert version > (0, 30, 0)
    assert version < (0, 32, 0)
    assert mod.plugin_display_string(version) == "0.31.0-preview.452+7a4f9b2"
