# -*- coding: utf-8 -*-
"""Generic web (URL / HTML file) source adapter."""

from __future__ import annotations

from pathlib import Path
from typing import Any

SOURCE_ID = 'web'
PUBLISHER = 'Web'
MENU_LABEL = 'URL or HTML...'
MENU_GROUP = 'import'
ID_KEY = 'web'


class WebSource:
    id = SOURCE_ID
    publisher = PUBLISHER
    menu_label = MENU_LABEL
    menu_group = MENU_GROUP
    include_series = False
    job_kind = 'web'

    def owns_record(self, record: dict[str, Any]) -> bool:
        text = str(record.get('source') or '').strip().casefold()
        return text == SOURCE_ID

    def work_id(self, record: dict[str, Any]) -> str:
        return str(record.get('work_id') or '').strip()

    def work_url(self, record: dict[str, Any]) -> str:
        return str(record.get('url') or '').strip()

    def identifiers(
        self, record: dict[str, Any], *, work_id: str, url: str
    ) -> dict[str, str]:
        ids: dict[str, str] = {}
        if url and not url.startswith('web:'):
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
        existing_web = str(ids.get(ID_KEY) or '').strip()
        existing_url = str(ids.get('url') or '').strip()
        if work_id and existing_web == work_id:
            return True
        if url and existing_url.rstrip('/') == url.rstrip('/'):
            return True
        return False

    def plan_job(self, options: dict[str, Any], job_dir: Path) -> dict[str, Any]:
        try:
            from calibre_plugins.fanfic_organizer.sources.web.plan import plan_web
        except ImportError:
            from sources.web.plan import plan_web

        return plan_web(options, Path(job_dir))

    def run_import_dialog(self, gui: Any) -> dict[str, Any] | None:
        try:
            from calibre_plugins.fanfic_organizer.prefs import prefs
            from calibre_plugins.fanfic_organizer.sources.web.dialog import (
                WebImportDialog,
            )
        except ImportError:
            return None

        dialog = WebImportDialog(gui)
        if not dialog.exec_():
            return None
        values = dialog.values()
        prefs['last_web_mode'] = values.get('mode') or 'single'
        prefs['last_web_url'] = values.get('url') or ''
        prefs['last_web_html_path'] = values.get('html_path') or ''
        prefs['last_web_seeds'] = '\n'.join(values.get('seeds') or [])
        prefs['last_web_full_list'] = bool(values.get('full_list'))
        prefs['last_web_expand'] = values.get('expand') or 'same_domain'
        prefs['last_web_domains'] = ', '.join(values.get('domains') or [])
        prefs['last_web_max_pages'] = int(values.get('max_pages') or 50)
        prefs['last_web_max_depth'] = int(values.get('max_depth') or 2)
        prefs['last_web_book_title'] = values.get('book_title') or ''
        prefs['last_web_bundle_path'] = values.get('bundle_path') or ''
        prefs['web_build_epub'] = bool(values.get('download_epubs', True))
        prefs['update_existing'] = values['update_existing']
        return values
