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

# Opens in the browser; also injected as a top-level Calibre context-menu item.
OPEN_IN_AO3_LABEL = 'Open in AO3'

# Top-level labels for actions that need a book selection.
SELECTION_ACTION_LABELS = (
    OPEN_IN_AO3_LABEL,
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
# Toolbar source labels come from ``sources.source_menu_labels(group='toolbar')``.
_FIXED_GLOBAL_ACTION_LABELS = (
    'Process library...',
    'Running jobs...',
    'Tags and collections',
    'Import',
    'Check for updates...',
    'Deploy to KOReader…',
    'Plugin settings...',
)


def _global_action_labels() -> tuple[str, ...]:
    import sys
    from pathlib import Path

    root = str(Path(__file__).resolve().parent)
    if root not in sys.path:
        sys.path.insert(0, root)
    try:
        from calibre_plugins.fanfic_organizer.sources import source_menu_labels
    except ImportError:
        try:
            from sources import source_menu_labels
        except ImportError:
            return ('Search AO3 and import...',) + _FIXED_GLOBAL_ACTION_LABELS
    return source_menu_labels(group='toolbar') + _FIXED_GLOBAL_ACTION_LABELS


GLOBAL_ACTION_LABELS = _global_action_labels()


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
    """Labels shown on the plugin menu for toolbar vs library right-click.

    Library right-click injects Open in AO3 at the Calibre context-menu root,
    so the Fanfic Organizer submenu omits it there to avoid a duplicate.
    """
    if for_context:
        return tuple(
            label
            for label in SELECTION_ACTION_LABELS
            if label != OPEN_IN_AO3_LABEL
        )
    return SELECTION_ACTION_LABELS + GLOBAL_ACTION_LABELS


def insert_before_plugin_action(
    action_texts: Sequence[str | None],
    plugin_name: str,
    open_label: str = OPEN_IN_AO3_LABEL,
) -> list[str | None]:
    """Return menu labels with *open_label* placed just before *plugin_name*.

    Used to decide where the top-level Open in AO3 action sits relative to the
    Fanfic Organizer submenu on the Calibre library context menu.
    """
    items: list[str | None] = [text for text in action_texts if text != open_label]
    try:
        idx = items.index(plugin_name)
    except ValueError:
        items.append(open_label)
        return items
    items.insert(idx, open_label)
    return items
