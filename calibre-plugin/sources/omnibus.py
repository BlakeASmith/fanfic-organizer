# -*- coding: utf-8 -*-
"""Omnibus (combined EPUB) source adapter for Calibre matching / import."""

from __future__ import annotations

from typing import Any

SOURCE_ID = 'omnibus'
PUBLISHER = 'Fanfic Organizer'
MENU_LABEL = ''
MENU_GROUP = 'toolbar'


class OmnibusSource:
    id = SOURCE_ID
    publisher = PUBLISHER
    menu_label = MENU_LABEL
    menu_group = MENU_GROUP
    include_series = False
    job_kind = 'omnibus'

    def owns_record(self, record: dict[str, Any]) -> bool:
        text = str(record.get('source') or '').strip().casefold()
        if text == SOURCE_ID:
            return True
        ids = record.get('identifiers') if isinstance(record.get('identifiers'), dict) else {}
        if ids.get('omnibus'):
            return True
        meta = record.get('metadata') if isinstance(record.get('metadata'), dict) else {}
        return bool(meta.get('omnibus'))

    def work_id(self, record: dict[str, Any]) -> str:
        ids = record.get('identifiers') if isinstance(record.get('identifiers'), dict) else {}
        oid = str(ids.get('omnibus') or '').strip()
        if oid:
            return oid
        omni = record.get('omnibus') if isinstance(record.get('omnibus'), dict) else {}
        return str(omni.get('id') or record.get('work_id') or '').strip()

    def work_url(self, record: dict[str, Any]) -> str:
        return ''

    def identifiers(
        self, record: dict[str, Any], *, work_id: str, url: str
    ) -> dict[str, str]:
        ids: dict[str, str] = {}
        existing = record.get('identifiers') if isinstance(record.get('identifiers'), dict) else {}
        for key in ('omnibus', 'ao3members', 'ao3series', 'omnibuscollection'):
            val = str(existing.get(key) or '').strip()
            if val:
                ids[key] = val
        if work_id and 'omnibus' not in ids:
            ids['omnibus'] = work_id
        omni = record.get('omnibus') if isinstance(record.get('omnibus'), dict) else {}
        if omni.get('id') and 'omnibus' not in ids:
            ids['omnibus'] = str(omni['id'])
        if omni.get('series_id') and 'ao3series' not in ids:
            ids['ao3series'] = str(omni['series_id'])
        if omni.get('collection') and 'omnibuscollection' not in ids:
            ids['omnibuscollection'] = str(omni['collection'])
        if omni.get('member_ids') and 'ao3members' not in ids:
            ids['ao3members'] = ','.join(str(m) for m in omni['member_ids'])
        return ids

    def book_matches(
        self,
        identifiers: dict[str, Any] | None,
        *,
        work_id: str = '',
        url: str = '',
    ) -> bool:
        ids = identifiers or {}
        existing = str(ids.get('omnibus') or '').strip()
        wanted = str(work_id or '').strip()
        return bool(wanted and existing and existing == wanted)

    def plan_job(self, options: dict[str, Any], job_dir: Any) -> dict[str, Any]:
        raise NotImplementedError('omnibus jobs use job_plans.plan_omnibus_*')

    def run_import_dialog(self, gui: Any) -> dict[str, Any] | None:
        return None
