# -*- coding: utf-8 -*-
"""AO3 source adapter for Calibre import / matching."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

AO3_WORK_ID_RE = re.compile(
    r'(?:https?://)?(?:www\.)?archiveofourown\.org/works/(\d+)',
    re.IGNORECASE,
)

SOURCE_ID = 'ao3'
PUBLISHER = 'Archive of Our Own'
MENU_LABEL = 'Search AO3 and import...'


def work_id_from_url(url: Any) -> str | None:
    if not url:
        return None
    match = AO3_WORK_ID_RE.search(str(url))
    return match.group(1) if match else None


def work_url_for_id(work_id: Any) -> str | None:
    text = str(work_id or '').strip()
    if not text:
        return None
    return f'https://archiveofourown.org/works/{text}'


class Ao3Source:
    id = SOURCE_ID
    publisher = PUBLISHER
    menu_label = MENU_LABEL
    include_series = True
    job_kind = 'scrape'

    def owns_record(self, record: dict[str, Any]) -> bool:
        text = str(record.get('source') or '').strip().casefold()
        if text and text != SOURCE_ID:
            return False
        # Default catch-all; more specific sources are registered first.
        return True

    def work_id(self, record: dict[str, Any]) -> str:
        work_id = str(record.get('work_id') or '').strip()
        if work_id:
            return work_id
        return work_id_from_url(record.get('url')) or ''

    def work_url(self, record: dict[str, Any]) -> str:
        url = str(record.get('url') or '').strip()
        if url:
            return url
        return work_url_for_id(self.work_id(record)) or ''

    def identifiers(
        self, record: dict[str, Any], *, work_id: str, url: str
    ) -> dict[str, str]:
        ids: dict[str, str] = {}
        if url:
            ids['url'] = url
        if work_id:
            ids['ao3'] = work_id
        return ids

    def apply_series_identifier(
        self, identifiers: dict[str, str], series_id: str
    ) -> None:
        if series_id:
            identifiers['ao3series'] = series_id

    def book_matches(
        self,
        identifiers: dict[str, Any] | None,
        *,
        work_id: str = '',
        url: str = '',
    ) -> bool:
        ids = identifiers or {}
        work_id = str(work_id or '').strip()
        url = str(url or '').strip()
        existing_ao3 = str(ids.get('ao3') or '').strip()
        existing_url = str(ids.get('url') or '').strip()
        existing_from_url = work_id_from_url(existing_url) or ''
        wanted_from_url = work_id_from_url(url) or ''
        if work_id and existing_ao3 == work_id:
            return True
        if work_id and existing_from_url == work_id:
            return True
        if wanted_from_url and existing_from_url == wanted_from_url:
            return True
        if wanted_from_url and existing_ao3 == wanted_from_url:
            return True
        if url and existing_url.rstrip('/') == url.rstrip('/'):
            return True
        return False

    def plan_job(self, options: dict[str, Any], job_dir: Path) -> dict[str, Any]:
        try:
            from calibre_plugins.fanfic_organizer.job_plans import plan_scrape
        except ImportError:
            from job_plans import plan_scrape

        return plan_scrape(options, Path(job_dir))

    def run_import_dialog(self, gui: Any) -> dict[str, Any] | None:
        try:
            from calibre_plugins.fanfic_organizer.dialogs import ScrapeSearchDialog
            from calibre_plugins.fanfic_organizer.prefs import prefs
        except ImportError:
            return None

        dialog = ScrapeSearchDialog(gui)
        if not dialog.exec_():
            return None
        values = dialog.values()
        prefs['last_scrape_url'] = values['url']
        prefs['last_tag_id'] = values['tag_id']
        prefs['last_query'] = values['query']
        prefs['last_max_results'] = values['max_results'] or '25'
        prefs['download_epubs'] = values['download_epubs']
        prefs['simplify_tags'] = values['simplify_tags']
        prefs['drop_unmarked'] = values['drop_unmarked']
        prefs['update_existing'] = values['update_existing']
        return values
