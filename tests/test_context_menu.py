"""Context-menu layout helpers (Calibre-free)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[1] / "calibre-plugin"


def load_context_menu():
    spec = importlib.util.spec_from_file_location(
        "ao3_plugin_context_menu", PLUGIN / "context_menu.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_ensure_name_in_layout_appends_when_missing():
    cm = load_context_menu()
    layout, changed = cm.ensure_name_in_layout(["Edit Metadata", None, "View"], "Fanfic Organizer")
    assert changed is True
    assert layout == ["Edit Metadata", None, "View", "Fanfic Organizer"]


def test_ensure_name_in_layout_noop_when_present():
    cm = load_context_menu()
    src = ["Edit Metadata", "Fanfic Organizer", "View"]
    layout, changed = cm.ensure_name_in_layout(src, "Fanfic Organizer")
    assert changed is False
    assert layout == src


def test_ensure_name_in_layout_none_starts_fresh():
    cm = load_context_menu()
    layout, changed = cm.ensure_name_in_layout(None, "Fanfic Organizer")
    assert changed is True
    assert layout == ["Fanfic Organizer"]


def test_layouts_needing_plugin_only_missing_keys():
    cm = load_context_menu()
    updates = cm.layouts_needing_plugin(
        {
            "action-layout-context-menu": ["View", "Fanfic Organizer"],
            "action-layout-context-menu-split": ["View"],
            "action-layout-context-menu-cover-browser": None,
        },
        "Fanfic Organizer",
    )
    assert "action-layout-context-menu" not in updates
    assert updates["action-layout-context-menu-split"] == ["View", "Fanfic Organizer"]
    assert updates["action-layout-context-menu-cover-browser"] == ["Fanfic Organizer"]


def test_menu_action_labels_context_is_selection_only():
    cm = load_context_menu()
    labels = cm.menu_action_labels(for_context=True)
    assert labels == cm.SELECTION_ACTION_LABELS
    assert "Complete selected" in labels
    assert "Fill from AO3" in labels
    assert "Search similar..." in labels
    assert "Search AO3 and import..." not in labels
    assert "Plugin settings..." not in labels
    assert "Running jobs..." not in labels
    assert "Process library..." not in labels


def test_menu_action_labels_toolbar_includes_global():
    cm = load_context_menu()
    labels = cm.menu_action_labels(for_context=False)
    assert labels == cm.SELECTION_ACTION_LABELS + cm.GLOBAL_ACTION_LABELS
    assert "Search AO3 and import..." in labels
    assert "Tags and collections" in labels
    assert "Plugin settings..." in labels
