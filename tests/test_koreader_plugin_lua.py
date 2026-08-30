"""Sanity checks for the bundled KOReader collections plugin."""

from __future__ import annotations

from pathlib import Path

PLUGIN_DIR = (
    Path(__file__).resolve().parents[1]
    / "addons"
    / "koreader-collections"
    / "fanficcollections.koplugin"
)


def test_main_lua_defines_plugin_class_not_instance():
    text = (PLUGIN_DIR / "main.lua").read_text(encoding="utf-8")
    assert "WidgetContainer:extend" in text
    assert "WidgetContainer:new{" not in text
    assert "return FanficCollections" in text
    assert "SetupShowReader" in text
    assert "filemanagerutil.openFile" in text
    assert "DocumentRegistry:hasProvider" in text
    assert "self.ui.menu:registerToMainMenu(self)" in text
    assert 'require("errors")' in text
    assert "run_action" in text


def test_metadata_lua_resolves_paths_from_calibre_library_roots():
    text = (PLUGIN_DIR / "metadata.lua").read_text(encoding="utf-8")
    assert "metadata.calibre" in text
    assert "function Metadata.resolve_path(book)" in text
    assert "resolve_path_with_debug" in text
    assert "Metadata.library_roots()" in text
    assert "KOBO_STORAGE_ROOTS" in text
    assert "cache/calibre/libraries.lua" in text
    assert 'DataStorage:getDataDir() .. "/" .. lpath' not in text


def test_errors_lua_shows_debug_instead_of_propagating():
    text = (PLUGIN_DIR / "errors.lua").read_text(encoding="utf-8")
    assert "ConfirmBox" in text
    assert "logger.err" in text
    assert "function Errors.guard" in text
