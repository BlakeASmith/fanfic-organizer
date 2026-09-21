# -*- coding: utf-8 -*-
"""TWC journal source adapter (OJS on journal.transformativeworks.org)."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

SOURCE_ID = 'twc'
PUBLISHER = 'Transformative Works and Cultures'
MENU_LABEL = 'TWC journal...'
MENU_GROUP = 'import'
ID_KEY = 'twc'

TWC_URL_RE = re.compile(
    r'(?:https?://)?(?:www\.)?journal\.transformativeworks\.org/index\.php/twc/(?:issue|article)/view/\d+',
    re.IGNORECASE,
)


class TwcSource:
    id = SOURCE_ID
    publisher = PUBLISHER
    menu_label = MENU_LABEL
    menu_group = MENU_GROUP
    include_series = False
    job_kind = 'twc'

    def owns_record(self, record: dict[str, Any]) -> bool:
        text = str(record.get('source') or '').strip().casefold()
        if text == SOURCE_ID:
            return True
        if text:
            return False
        return bool(TWC_URL_RE.search(str(record.get('url') or '')))

    def work_id(self, record: dict[str, Any]) -> str:
        return str(record.get('work_id') or '').strip()

    def work_url(self, record: dict[str, Any]) -> str:
        return str(record.get('url') or '').strip()

    def identifiers(
        self, record: dict[str, Any], *, work_id: str, url: str
    ) -> dict[str, str]:
        ids: dict[str, str] = {}
        if url:
            ids['url'] = url
        if work_id:
            ids[ID_KEY] = work_id
        return ids

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
        existing = str(ids.get(ID_KEY) or '').strip()
        existing_url = str(ids.get('url') or '').strip()
        if work_id and existing == work_id:
            return True
        if url and existing_url.rstrip('/') == url.rstrip('/'):
            return True
        return False

    def plan_job(self, options: dict[str, Any], job_dir: Path) -> dict[str, Any]:
        try:
            from calibre_plugins.fanfic_organizer.sources.twc.plan import plan_twc
        except ImportError:
            from sources.twc.plan import plan_twc

        return plan_twc(options, Path(job_dir))

    def run_import_dialog(self, gui: Any) -> dict[str, Any] | None:
        try:
            from calibre_plugins.fanfic_organizer.prefs import prefs
            from calibre_plugins.fanfic_organizer.sources.twc.dialog import (
                TwcImportDialog,
            )
        except ImportError:
            return None

        dialog = TwcImportDialog(gui)
        if not dialog.exec_():
            return None
        values = dialog.values()
        prefs['last_twc_url'] = values['url']
        prefs['twc_build_epub'] = bool(values.get('download_epubs', True))
        return values
