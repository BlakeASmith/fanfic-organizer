# -*- coding: utf-8 -*-

from __future__ import annotations

from PyQt5.Qt import QMenu

from calibre.gui2 import error_dialog, info_dialog, question_dialog
from calibre.gui2.actions import InterfaceAction

from calibre_plugins.ao3_scraper.dialogs import (
    ImportJsonlDialog,
    ScrapeSearchDialog,
    SimilarSearchDialog,
    TagMappingsDialog,
    TagPurgeDialog,
    WarmLogDialog,
)
from calibre_plugins.ao3_scraper.prefs import plugin_runtime_settings, prefs
from calibre_plugins.ao3_scraper.progress import (
    ApplyCollectionsDialog,
    DownloadSelectedDialog,
    FillSeriesDialog,
    ImportProgressDialog,
    ImportSeriesDialog,
    ScrapeImportDialog,
    SimplifySelectedDialog,
)
from calibre_plugins.ao3_scraper.scrape_run import merge_plugin_settings

try:
    load_translations()
except NameError:
    pass


class AO3ScraperPlugin(InterfaceAction):
    name = 'AO3 Scraper'
    action_spec = ('AO3 Scraper', None, 'Search AO3 and import into this library', None)

    def genesis(self):
        self.qaction.triggered.connect(self.show_scrape_dialog)
        self.menu = QMenu(self.gui)
        self.qaction.setMenu(self.menu)
        self.menu.aboutToShow.connect(self.build_menu)
        self._job_dialog = None
        self._warm_log_dialog = None

    def initialization_complete(self):
        # Do not create columns or write the open library on startup.
        return

    def build_menu(self):
        self.menu.clear()
        self.menu.addAction('Search AO3 and import...', self.show_scrape_dialog)
        self.menu.addAction('Search similar...', self.show_similar_dialog)
        self.menu.addAction('Import JSONL or zip...', self.show_import_dialog)
        self.menu.addAction(
            'Download EPUB for selected books...',
            self.download_selected_epubs,
        )
        self.menu.addAction(
            'Import rest of series for selected books...',
            self.import_series_for_selected,
        )
        self.menu.addAction(
            'Fill series for selected books...',
            self.fill_series_for_selected,
        )
        self.menu.addAction(
            'Simplify tags, fandoms & relationships for selected books...',
            self.simplify_selected_books,
        )
        self.menu.addAction(
            'Edit collections of selected books...',
            self.edit_collections_of_selected,
        )
        self.menu.addAction(
            'Recompute collections for selected books...',
            self.recompute_collections_for_selected,
        )
        self.menu.addAction(
            'Add selected books to a collection...',
            self.add_selected_books_to_collection,
        )
        self.menu.addAction(
            'Warm tag cache in background...',
            self.warm_tag_cache,
        )
        self.menu.addAction(
            'Background tag cache log...',
            self.show_tag_cache_log,
        )
        self.menu.addAction(
            'Stop background tag cache...',
            self.stop_tag_cache_warm,
        )
        self.menu.addAction('Tag graph...', self.show_tag_graph)
        self.menu.addAction(
            'Collections & tag rules...',
            self.show_tag_mappings_dialog,
        )
        self.menu.addAction('Tag purge...', self.show_tag_purge_dialog)
        self.menu.addAction('Plugin settings...', self.show_configuration)

    def apply_settings(self):
        return

    def show_configuration(self):
        self.interface_action_base_plugin.do_user_config(parent=self.gui)

    def _job_running(self) -> bool:
        return self._job_dialog is not None and self._job_dialog.isVisible()

    def _start_job_dialog(self, dialog) -> None:
        self._job_dialog = dialog
        dialog.finished.connect(self._clear_job_dialog)
        dialog.show()

    def _clear_job_dialog(self, *_args):
        self._job_dialog = None

    def show_import_dialog(self):
        if self._job_running():
            self._job_dialog.raise_()
            self._job_dialog.activateWindow()
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
        prefs['update_existing'] = values['update_existing']

        progress = ImportProgressDialog(
            self.gui,
            path=values['path'],
            simplify_tags=values['simplify_tags'],
            update_existing=values['update_existing'],
            include_series=bool(prefs.get('import_full_series', False)),
        )
        self._start_job_dialog(progress)

    def show_scrape_dialog(self):
        if self._job_running():
            self._job_dialog.raise_()
            self._job_dialog.activateWindow()
            return

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

        progress = ScrapeImportDialog(
            self.gui,
            options=merge_plugin_settings(values, plugin_runtime_settings()),
        )
        self._start_job_dialog(progress)

    def show_similar_dialog(self):
        if self._job_running():
            self._job_dialog.raise_()
            self._job_dialog.activateWindow()
            return

        book_ids = list(self.gui.library_view.get_selected_ids())
        if not book_ids:
            error_dialog(
                self.gui,
                'AO3 Scraper',
                'Select one or more books in the library first.',
                show=True,
            )
            return

        from calibre_plugins.ao3_scraper.selected import load_selected_similar_records
        from calibre_plugins.ao3_scraper.similar import facets_from_records

        ready, skipped = load_selected_similar_records(self.gui.current_db, book_ids)
        if not ready:
            extra = ''
            if skipped:
                extra = '\n\n' + '\n'.join(
                    f'{item.get("title")}: {item.get("reason")}' for item in skipped[:8]
                )
            error_dialog(
                self.gui,
                'AO3 Scraper',
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

        progress = ScrapeImportDialog(
            self.gui,
            options=merge_plugin_settings(values, plugin_runtime_settings()),
        )
        self._start_job_dialog(progress)

    def download_selected_epubs(self):
        if self._job_running():
            self._job_dialog.raise_()
            self._job_dialog.activateWindow()
            return

        book_ids = list(self.gui.library_view.get_selected_ids())
        if not book_ids:
            error_dialog(
                self.gui,
                'AO3 Scraper',
                'Select one or more books in the library first.',
                show=True,
            )
            return

        from calibre_plugins.ao3_scraper.epub_plan import REASON_HAS_EPUB, REASON_NO_AO3
        from calibre_plugins.ao3_scraper.selected import load_selected_for_epub_download

        ready, skipped = load_selected_for_epub_download(
            self.gui.current_db, book_ids
        )
        already = [item for item in skipped if item.get('reason') == REASON_HAS_EPUB]
        no_id = [item for item in skipped if item.get('reason') == REASON_NO_AO3]
        if not ready:
            if already and not no_id:
                info_dialog(
                    self.gui,
                    'AO3 Scraper',
                    'Selected books already have an EPUB. Nothing to download.',
                    show=True,
                )
                return
            error_dialog(
                self.gui,
                'AO3 Scraper',
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
            'AO3 Scraper',
            (
                f'Download the native AO3 EPUB for {len(ready)} selected {noun} '
                f'that do not already have one?{skip_note}\n\n'
                'Uses each book\'s AO3 URL / work id. Existing EPUB files are '
                'left unchanged.'
            ),
        ):
            return

        progress = DownloadSelectedDialog(self.gui, book_ids)
        self._start_job_dialog(progress)

    def import_series_for_selected(self):
        if self._job_running():
            self._job_dialog.raise_()
            self._job_dialog.activateWindow()
            return

        book_ids = list(self.gui.library_view.get_selected_ids())
        if not book_ids:
            error_dialog(
                self.gui,
                'AO3 Scraper',
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
            'AO3 Scraper',
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

        progress = ImportSeriesDialog(self.gui, book_ids)
        self._start_job_dialog(progress)

    def fill_series_for_selected(self):
        if self._job_running():
            self._job_dialog.raise_()
            self._job_dialog.activateWindow()
            return

        book_ids = list(self.gui.library_view.get_selected_ids())
        if not book_ids:
            error_dialog(
                self.gui,
                'AO3 Scraper',
                'Select one or more books in the library first.',
                show=True,
            )
            return

        noun = 'book' if len(book_ids) == 1 else 'books'
        if not question_dialog(
            self.gui,
            'AO3 Scraper',
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

        progress = FillSeriesDialog(self.gui, book_ids)
        self._start_job_dialog(progress)

    def simplify_selected_books(self):
        if self._job_running():
            self._job_dialog.raise_()
            self._job_dialog.activateWindow()
            return

        book_ids = list(self.gui.library_view.get_selected_ids())
        if not book_ids:
            error_dialog(
                self.gui,
                'AO3 Scraper',
                'Select one or more books in the library first.',
                show=True,
            )
            return

        if not question_dialog(
            self.gui,
            'AO3 Scraper',
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

        progress = SimplifySelectedDialog(self.gui, book_ids)
        self._start_job_dialog(progress)

    def recompute_collections_for_selected(self, *args, confirm=True):
        if self._job_running():
            self._job_dialog.raise_()
            self._job_dialog.activateWindow()
            return

        book_ids = list(self.gui.library_view.get_selected_ids())
        if not book_ids:
            error_dialog(
                self.gui,
                'AO3 Scraper',
                'Select one or more books in the library first.',
                show=True,
            )
            return

        noun = 'book' if len(book_ids) == 1 else 'books'
        if confirm and not question_dialog(
            self.gui,
            'AO3 Scraper',
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

        progress = ApplyCollectionsDialog(self.gui, book_ids)
        self._start_job_dialog(progress)

    def edit_collections_of_selected(self, *args):
        if self._job_running():
            self._job_dialog.raise_()
            self._job_dialog.activateWindow()
            return

        book_ids = list(self.gui.library_view.get_selected_ids())
        if not book_ids:
            error_dialog(
                self.gui,
                'AO3 Scraper',
                'Select one or more books in the library first.',
                show=True,
            )
            return

        from calibre_plugins.ao3_scraper.collection_edit import (
            EditSelectedCollectionsDialog,
        )

        dialog = EditSelectedCollectionsDialog(self.gui, book_ids)
        dialog.exec_()

    def apply_collection_rules_to_selected(self, *args, confirm=True):
        self.recompute_collections_for_selected(*args, confirm=confirm)

    def add_selected_books_to_collection(self, *args, collection_name='', confirm=True):
        if self._job_running():
            self._job_dialog.raise_()
            self._job_dialog.activateWindow()
            return

        book_ids = list(self.gui.library_view.get_selected_ids())
        if not book_ids:
            error_dialog(
                self.gui,
                'AO3 Scraper',
                'Select one or more books in the library first.',
                show=True,
            )
            return

        name = str(collection_name or '').strip()
        if not name:
            from calibre_plugins.ao3_scraper.collection_edit import (
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
                'AO3 Scraper',
                'Type a collection name first.',
                show=True,
            )
            return

        noun = 'book' if len(book_ids) == 1 else 'books'
        if confirm and not question_dialog(
            self.gui,
            'AO3 Scraper',
            (
                f'Add {len(book_ids)} selected {noun} to “{name}”?\n\n'
                'This saves a per-work rule, then updates Collections from '
                'your rules. Tags are left as they are (no AO3 tag lookup).'
            ),
        ):
            return

        from calibre_plugins.ao3_scraper.collection_rules import (
            build_collections_pin_argv,
        )
        from calibre_plugins.ao3_scraper.enrich import EnrichCancelled, run_ao3kit
        from calibre_plugins.ao3_scraper.selected import pin_targets_from_selected

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
                'AO3 Scraper',
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
                        'AO3 Scraper',
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
        from calibre_plugins.ao3_scraper.tag_purge import scope_book_ids

        book_ids = scope_book_ids(db, '')
        noun = 'book' if len(book_ids) == 1 else 'books'
        if not question_dialog(
            self.gui,
            'AO3 Scraper',
            (
                f'Collect tags from all {len(book_ids)} {noun} in the open '
                'library and fetch uncached AO3 mappings in the background?\n\n'
                'This does not change the library. It only fills ao3kit\'s '
                'tag cache so Simplify / import later skip the slow AO3 '
                'lookups.\n\n'
                'Pace is slow so Search and Download can still use AO3. '
                'Stop it from this menu when you want.'
            ),
        ):
            return

        from PyQt5.Qt import QApplication, Qt

        from calibre_plugins.ao3_scraper.enrich import (
            EnrichCancelled,
            resolve_ao3kit_runtime,
            run_ao3kit,
        )
        from calibre_plugins.ao3_scraper.scrape_run import (
            build_warm_start_argv,
            merge_plugin_settings,
        )
        from calibre_plugins.ao3_scraper.selected import load_records_for_tag_warm
        from calibre_plugins.ao3_scraper.tag_warm import (
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
                    'AO3 Scraper',
                    'Could not find the ao3kit checkout / Python.',
                    det_msg=error or '',
                    show=True,
                )
                return

            records = load_records_for_tag_warm(db, book_ids)
            names = unique_tag_names_from_records(records)
            if not names:
                error_dialog(
                    self.gui,
                    'AO3 Scraper',
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
                'AO3 Scraper',
                'Could not start the background tag cache.',
                det_msg=(stderr or stdout or f'exit {code}').strip(),
                show=True,
            )
            return

        info_dialog(
            self.gui,
            'AO3 Scraper',
            format_warm_started_text(
                status,
                book_count=len(book_ids),
                name_count=len(names),
                already=already,
            ),
            show=True,
        )

    def stop_tag_cache_warm(self):
        if not question_dialog(
            self.gui,
            'AO3 Scraper',
            (
                'Stop the background tag-cache process?\n\n'
                'Already-fetched mappings stay in the cache. You can start '
                'it again later from this menu.'
            ),
        ):
            return

        from calibre_plugins.ao3_scraper.enrich import EnrichCancelled, run_ao3kit
        from calibre_plugins.ao3_scraper.scrape_run import build_warm_stop_argv
        from calibre_plugins.ao3_scraper.tag_warm import (
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
                'AO3 Scraper',
                'Could not stop the background tag cache.',
                det_msg=details or summary,
                show=True,
            )
            return
        if details:
            info_dialog(
                self.gui, 'AO3 Scraper', summary, det_msg=details, show=True
            )
        else:
            info_dialog(self.gui, 'AO3 Scraper', summary, show=True)

    def show_tag_cache_log(self):
        existing = self._warm_log_dialog
        if existing is not None:
            try:
                if existing.isVisible():
                    existing.raise_()
                    existing.activateWindow()
                    return
            except RuntimeError:
                self._warm_log_dialog = None

        from calibre_plugins.ao3_scraper.enrich import resolve_ao3kit_runtime
        from calibre_plugins.ao3_scraper.tag_warm import (
            warm_log_path,
            warm_status_path,
        )

        project, _python, error = resolve_ao3kit_runtime()
        if error or project is None:
            error_dialog(
                self.gui,
                'AO3 Scraper',
                'Could not find the ao3kit checkout / Python.',
                det_msg=error or '',
                show=True,
            )
            return

        dialog = WarmLogDialog(
            self.gui,
            log_path=warm_log_path(project),
            status_path=warm_status_path(project),
        )
        dialog.finished.connect(lambda *_args: setattr(self, '_warm_log_dialog', None))
        self._warm_log_dialog = dialog
        dialog.show()

    def show_tag_graph(self):
        db = self.gui.current_db
        from calibre_plugins.ao3_scraper.tag_purge import (
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
            'AO3 Scraper',
            (
                f'Graph {where} as work nodes linked to all of their tags '
                '(plus synonym and metatag links), then open it in your '
                'browser?\n\n'
                'This does not change the library. Uncached tags show as '
                'missing — warm the tag cache first for a fuller graph.'
            ),
        ):
            return

        from PyQt5.Qt import QApplication, Qt

        from calibre_plugins.ao3_scraper.enrich import (
            EnrichCancelled,
            resolve_ao3kit_runtime,
            run_ao3kit,
        )
        from calibre_plugins.ao3_scraper.scrape_run import build_tag_graph_argv
        from calibre_plugins.ao3_scraper.selected import load_records_for_tag_warm
        from calibre_plugins.ao3_scraper.tag_warm import write_graph_jsonl

        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            project, _python, error = resolve_ao3kit_runtime()
            if error or project is None:
                error_dialog(
                    self.gui,
                    'AO3 Scraper',
                    'Could not find the ao3kit checkout / Python.',
                    det_msg=error or '',
                    show=True,
                )
                return

            records = load_records_for_tag_warm(db, book_ids)
            if not records:
                error_dialog(
                    self.gui,
                    'AO3 Scraper',
                    empty_error,
                    show=True,
                )
                return

            jsonl = project / '.cache' / 'tag_graph_works.jsonl'
            output = project / '.cache' / 'tag-graph.html'
            write_graph_jsonl(jsonl, records)
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
                'AO3 Scraper',
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
            'AO3 Scraper',
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
        if self._job_running():
            self._job_dialog.raise_()
            self._job_dialog.activateWindow()
            return

        book_ids = []
        try:
            from calibre_plugins.ao3_scraper.tag_purge import selected_ids_from_gui

            book_ids = selected_ids_from_gui(self.gui)
        except Exception:
            try:
                book_ids = list(self.gui.library_view.get_selected_ids())
            except Exception:
                book_ids = []
        dialog = TagPurgeDialog(self.gui, book_ids=book_ids)
        dialog.exec_()
