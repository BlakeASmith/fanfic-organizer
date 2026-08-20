# -*- coding: utf-8 -*-

from __future__ import annotations

import traceback

from PyQt5.Qt import QMenu

from calibre.gui2 import error_dialog, info_dialog
from calibre.gui2.actions import InterfaceAction
from calibre.gui2.threaded import ThreadedJob

from calibre_plugins.ao3_scraper.columns import ensure_raw_metadata_column
from calibre_plugins.ao3_scraper.dialogs import ImportJsonlDialog
from calibre_plugins.ao3_scraper.importer import import_records
from calibre_plugins.ao3_scraper.jsonl_loader import load_jsonl_records
from calibre_plugins.ao3_scraper.prefs import prefs

try:
    load_translations()
except NameError:
    pass


class AO3ScraperPlugin(InterfaceAction):
    name = 'AO3 Scraper'
    action_spec = ('AO3 Scraper', None, 'Import AO3 JSONL metadata', None)

    def genesis(self):
        self.qaction.triggered.connect(self.show_import_dialog)
        self.menu = QMenu(self.gui)
        self.qaction.setMenu(self.menu)
        self.menu.aboutToShow.connect(self.build_menu)

    def initialization_complete(self):
        ensure_raw_metadata_column(self.gui.current_db)
        prefs['setup_complete'] = True

    def build_menu(self):
        self.menu.clear()
        self.menu.addAction('Import JSONL...', self.show_import_dialog)
        self.menu.addAction('Plugin settings...', self.show_configuration)

    def apply_settings(self):
        ensure_raw_metadata_column(self.gui.current_db)

    def show_import_dialog(self):
        dialog = ImportJsonlDialog(self.gui)
        if not dialog.exec_():
            return

        values = dialog.values()
        if not values['path']:
            error_dialog(self.gui, 'AO3 Scraper', 'Choose a JSONL file to import.', show=True)
            return

        prefs['last_jsonl_path'] = values['path']

        job = ThreadedJob(
            'ao3_scraper_import',
            'Importing AO3 JSONL',
            self.run_import,
            (values['path'], values['update_existing']),
            {},
            self.import_finished,
            abortable=False,
        )
        self.gui.job_manager.run_threaded_job(job)

    def run_import(self, path: str, update_existing: bool, *args, **kwargs):
        records = load_jsonl_records(path)
        if not records:
            raise ValueError('The JSONL file contains no records.')
        return {
            'records': records,
            'update_existing': update_existing,
        }

    def import_finished(self, job):
        if job.failed:
            error_dialog(
                self.gui,
                'AO3 Scraper',
                'Import failed.',
                det_msg=str(job.exception) + '\n\n' + traceback.format_exc(),
                show=True,
            )
            return

        result = job.result
        db = self.gui.current_db
        ensure_raw_metadata_column(db)
        outcomes = import_records(
            db,
            result['records'],
            update_existing=result['update_existing'],
        )

        added = sum(1 for x in outcomes if x['action'] == 'added')
        updated = sum(1 for x in outcomes if x['action'] == 'updated')
        skipped = sum(1 for x in outcomes if x['action'] == 'skipped')
        info_dialog(
            self.gui,
            'AO3 Scraper',
            f'Imported {len(outcomes)} works ({added} added, {updated} updated, {skipped} skipped).',
            show=True,
        )
        self.gui.library_view.model().refresh()
