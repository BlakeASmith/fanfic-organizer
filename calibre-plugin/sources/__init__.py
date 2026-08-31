# -*- coding: utf-8 -*-
"""Per-source Calibre adapters (identifiers, matching, import UI).

AO3-specific and Wikipedia-specific logic live in sibling modules. Shared
import / cleaned-field code calls the registry instead of branching on source
names.
"""

from __future__ import annotations

from typing import Any, Protocol

SOURCE_AO3 = 'ao3'


class SourceAdapter(Protocol):
    """Calibre-side behaviour for one content source."""

    id: str
    publisher: str
    menu_label: str
    include_series: bool
    job_kind: str

    def owns_record(self, record: dict[str, Any]) -> bool: ...

    def work_id(self, record: dict[str, Any]) -> str: ...

    def work_url(self, record: dict[str, Any]) -> str: ...

    def identifiers(
        self, record: dict[str, Any], *, work_id: str, url: str
    ) -> dict[str, str]: ...

    def book_matches(
        self,
        identifiers: dict[str, Any] | None,
        *,
        work_id: str = '',
        url: str = '',
    ) -> bool: ...

    def plan_job(self, options: dict[str, Any], job_dir: Any) -> dict[str, Any]: ...

    def run_import_dialog(self, gui: Any) -> dict[str, Any] | None: ...


def _load_adapters() -> list[Any]:
    try:
        from calibre_plugins.fanfic_organizer.sources.ao3 import Ao3Source
        from calibre_plugins.fanfic_organizer.sources.omnibus import OmnibusSource
        from calibre_plugins.fanfic_organizer.sources.web import WebSource
        from calibre_plugins.fanfic_organizer.sources.wikipedia import (
            WikipediaSource,
        )
    except ImportError:
        from sources.ao3 import Ao3Source
        from sources.omnibus import OmnibusSource
        from sources.web import WebSource
        from sources.wikipedia import WikipediaSource

    # Specific sources before AO3 (the default catch-all).
    return [OmnibusSource(), WikipediaSource(), WebSource(), Ao3Source()]


_ADAPTERS: list[Any] | None = None


def all_sources() -> list[Any]:
    global _ADAPTERS
    if _ADAPTERS is None:
        _ADAPTERS = _load_adapters()
    return list(_ADAPTERS)


def source_menu_labels(*, group: str = 'toolbar') -> tuple[str, ...]:
    wanted = str(group or 'toolbar').strip().casefold() or 'toolbar'
    labels: list[str] = []
    for adapter in all_sources():
        label = getattr(adapter, 'menu_label', '') or ''
        if not label:
            continue
        g = str(getattr(adapter, 'menu_group', 'toolbar') or 'toolbar').casefold()
        if g == wanted:
            labels.append(label)
    return tuple(labels)


def get_source(source_id: str) -> Any | None:
    wanted = str(source_id or '').strip().casefold()
    for adapter in all_sources():
        if adapter.id == wanted:
            return adapter
    return None


def adapter_for_record(record: dict[str, Any] | None) -> Any:
    adapters = all_sources()
    if isinstance(record, dict):
        for adapter in adapters:
            if adapter.owns_record(record):
                return adapter
    for adapter in adapters:
        if adapter.id == SOURCE_AO3:
            return adapter
    return adapters[0]


def record_source_id(record: dict[str, Any] | None) -> str:
    return adapter_for_record(record).id
