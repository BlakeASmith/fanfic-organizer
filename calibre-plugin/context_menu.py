# -*- coding: utf-8 -*-
"""Calibre library context-menu layout helpers (no Calibre imports)."""

from __future__ import annotations

from typing import Iterable, Sequence

# Layouts that show book-oriented actions (library / split / cover browser).
CONTEXT_MENU_LAYOUT_KEYS = (
    'action-layout-context-menu',
    'action-layout-context-menu-split',
    'action-layout-context-menu-cover-browser',
)

# Top-level labels for actions that need a book selection.
SELECTION_ACTION_LABELS = (
    'Complete selected',
    'Fill from AO3',
    'Download EPUB',
    'Generate covers',
    'Import rest of series',
    'Fill series',
    'Simplify tags, fandoms & relationships',
    'Edit collections...',
    'Recompute collections',
    'Add to a collection...',
    'Search similar...',
)

# Top-level labels / submenus that are library-wide (toolbar only).
GLOBAL_ACTION_LABELS = (
    'Search AO3 and import...',
    'Process library...',
    'Running jobs...',
    'Tags and collections',
    'Import',
    'Check for updates...',
    'Deploy to KOReader…',
    'Plugin settings...',
)


def ensure_name_in_layout(
    layout: Sequence[str | None] | None,
    name: str,
) -> tuple[list[str | None], bool]:
    """Append *name* if missing. Returns (new_layout, changed)."""
    items: list[str | None] = list(layout) if layout is not None else []
    if name in items:
        return items, False
    items.append(name)
    return items, True


def layouts_needing_plugin(
    layouts: dict[str, Sequence[str | None] | None],
    name: str,
    *,
    keys: Iterable[str] = CONTEXT_MENU_LAYOUT_KEYS,
) -> dict[str, list[str | None]]:
    """Return only the layouts that should be rewritten to include *name*."""
    updates: dict[str, list[str | None]] = {}
    for key in keys:
        new_layout, changed = ensure_name_in_layout(layouts.get(key), name)
        if changed:
            updates[key] = new_layout
    return updates


def menu_action_labels(*, for_context: bool) -> tuple[str, ...]:
    """Labels shown on the plugin menu for toolbar vs library right-click."""
    if for_context:
        return SELECTION_ACTION_LABELS
    return SELECTION_ACTION_LABELS + GLOBAL_ACTION_LABELS
