# -*- coding: utf-8 -*-

from __future__ import annotations

from PyQt5.Qt import QMenu

from calibre.gui2 import error_dialog
from calibre.gui2.actions import InterfaceAction

from calibre_plugins.ao3_scraper.columns import ensure_plugin_columns
from calibre_plugins.ao3_scraper.dialogs import ImportJsonlDialog
from calibre_plugins.ao3_scraper.prefs import prefs
from calibre_plugins.ao3_scraper.progress import ImportProgressDialog

try:
    load_translations()
except NameError:
    pass


class AO3ScraperPlugin(InterfaceAction):
    name = 'AO3 Scraper'
    action_spec = ('AO3 Scraper', None, 'Import AO3 JSONL or EPUB zip', None)

    def genesis(self):
        self.qaction.triggered.connect(self.show_import_dialog)
        self.menu = QMenu(self.gui)
        self.qaction.setMenu(self.menu)
        self.menu.aboutToShow.connect(self.build_menu)
        self._import_dialog = None

    def initialization_complete(self):
        ensure_plugin_columns(self.gui.current_db)
        prefs['setup_complete'] = True

    def build_menu(self):
        self.menu.clear()
        self.menu.addAction('Import JSONL or zip...', self.show_import_dialog)
        self.menu.addAction('Plugin settings...', self.show_configuration)

    def apply_settings(self):
        ensure_plugin_columns(self.gui.current_db)

    def show_import_dialog(self):
        if self._import_dialog is not None and self._import_dialog.isVisible():
            self._import_dialog.raise_()
            self._import_dialog.activateWindow()
            return

        dialog = ImportJsonlDialog(self.gui)
        if not dialog.exec_():
            return

        values = dialog.values()
        if not values['path']:
            error_dialog(self.gui, 'AO3 Scraper', 'Choose a JSONL or import zip file.', show=True)
            return

        prefs['last_jsonl_path'] = values['path']
        prefs['simplify_tags'] = values['simplify_tags']

        progress = ImportProgressDialog(
            self.gui,
            path=values['path'],
            simplify_tags=values['simplify_tags'],
            update_existing=values['update_existing'],
        )
        self._import_dialog = progress
        progress.finished.connect(self._clear_import_dialog)
        progress.show()

    def _clear_import_dialog(self, *_args):
        self._import_dialog = None
