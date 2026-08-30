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
