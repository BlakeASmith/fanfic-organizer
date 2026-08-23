# -*- coding: utf-8 -*-

from __future__ import annotations

from PyQt5.Qt import QIcon, QMenu, QPixmap, QToolButton

from calibre.gui2 import error_dialog, info_dialog, question_dialog
from calibre.gui2.actions import InterfaceAction

from calibre_plugins.wranglekit.dialogs import (
    ImportJsonlDialog,
    ScrapeSearchDialog,
    SimilarSearchDialog,
    TagMappingsDialog,
    TagPurgeDialog,
)
from calibre_plugins.wranglekit.prefs import plugin_runtime_settings, prefs
from calibre_plugins.wranglekit.scrape_run import merge_plugin_settings

try:
    load_translations()
except NameError:
    pass


PLUGIN_ICON = 'images/icon.png'


def load_plugin_icon(action) -> QIcon:
    try:
        data = action.load_resources([PLUGIN_ICON]).get(PLUGIN_ICON)
    except Exception:
        data = None
    if not data:
        return QIcon()
    pixmap = QPixmap()
    if not pixmap.loadFromData(data):
        return QIcon()
    return QIcon(pixmap)


class WranglekitPlugin(InterfaceAction):
    name = 'Wranglekit'
    action_spec = (
        'Wranglekit',
        None,
        'Search AO3, manage selected books, tags, and jobs',
        None,
    )
    popup_type = QToolButton.InstantPopup

    def genesis(self):
        icon = load_plugin_icon(self)
        self.qaction.setIcon(icon)
        self.qaction.triggered.connect(self.show_plugin_menu)
        self.menu = QMenu(self.gui)
        self.qaction.setMenu(self.menu)
        self.menu.aboutToShow.connect(self.build_menu)
        self._jobs = None

    def jobs(self):
        if self._jobs is None:
            from calibre_plugins.wranglekit.job_supervise import JobSupervisor

            self._jobs = JobSupervisor(self)
        return self._jobs

    def initialization_complete(self):
        # Watch leftover jobs from the last session (pending Calibre ingest).
        # Do not create columns or write the open library on startup.
        self.jobs()
        self._apply_popup_mode()

    def _selected_ids(self):
        try:
            return list(self.gui.library_view.get_selected_ids())
        except Exception:
            return []

    def _toolbar_button(self):
        bars_manager = getattr(self.gui, 'bars_manager', None)
        bars = getattr(bars_manager, 'bars', None) if bars_manager is not None else None
        if not bars:
            return None
        for bar in bars:
            widget = bar.widgetForAction(self.qaction)
            if widget is not None:
                return widget
        return None

    def _apply_popup_mode(self):
        self.popup_type = QToolButton.InstantPopup
        bars_manager = getattr(self.gui, 'bars_manager', None)
        bars = getattr(bars_manager, 'bars', None) if bars_manager is not None else None
        if not bars:
            return
        for bar in bars:
            widget = bar.widgetForAction(self.qaction)
            if widget is not None:
                widget.setPopupMode(self.popup_type)

    def show_plugin_menu(self):
        self._apply_popup_mode()
        widget = self._toolbar_button()
        if widget is not None and hasattr(widget, 'showMenu'):
            widget.showMenu()
            return
        try:
            from PyQt5.Qt import QCursor
        except ImportError:
            from PyQt5.QtGui import QCursor
        self.menu.popup(QCursor.pos())

    def build_menu(self):
        self.menu.clear()
        selected_ids = self._selected_ids()
        n = len(selected_ids)
        has_selection = n > 0

        self.menu.addAction('Search AO3 and import...', self.show_scrape_dialog)
        similar = self.menu.addAction(
            'Search similar...', self.show_similar_dialog
        )
        similar.setEnabled(has_selection)
        similar.setStatusTip('Build an AO3 search from the selected books')
        self.menu.addAction('Import JSONL or zip...', self.show_import_dialog)

        self.menu.addSeparator()
        if n == 0:
            selected_label = 'Selected books'
        elif n == 1:
            selected_label = 'Selected book'
        else:
            selected_label = f'Selected books ({n})'
        selected = self.menu.addMenu(selected_label)
        selected.setEnabled(has_selection)
        selected.addAction('Complete selected...', self.complete_selected_books)
        selected.addSeparator()
        selected.addAction('Download EPUB...', self.download_selected_epubs)
        selected.addAction('Generate covers...', self.generate_covers_for_selected)
        selected.addAction(
            'Import rest of series...', self.import_series_for_selected
        )
        selected.addAction('Fill series...', self.fill_series_for_selected)
        selected.addSeparator()
        selected.addAction(
            'Simplify tags, fandoms & relationships...',
            self.simplify_selected_books,
        )
        selected.addSeparator()
        selected.addAction(
            'Edit collections...', self.edit_collections_of_selected
        )
        selected.addAction(
            'Recompute collections...',
            self.recompute_collections_for_selected,
        )
        selected.addAction(
            'Add to a collection...',
            self.add_selected_books_to_collection,
        )

        self.menu.addSeparator()
        self.menu.addAction('Running jobs...', self.show_running_jobs)

        self.menu.addSeparator()
        tags = self.menu.addMenu('Tags and collections')
        tags.addAction(
            'Collections & tag rules...', self.show_tag_mappings_dialog
        )
        tags.addAction('Tag graph...', self.show_tag_graph)
        tags.addAction('Tag purge...', self.show_tag_purge_dialog)
        tags.addSeparator()
        tags.addAction('Warm tag cache...', self.warm_tag_cache)
        tags.addAction('Tag cache log...', self.show_tag_cache_log)
        tags.addAction('Stop tag cache...', self.stop_tag_cache_warm)

        self.menu.addSeparator()
        self.menu.addAction('Plugin settings...', self.show_configuration)

    def show_running_jobs(self):
        self.jobs().show_list()

    def apply_settings(self):
        return

    def show_configuration(self):
        self.interface_action_base_plugin.do_user_config(parent=self.gui)

    def show_import_dialog(self):
        dialog = ImportJsonlDialog(self.gui)
        if not dialog.exec_():
            return

        values = dialog.values()
        if not values['path']:
            error_dialog(self.gui, 'Wranglekit', 'Choose a JSONL or import zip file.', show=True)
            return

        prefs['last_jsonl_path'] = values['path']
        prefs['simplify_tags'] = values['simplify_tags']
        prefs['update_existing'] = values['update_existing']

        from calibre_plugins.wranglekit.job_plans import plan_import
        from calibre_plugins.wranglekit.jsonl_loader import load_import_source

        try:
            records, bundle_root, cleanup = load_import_source(values['path'])
        except Exception as exc:
            error_dialog(
                self.gui,
                'Wranglekit',
                'Could not read that import file.',
                det_msg=str(exc),
                show=True,
            )
            return
        if not records:
            error_dialog(
                self.gui, 'Wranglekit', 'The import file contains no records.', show=True
            )
            return
        job_dir = self.jobs().prepare_job_dir('import')
        if job_dir is None:
            return
        plan_import(
            records,
            job_dir,
            options={
                **merge_plugin_settings({}, plugin_runtime_settings()),
                'simplify_tags': values['simplify_tags'],
                'update_existing': values['update_existing'],
                'include_series': bool(prefs.get('import_full_series', False)),
            },
            bundle_root=bundle_root,
            cleanup_dir=str(cleanup) if cleanup else None,
        )
        self.jobs().start_prepared(job_dir)

    def show_scrape_dialog(self):
        dialog = ScrapeSearchDialog(self.gui)
        if not dialog.exec_():
            return

        values = dialog.values()
        prefs['last_scrape_url'] = values['url']
        prefs['last_tag_id'] = values['tag_id']
        prefs['last_query'] = values['query']
        prefs['last_max_results'] = values['max_results'] or '25'
        prefs['download_epubs'] = values['download_epubs']
        prefs['simplify_tags'] = values['simplify_tags']
        prefs['update_existing'] = values['update_existing']

        from calibre_plugins.wranglekit.job_plans import plan_scrape

        job_dir = self.jobs().prepare_job_dir('scrape')
        if job_dir is None:
            return
        plan_scrape(
            merge_plugin_settings(values, plugin_runtime_settings()),
            job_dir,
        )
        self.jobs().start_prepared(job_dir)

    def show_similar_dialog(self):
        book_ids = list(self.gui.library_view.get_selected_ids())
        if not book_ids:
            error_dialog(
                self.gui,
                'Wranglekit',
                'Select one or more books in the library first.',
                show=True,
            )
            return

        from calibre_plugins.wranglekit.selected import load_selected_similar_records
        from calibre_plugins.wranglekit.similar import facets_from_records

        ready, skipped = load_selected_similar_records(self.gui.current_db, book_ids)
        if not ready:
            extra = ''
            if skipped:
                extra = '\n\n' + '\n'.join(
                    f'{item.get("title")}: {item.get("reason")}' for item in skipped[:8]
                )
            error_dialog(
                self.gui,
                'Wranglekit',
                'None of the selected books have fandoms, tags, characters, '
                'or an author to search from.' + extra,
                show=True,
            )
            return

        records = [item['record'] for item in ready]
        titles = [item.get('title') or '' for item in ready]
        facets = facets_from_records(records)
        dialog = SimilarSearchDialog(self.gui, facets, titles=titles)
        if not dialog.exec_():
            return

        values = dialog.values()
        prefs['last_tag_id'] = values['tag_id']
        prefs['last_query'] = values['query']
        prefs['last_max_results'] = values['max_results'] or '25'
        prefs['download_epubs'] = values['download_epubs']
        prefs['simplify_tags'] = values['simplify_tags']
        prefs['update_existing'] = values['update_existing']

        from calibre_plugins.wranglekit.job_plans import plan_scrape

        job_dir = self.jobs().prepare_job_dir('scrape')
        if job_dir is None:
            return
        plan_scrape(
            merge_plugin_settings(values, plugin_runtime_settings()),
            job_dir,
        )
        self.jobs().start_prepared(job_dir)

    def download_selected_epubs(self):
        book_ids = list(self.gui.library_view.get_selected_ids())
        if not book_ids:
            error_dialog(
                self.gui,
                'Wranglekit',
                'Select one or more books in the library first.',
                show=True,
            )
            return

        from calibre_plugins.wranglekit.epub_plan import REASON_HAS_EPUB, REASON_NO_AO3
        from calibre_plugins.wranglekit.selected import load_selected_for_epub_download

        ready, skipped = load_selected_for_epub_download(
            self.gui.current_db, book_ids
        )
        already = [item for item in skipped if item.get('reason') == REASON_HAS_EPUB]
        no_id = [item for item in skipped if item.get('reason') == REASON_NO_AO3]
        if not ready:
            if already and not no_id:
                info_dialog(
                    self.gui,
                    'Wranglekit',
                    'Selected books already have an EPUB. Nothing to download.',
                    show=True,
                )
                return
            error_dialog(
                self.gui,
                'Wranglekit',
                'None of the selected books have an AO3 URL or work id to download.',
                show=True,
            )
            return

        skip_bits = []
        if already:
            skip_bits.append(f'{len(already)} already have an EPUB')
        if no_id:
            skip_bits.append(f'{len(no_id)} have no AO3 URL / work id')
        skip_note = f'\n\nSkipping {", ".join(skip_bits)}.' if skip_bits else ''
        noun = 'book' if len(ready) == 1 else 'books'
        if not question_dialog(
            self.gui,
            'Wranglekit',
            (
                f'Download the native AO3 EPUB for {len(ready)} selected {noun} '
                f'that do not already have one?{skip_note}\n\n'
                'Uses each book\'s AO3 URL / work id. Existing EPUB files are '
                'left unchanged.'
            ),
        ):
            return

        from calibre_plugins.wranglekit.job_plans import plan_download_selected

        job_dir = self.jobs().prepare_job_dir('download')
        if job_dir is None:
            return
        plan_download_selected(
            ready,
            skipped,
            job_dir,
            merge_plugin_settings({}, plugin_runtime_settings()),
        )
        self.jobs().start_prepared(job_dir)

    def generate_covers_for_selected(self):
        book_ids = list(self.gui.library_view.get_selected_ids())
        if not book_ids:
            error_dialog(
                self.gui,
                'Wranglekit',
                'Select one or more books in the library first.',
                show=True,
            )
            return

        from pathlib import Path

        from calibre_plugins.wranglekit.cover_ui import load_cover_dict
        from calibre_plugins.wranglekit.job_plans import plan_cover_selected
        from calibre_plugins.wranglekit.selected import (
            export_selected_epubs_for_cover,
            load_selected_for_covers,
        )

        ready, skipped = load_selected_for_covers(self.gui.current_db, book_ids)
        if not ready:
            error_dialog(
                self.gui,
                'Wranglekit',
                'None of the selected books have a title to put on a cover.',
                show=True,
            )
            return
        with_epub = sum(1 for item in ready if item.get('has_epub'))
        without = len(ready) - with_epub
        noun = 'book' if len(ready) == 1 else 'books'
        extras = []
        if with_epub:
            extras.append(f'{with_epub} EPUB file(s) will be restamped.')
        if without:
            extras.append(
                f'{without} without an EPUB will only get a Calibre cover.'
            )
        if skipped:
            extras.append(f'Skipping {len(skipped)} with no title.')
        if not question_dialog(
            self.gui,
            'Wranglekit',
            (
                f'Generate covers for {len(ready)} selected {noun}?\n\n'
                'Uses title, author, word count, and quality score from the library. Style is '
                'set in Plugin settings → Cover style.\n\n'
                + '\n'.join(extras)
            ),
        ):
            return

        job_dir = self.jobs().prepare_job_dir('cover')
        if job_dir is None:
            return
        epub_dir = Path(job_dir) / 'work' / 'bundle' / 'epubs'
        ready = export_selected_epubs_for_cover(
            self.gui.current_db, ready, epub_dir
        )
        cover = load_cover_dict()
        plan_cover_selected(
            ready,
            skipped,
            job_dir,
            merge_plugin_settings(
                {'set_calibre_cover': bool(cover.get('set_calibre_cover', True))},
                plugin_runtime_settings(),
            ),
        )
        self.jobs().start_prepared(job_dir)

    def complete_selected_books(self):
        book_ids = list(self.gui.library_view.get_selected_ids())
        if not book_ids:
            error_dialog(
                self.gui,
                'Wranglekit',
                'Select one or more books in the library first.',
                show=True,
            )
            return

        noun = 'book' if len(book_ids) == 1 else 'books'
        if not question_dialog(
            self.gui,
            'Wranglekit',
            (
                f'Complete the {len(book_ids)} selected {noun}?\n\n'
                'This looks up each book on AO3 and then:\n'
                '• fills Calibre’s Series column\n'
                '• imports any other parts of those series\n'
                '• downloads native EPUBs that are missing\n'
                '• simplifies tags, fandoms, and relationships\n'
                '• recomputes collections from your rules\n\n'
                'Existing EPUBs are left unchanged. This can take a while; '
                'it runs in the background.'
            ),
        ):
            return

        from pathlib import Path

        from calibre_plugins.wranglekit.job_plans import plan_complete_selected
        from calibre_plugins.wranglekit.selected import (
            copy_book_epub,
            load_selected_records,
        )

        ready, skipped = load_selected_records(self.gui.current_db, book_ids)
        if not ready:
            error_dialog(
                self.gui,
                'Wranglekit',
                'None of the selected books have an AO3 URL or work id.',
                show=True,
            )
            return
        job_dir = self.jobs().prepare_job_dir('complete')
        if job_dir is None:
            return
        epub_dir = Path(job_dir) / 'work' / 'bundle' / 'epubs'
        epub_dir.mkdir(parents=True, exist_ok=True)
        db = self.gui.current_db
        for item in ready:
            work_id = str((item.get('record') or {}).get('work_id') or '').strip()
            if work_id:
                copy_book_epub(db, item['book_id'], epub_dir / f'{work_id}.epub')
        plan_complete_selected(
            [item['record'] for item in ready],
            skipped,
            job_dir,
            merge_plugin_settings({}, plugin_runtime_settings()),
        )
        self.jobs().start_prepared(job_dir)

    def import_series_for_selected(self):
        book_ids = list(self.gui.library_view.get_selected_ids())
        if not book_ids:
            error_dialog(
                self.gui,
                'Wranglekit',
                'Select one or more books in the library first.',
                show=True,
            )
            return

        download = bool(prefs.get('download_epubs', True))
        simplify = bool(prefs.get('simplify_tags', False))
        noun = 'book' if len(book_ids) == 1 else 'books'
        extras = []
        extras.append(
            'Native EPUBs will be downloaded for missing parts.'
            if download
            else 'Metadata only (EPUB download is off in plugin settings).'
        )
        if simplify:
            extras.append('Tags will be simplified after lookup.')
        if not question_dialog(
            self.gui,
            'Wranglekit',
            (
                f'Import other works in the same AO3 series as the '
                f'{len(book_ids)} selected {noun}?\n\n'
                'Looks up each book\'s series from stored metadata or AO3, '
                'then imports every part. Books already in this library are '
                'updated (series column) and existing EPUBs are left unchanged.\n\n'
                + '\n'.join(extras)
            ),
        ):
            return

        from calibre_plugins.wranglekit.job_plans import plan_import_series
        from calibre_plugins.wranglekit.selected import load_selected_records

        ready, skipped = load_selected_records(self.gui.current_db, book_ids)
        if not ready:
            error_dialog(
                self.gui,
                'Wranglekit',
                'None of the selected books have an AO3 URL or work id.',
                show=True,
            )
            return
        job_dir = self.jobs().prepare_job_dir('series')
        if job_dir is None:
            return
        plan_import_series(
            [item['record'] for item in ready],
            skipped,
            job_dir,
            merge_plugin_settings(
                {
                    'download_epubs': bool(prefs.get('download_epubs', True)),
                    'simplify_tags': bool(prefs.get('simplify_tags', False)),
                    'update_existing': bool(prefs.get('update_existing', True)),
                },
                plugin_runtime_settings(),
            ),
        )
        self.jobs().start_prepared(job_dir)

    def fill_series_for_selected(self):
        book_ids = list(self.gui.library_view.get_selected_ids())
        if not book_ids:
            error_dialog(
                self.gui,
                'Wranglekit',
                'Select one or more books in the library first.',
                show=True,
            )
            return

        noun = 'book' if len(book_ids) == 1 else 'books'
        if not question_dialog(
            self.gui,
            'Wranglekit',
            (
                f'Fill Calibre\'s Series column for the {len(book_ids)} '
                f'selected {noun}?\n\n'
                'Looks up each book\'s AO3 series from the work page and '
                'writes Series, series index, and the ao3series identifier. '
                'Does not import other parts of the series. Tags and EPUBs '
                'are left unchanged. Books that are not in a series stay as '
                'they are.'
            ),
        ):
            return

        from calibre_plugins.wranglekit.job_plans import plan_fill_series
        from calibre_plugins.wranglekit.selected import load_selected_records

        ready, skipped = load_selected_records(self.gui.current_db, book_ids)
        if not ready:
            error_dialog(
                self.gui,
                'Wranglekit',
                'None of the selected books have an AO3 URL or work id.',
                show=True,
            )
            return
        job_dir = self.jobs().prepare_job_dir('fill_series')
        if job_dir is None:
            return
        plan_fill_series(
            ready,
            skipped,
            job_dir,
            merge_plugin_settings({}, plugin_runtime_settings()),
        )
        self.jobs().start_prepared(job_dir)

    def simplify_selected_books(self):
        book_ids = list(self.gui.library_view.get_selected_ids())
        if not book_ids:
            error_dialog(
                self.gui,
                'Wranglekit',
                'Select one or more books in the library first.',
                show=True,
            )
            return

        if not question_dialog(
            self.gui,
            'Wranglekit',
            (
                f'Simplify tags, fandoms, and relationships for {len(book_ids)} '
                f'selected book(s) in the currently open library?\n\n'
                'Uses each book\'s AO3 URL / work id, Original Tags, Fandom, '
                'and Relationships (if present), runs AO3 cleanup + collection '
                'rules + tag rules + your .ao3kit Python rules, then writes '
                'Fandom, Relationships, Collections, word count, Original Tags, '
                'and Tags.'
            ),
        ):
            return

        from calibre_plugins.wranglekit.job_plans import plan_simplify_selected
        from calibre_plugins.wranglekit.selected import load_selected_records

        ready, skipped = load_selected_records(self.gui.current_db, book_ids)
        if not ready:
            error_dialog(
                self.gui,
                'Wranglekit',
                'None of the selected books have an AO3 URL or work id.',
                show=True,
            )
            return
        job_dir = self.jobs().prepare_job_dir('enrich')
        if job_dir is None:
            return
        plan_simplify_selected(
            ready,
            skipped,
            job_dir,
            merge_plugin_settings({}, plugin_runtime_settings()),
        )
        self.jobs().start_prepared(job_dir)

    def recompute_collections_for_selected(self, *args, confirm=True):
        book_ids = list(self.gui.library_view.get_selected_ids())
        if not book_ids:
            error_dialog(
                self.gui,
                'Wranglekit',
                'Select one or more books in the library first.',
                show=True,
            )
            return

        noun = 'book' if len(book_ids) == 1 else 'books'
        if confirm and not question_dialog(
            self.gui,
            'Wranglekit',
            (
                f'Recompute collections for {len(book_ids)} selected {noun} '
                f'in the currently open library?\n\n'
                'Replaces the Collections column from your collection rules. '
                'Does not fetch AO3 or change tags. Collections you added by '
                'hand on a book are saved as a per-work rule so they come back. '
                'To keep a book out of a collection, add a Never rule — '
                'removing it in Calibre alone does not stick.'
            ),
        ):
            return

        from calibre_plugins.wranglekit.job_plans import plan_simplify_selected
        from calibre_plugins.wranglekit.selected import load_selected_for_collections

        ready, skipped = load_selected_for_collections(self.gui.current_db, book_ids)
        if not ready:
            error_dialog(
                self.gui,
                'Wranglekit',
                'None of the selected books could be loaded.',
                show=True,
            )
            return
        job_dir = self.jobs().prepare_job_dir('collections')
        if job_dir is None:
            return
        plan_simplify_selected(
            ready,
            skipped,
            job_dir,
            merge_plugin_settings({}, plugin_runtime_settings()),
            collections_only=True,
        )
        self.jobs().start_prepared(job_dir)

    def edit_collections_of_selected(self, *args):
        book_ids = list(self.gui.library_view.get_selected_ids())
        if not book_ids:
            error_dialog(
                self.gui,
                'Wranglekit',
                'Select one or more books in the library first.',
                show=True,
            )
            return

        from calibre_plugins.wranglekit.collection_edit import (
            EditSelectedCollectionsDialog,
        )

        dialog = EditSelectedCollectionsDialog(self.gui, book_ids)
        dialog.exec_()

    def apply_collection_rules_to_selected(self, *args, confirm=True):
        self.recompute_collections_for_selected(*args, confirm=confirm)

    def add_selected_books_to_collection(self, *args, collection_name='', confirm=True):
        book_ids = list(self.gui.library_view.get_selected_ids())
        if not book_ids:
            error_dialog(
                self.gui,
                'Wranglekit',
                'Select one or more books in the library first.',
                show=True,
            )
            return

        name = str(collection_name or '').strip()
        if not name:
            from calibre_plugins.wranglekit.collection_edit import (
                load_known_collection_names,
                prompt_collection_name,
            )

            name = prompt_collection_name(
                self.gui,
                load_known_collection_names(self.gui, self.gui.current_db),
                prompt=(
                    'Add the selected books to this collection (pick an '
                    'existing name or type a new one):'
                ),
            )
            if not name:
                return
        if not name:
            error_dialog(
                self.gui,
                'Wranglekit',
                'Type a collection name first.',
                show=True,
            )
            return

        noun = 'book' if len(book_ids) == 1 else 'books'
        if confirm and not question_dialog(
            self.gui,
            'Wranglekit',
            (
                f'Add {len(book_ids)} selected {noun} to “{name}”?\n\n'
                'This saves a per-work rule, then updates Collections from '
                'your rules. Tags are left as they are (no AO3 tag lookup).'
            ),
        ):
            return

        from calibre_plugins.wranglekit.collection_rules import (
            build_collections_pin_argv,
        )
        from calibre_plugins.wranglekit.enrich import EnrichCancelled, run_ao3kit
        from calibre_plugins.wranglekit.selected import pin_targets_from_selected

        targets, skipped = pin_targets_from_selected(self.gui.current_db, book_ids)
        if not targets:
            extra = ''
            if skipped:
                extra = '\n\n' + '\n'.join(
                    f'{item.get("title")}: {item.get("reason")}'
                    for item in skipped[:8]
                )
            error_dialog(
                self.gui,
                'Wranglekit',
                'None of the selected books have an AO3 work id or Calibre UUID.'
                + extra,
                show=True,
            )
            return

        try:
            from PyQt5.Qt import QApplication, Qt
        except ImportError:
            from PyQt5.QtWidgets import QApplication
            from PyQt5.QtCore import Qt
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            for item in targets:
                argv = build_collections_pin_argv(
                    collection=name,
                    work_id=item['work_id'],
                    uuid=item['uuid'] if not item['work_id'] else '',
                    description=item['title'],
                )
                code, stdout, stderr = run_ao3kit(argv)
                if code != 0:
                    error_dialog(
                        self.gui,
                        'Wranglekit',
                        f'Could not pin “{item.get("title") or item["book_id"]}” '
                        f'to {name}.',
                        det_msg=(stderr or stdout or f'exit {code}').strip(),
                        show=True,
                    )
                    return
        except EnrichCancelled:
            return
        finally:
            QApplication.restoreOverrideCursor()

        self.recompute_collections_for_selected(confirm=False)

    def warm_tag_cache(self):
        db = self.gui.current_db
        from calibre_plugins.wranglekit.tag_purge import scope_book_ids

        book_ids = scope_book_ids(db, '')
        noun = 'book' if len(book_ids) == 1 else 'books'
        if not question_dialog(
            self.gui,
            'Wranglekit',
            (
                f'Collect tags from all {len(book_ids)} {noun} in the open '
                'library and fetch uncached AO3 mappings in the background?\n\n'
                'This does not change the library. It only fills ao3kit\'s '
                'tag cache so Simplify / import later skip the slow AO3 '
                'lookups.\n\n'
                'Pace is slow so Search and Download can still use AO3. '
                'Stop it from Tags and collections → Stop tag cache when you want.'
            ),
        ):
            return

        from PyQt5.Qt import QApplication, Qt

        from calibre_plugins.wranglekit.enrich import (
            EnrichCancelled,
            resolve_ao3kit_runtime,
            run_ao3kit,
        )
        from calibre_plugins.wranglekit.scrape_run import (
            build_warm_start_argv,
            merge_plugin_settings,
        )
        from calibre_plugins.wranglekit.selected import load_records_for_tag_warm
        from calibre_plugins.wranglekit.tag_warm import (
            format_warm_started_text,
            parse_warm_status_json,
            unique_tag_names_from_records,
            write_names_file,
        )

        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            project, _python, error = resolve_ao3kit_runtime()
            if error or project is None:
                error_dialog(
                    self.gui,
                    'Wranglekit',
                    'Could not find ao3kit. Install wranglekit.zip from GitHub '
                    'Releases, or set Project path in plugin settings.',
                    det_msg=error or '',
                    show=True,
                )
                return

            records = load_records_for_tag_warm(db, book_ids)
            names = unique_tag_names_from_records(records)
            if not names:
                error_dialog(
                    self.gui,
                    'Wranglekit',
                    'No tags or fandoms found on books in this library.',
                    show=True,
                )
                return

            seed = project / '.cache' / 'tag_warm_names.txt'
            write_names_file(seed, names)
            argv = build_warm_start_argv(
                str(seed),
                merge_plugin_settings({}, plugin_runtime_settings()),
            )
            code, stdout, stderr = run_ao3kit(argv)
        except EnrichCancelled:
            return
        finally:
            QApplication.restoreOverrideCursor()

        status = parse_warm_status_json(stdout) or {}
        already = 'already running' in str(status.get('message') or '').lower()
        if code != 0:
            error_dialog(
                self.gui,
                'Wranglekit',
                'Could not start the background tag cache.',
                det_msg=(stderr or stdout or f'exit {code}').strip(),
                show=True,
            )
            return

        info_dialog(
            self.gui,
            'Wranglekit',
            format_warm_started_text(
                status,
                book_count=len(book_ids),
                name_count=len(names),
                already=already,
            ),
            show=True,
        )
        if status.get('running') or already:
            self.jobs().attach('warm')

    def stop_tag_cache_warm(self):
        if not question_dialog(
            self.gui,
            'Wranglekit',
            (
                'Stop the background tag-cache process?\n\n'
                'Already-fetched mappings stay in the cache. You can start '
                'it again later from Tags and collections → Warm tag cache.'
            ),
        ):
            return

        from calibre_plugins.wranglekit.enrich import EnrichCancelled, run_ao3kit
        from calibre_plugins.wranglekit.scrape_run import build_warm_stop_argv
        from calibre_plugins.wranglekit.tag_warm import (
            format_warm_stopped_dialog,
            parse_warm_status_json,
        )

        try:
            code, stdout, stderr = run_ao3kit(build_warm_stop_argv())
        except EnrichCancelled:
            return

        status = parse_warm_status_json(stdout) or {}
        summary, details = format_warm_stopped_dialog(status)
        if not summary:
            summary = (
                str(status.get('message') or '').strip()
                or (stderr or stdout or f'exit {code}').strip()
            )
        if code != 0:
            error_dialog(
                self.gui,
                'Wranglekit',
                'Could not stop the background tag cache.',
                det_msg=details or summary,
                show=True,
            )
            return
        if details:
            info_dialog(
                self.gui, 'Wranglekit', summary, det_msg=details, show=True
            )
        else:
            info_dialog(self.gui, 'Wranglekit', summary, show=True)

    def show_tag_cache_log(self):
        self.jobs().attach('warm')

    def show_tag_graph(self):
        db = self.gui.current_db
        from calibre_plugins.wranglekit.tag_purge import (
            graph_scope_ids,
            scope_book_ids,
            selected_ids_from_gui,
        )

        selected = []
        try:
            selected = selected_ids_from_gui(self.gui)
        except Exception:
            try:
                selected = list(self.gui.library_view.get_selected_ids())
            except Exception:
                selected = []
        book_ids, scope = graph_scope_ids(
            selected=selected,
            library_ids=scope_book_ids(db, ''),
        )
        noun = 'book' if len(book_ids) == 1 else 'books'
        if scope == 'selected':
            where = f'the {len(book_ids)} selected {noun}'
            empty_error = 'No tags or fandoms found on the selected books.'
        else:
            where = f'all {len(book_ids)} {noun} in this library'
            empty_error = 'No tags or fandoms found on books in this library.'
        if not question_dialog(
            self.gui,
            'Wranglekit',
            (
                f'Graph {where} as work nodes linked to all of their tags '
                '(plus synonym and metatag links), then open the live viewer?\n\n'
                'Find similar from a work or a tag searches AO3 and imports matches. '
                'Uncached tags show as missing — warm the tag cache first '
                'for a fuller graph.'
            ),
        ):
            return

        from PyQt5.Qt import QApplication, Qt

        from calibre_plugins.wranglekit.enrich import (
            EnrichCancelled,
            resolve_ao3kit_runtime,
            run_ao3kit,
        )
        from calibre_plugins.wranglekit.scrape_run import (
            build_tag_graph_argv,
            live_graph_reload_argv,
        )
        from calibre_plugins.wranglekit.selected import load_records_for_tag_warm
        from calibre_plugins.wranglekit.tag_warm import write_graph_jsonl

        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            project, _python, error = resolve_ao3kit_runtime()
            if error or project is None:
                error_dialog(
                    self.gui,
                    'Wranglekit',
                    'Could not find ao3kit. Install wranglekit.zip from GitHub '
                    'Releases, or set Project path in plugin settings.',
                    det_msg=error or '',
                    show=True,
                )
                return

            records = load_records_for_tag_warm(db, book_ids)
            if not records:
                error_dialog(
                    self.gui,
                    'Wranglekit',
                    empty_error,
                    show=True,
                )
                return

            jsonl = project / '.cache' / 'tag_graph_works.jsonl'
            output = project / '.cache' / 'tag-graph.html'
            write_graph_jsonl(jsonl, records)
            url = self.jobs().ensure_graph_server()
            live_code, live_out, live_err = (1, '', '')
            if url:
                live_code, live_out, live_err = run_ao3kit(live_graph_reload_argv())
                if live_code == 0:
                    try:
                        import json as json_mod

                        live_payload = json_mod.loads(
                            (live_out or '').strip().splitlines()[-1]
                        )
                        url = str(live_payload.get('url') or url)
                    except Exception:
                        pass
            if url:
                from PyQt5.Qt import QDesktopServices, QUrl
                from time import time

                open_url = url.rstrip('/') + f'/?t={int(time())}'
                QDesktopServices.openUrl(QUrl(open_url))
                info_dialog(
                    self.gui,
                    'Wranglekit',
                    'Opened the live tag graph.\n\n'
                    'Find similar on a work or tag to search AO3; new imports appear '
                    'in the graph as they land in the library. The viewer job '
                    'is listed under Running jobs…',
                    show=True,
                )
                return
            argv = build_tag_graph_argv(
                None, str(output), jsonl=str(jsonl), open_browser=False
            )
            code, stdout, stderr = run_ao3kit(argv)
        except EnrichCancelled:
            return
        finally:
            QApplication.restoreOverrideCursor()

        summary = (stdout or '').strip() or (stderr or '').strip()
        if code != 0:
            error_dialog(
                self.gui,
                'Wranglekit',
                'Could not build the tag graph.',
                det_msg=summary or f'exit {code}',
                show=True,
            )
            return
        if output.is_file():
            from PyQt5.Qt import QDesktopServices, QUrl

            QDesktopServices.openUrl(QUrl.fromLocalFile(str(output)))
        info_dialog(
            self.gui,
            'Wranglekit',
            summary or f'Wrote {output}',
            show=True,
        )

    def show_tag_mappings_dialog(self, *args):
        dialog = TagMappingsDialog(self.gui)
        dialog.exec_()
        pin_name = str(getattr(dialog, 'pin_collection', '') or '').strip()
        if getattr(dialog, 'edit_selection', False):
            self.edit_collections_of_selected()
        elif pin_name:
            self.add_selected_books_to_collection(
                collection_name=pin_name, confirm=False
            )
        elif getattr(dialog, 'apply_selection', False):
            self.recompute_collections_for_selected(confirm=False)

    def show_tag_purge_dialog(self, *args):
        book_ids = []
        try:
            from calibre_plugins.wranglekit.tag_purge import selected_ids_from_gui

            book_ids = selected_ids_from_gui(self.gui)
        except Exception:
            try:
                book_ids = list(self.gui.library_view.get_selected_ids())
            except Exception:
                book_ids = []
        dialog = TagPurgeDialog(self.gui, book_ids=book_ids)
        dialog.exec_()
