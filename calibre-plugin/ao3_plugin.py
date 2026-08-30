# -*- coding: utf-8 -*-

from __future__ import annotations

from PyQt5.Qt import QIcon, QMenu, QPixmap, QToolButton

from calibre.gui2 import error_dialog, info_dialog
from calibre.gui2.actions import InterfaceAction

from calibre_plugins.fanfic_organizer.dialogs import (
    ImportJsonlDialog,
    ProcessLibraryDialog,
    ScrapeSearchDialog,
    SimilarSearchDialog,
    TagMappingsDialog,
    TagPurgeDialog,
)
from calibre_plugins.fanfic_organizer.prefs import plugin_runtime_settings, prefs
from calibre_plugins.fanfic_organizer.scrape_run import merge_plugin_settings

try:
    load_translations()
except NameError:
    pass


PLUGIN_ICON = 'images/icon.png'
OPEN_IN_AO3_ICON = 'images/open-in-ao3.png'


def load_plugin_icon(action, resource: str = PLUGIN_ICON) -> QIcon:
    try:
        data = action.load_resources([resource]).get(resource)
    except Exception:
        data = None
    if not data:
        return QIcon()
    pixmap = QPixmap()
    if not pixmap.loadFromData(data):
        return QIcon()
    return QIcon(pixmap)


class FanficOrganizerPlugin(InterfaceAction):
    name = 'Fanfic Organizer'
    action_spec = (
        'Fanfic Organizer',
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
        self._koreader = None

    def koreader(self):
        if self._koreader is None:
            from calibre_plugins.fanfic_organizer.koreader_support import KoreaderSupport

            self._koreader = KoreaderSupport(self)
        return self._koreader

    def jobs(self):
        if self._jobs is None:
            from calibre_plugins.fanfic_organizer.job_supervise import JobSupervisor

            self._jobs = JobSupervisor(self)
        return self._jobs

    def initialization_complete(self):
        # Watch leftover jobs from the last session (pending Calibre ingest).
        # Do not create columns or write the open library on startup.
        self.jobs()
        self._apply_popup_mode()
        self._menu_for_context = False
        self._context_menu_hooks = False
        try:
            from PyQt5.Qt import QTimer
        except ImportError:
            from PyQt5.QtCore import QTimer
        QTimer.singleShot(0, self._ensure_context_menu_placement)

    def _ensure_context_menu_placement(self):
        """One-shot: append this plugin to library context-menu layouts."""
        if not prefs.get('context_menu_placed', False):
            try:
                from calibre.gui2 import gprefs
                from calibre_plugins.fanfic_organizer.context_menu import (
                    CONTEXT_MENU_LAYOUT_KEYS,
                    layouts_needing_plugin,
                )
            except Exception:
                prefs['context_menu_placed'] = True
                self._hook_context_menu_mode()
                return

            current = {key: gprefs.get(key) for key in CONTEXT_MENU_LAYOUT_KEYS}
            updates = layouts_needing_plugin(current, self.name)
            for key, layout in updates.items():
                gprefs[key] = tuple(layout)
            prefs['context_menu_placed'] = True
            if updates:
                rebuild = getattr(self.gui, 'build_context_menus', None)
                if callable(rebuild):
                    try:
                        rebuild()
                    except Exception:
                        pass
        self._hook_context_menu_mode()

    def _iter_book_context_menus(self):
        """Yield Calibre book-list / cover-browser context menus once each."""
        seen: set[int] = set()
        views = []
        library_view = getattr(self.gui, 'library_view', None)
        if library_view is not None:
            views.append(library_view)
            pin_view = getattr(library_view, 'pin_view', None)
            if pin_view is not None:
                views.append(pin_view)
        cover_flow = getattr(self.gui, 'cover_flow', None)
        if cover_flow is not None:
            views.append(cover_flow)
        for view in views:
            menu = getattr(view, 'context_menu', None)
            if menu is None:
                continue
            menu_id = id(menu)
            if menu_id in seen:
                continue
            seen.add(menu_id)
            yield menu

    def _hook_context_menu_mode(self):
        """Track library right-click so our submenu can stay selection-only."""
        if self._context_menu_hooks:
            return
        hooked = False
        for menu in self._iter_book_context_menus():
            menu.aboutToShow.connect(self._begin_context_menu_mode)
            menu.aboutToHide.connect(self._end_context_menu_mode)
            hooked = True
        self._context_menu_hooks = hooked

    def _begin_context_menu_mode(self):
        self._menu_for_context = True
        menu = self.sender()
        if menu is not None:
            self._place_open_in_ao3_on_context_menu(menu)

    def _end_context_menu_mode(self):
        self._menu_for_context = False

    def _open_in_ao3_context_action(self):
        action = getattr(self, '_open_in_ao3_action', None)
        if action is not None:
            return action
        try:
            from PyQt5.Qt import QAction
        except ImportError:
            from PyQt5.QtWidgets import QAction
        from calibre_plugins.fanfic_organizer.context_menu import OPEN_IN_AO3_LABEL

        action = QAction(OPEN_IN_AO3_LABEL, self.gui)
        icon = load_plugin_icon(self, OPEN_IN_AO3_ICON)
        action.setIcon(icon)
        try:
            action.setIconVisibleInMenu(True)
        except Exception:
            pass
        action.setStatusTip('Open the selected book(s) on archiveofourown.org')
        action.triggered.connect(self.open_selected_in_ao3)
        self._open_in_ao3_action = action
        return action

    def _place_open_in_ao3_on_context_menu(self, menu):
        """Insert Open in AO3 as a top-level item before Fanfic Organizer."""
        from calibre_plugins.fanfic_organizer.context_menu import OPEN_IN_AO3_LABEL

        action = self._open_in_ao3_context_action()
        for other in self._iter_book_context_menus():
            if action in other.actions():
                other.removeAction(action)
        plugin_action = None
        for existing in menu.actions():
            text = existing.text().replace('&', '')
            if text == self.name:
                plugin_action = existing
                break
        if plugin_action is not None:
            menu.insertAction(plugin_action, action)
        else:
            menu.addAction(action)
        action.setEnabled(bool(self._selected_ids()))
        action.setText(OPEN_IN_AO3_LABEL)

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
        # Library right-click uses the same QAction menu; show selection ops only.
        # One-shot clear: Linux clone_menu emits aboutToShow without aboutToHide.
        for_context = bool(getattr(self, '_menu_for_context', False))
        self._menu_for_context = False

        self.menu.clear()
        selected_ids = self._selected_ids()
        has_selection = len(selected_ids) > 0
        self._populate_selection_actions(has_selection, for_context=for_context)
        if not for_context:
            self._populate_global_actions()

    def _populate_selection_actions(self, has_selection: bool, *, for_context: bool = False):
        if not for_context:
            open_ao3 = self.menu.addAction(
                'Open in AO3', self.open_selected_in_ao3
            )
            open_ao3.setIcon(load_plugin_icon(self, OPEN_IN_AO3_ICON))
            try:
                open_ao3.setIconVisibleInMenu(True)
            except Exception:
                pass
            open_ao3.setEnabled(has_selection)
            open_ao3.setStatusTip(
                'Open the selected book(s) on archiveofourown.org'
            )
        complete = self.menu.addAction(
            'Complete selected', self.complete_selected_books
        )
        complete.setEnabled(has_selection)
        complete.setStatusTip(
            'Fill series, import missing parts, download EPUBs, and simplify tags'
        )
        fill = self.menu.addAction('Fill from AO3', self.fill_selected_from_ao3)
        fill.setEnabled(has_selection)
        fill.setStatusTip(
            'Identify from URL, EPUB, or title+author, then fill missing metadata'
        )
        for label, slot in (
            ('Download EPUB', self.download_selected_epubs),
            ('Generate covers', self.generate_covers_for_selected),
            ('Import rest of series', self.import_series_for_selected),
            ('Fill series', self.fill_series_for_selected),
        ):
            action = self.menu.addAction(label, slot)
            action.setEnabled(has_selection)
        self.menu.addSeparator()
        simplify = self.menu.addAction(
            'Simplify tags, fandoms & relationships',
            self.simplify_selected_books,
        )
        simplify.setEnabled(has_selection)
        self.menu.addSeparator()
        for label, slot in (
            ('Edit collections...', self.edit_collections_of_selected),
            ('Recompute collections', self.recompute_collections_for_selected),
            ('Add to a collection...', self.add_selected_books_to_collection),
        ):
            action = self.menu.addAction(label, slot)
            action.setEnabled(has_selection)
        similar = self.menu.addAction(
            'Search similar...', self.show_similar_dialog
        )
        similar.setEnabled(has_selection)
        similar.setStatusTip('Build an AO3 search from the selected books')

    def _populate_global_actions(self):
        self.menu.addSeparator()
        self.menu.addAction('Search AO3 and import...', self.show_scrape_dialog)
        self.menu.addAction(
            'Process library...', self.show_process_library_dialog
        )
        self.menu.addAction('Running jobs...', self.show_running_jobs)

        self.menu.addSeparator()
        tags = self.menu.addMenu('Tags and collections')
        tags.addAction(
            'Collections & tag rules...', self.show_tag_mappings_dialog
        )
        tags.addAction('Tag graph', self.show_tag_graph)
        tags.addAction('Tag purge...', self.show_tag_purge_dialog)
        tags.addSeparator()
        tags.addAction('Warm tag cache', self.warm_tag_cache)
        tags.addAction('Tag cache log...', self.show_tag_cache_log)
        tags.addAction('Stop tag cache', self.stop_tag_cache_warm)

        self.menu.addSeparator()
        more = self.menu.addMenu('Import')
        more.addAction('JSONL or zip...', self.show_import_dialog)

        self.menu.addSeparator()
        self.menu.addAction('Check for updates...', self.check_for_updates)
        deploy_koreader = self.menu.addAction(
            'Deploy to KOReader…', self.deploy_to_koreader
        )
        deploy_koreader.setEnabled(self.koreader().deploy_ready())
        deploy_koreader.setStatusTip(
            'Install or refresh the Fanfic collections KOReader plugin and '
            'fanfic.collections.json on a Kobo or Android device with KOReader'
        )
        self.menu.addAction('Plugin settings...', self.show_configuration)

    def show_running_jobs(self):
        self.jobs().show_list()

    def apply_settings(self):
        return

    def deploy_to_koreader(self):
        self.koreader().deploy(silent=False)

    def show_configuration(self):
        self.interface_action_base_plugin.do_user_config(parent=self.gui)

    def check_for_updates(self):
        from calibre_plugins.fanfic_organizer.update_ui import show_update_check

        show_update_check(self.gui)

    def show_import_dialog(self):
        dialog = ImportJsonlDialog(self.gui)
        if not dialog.exec_():
            return

        values = dialog.values()
        if not values['path']:
            error_dialog(self.gui, 'Fanfic Organizer', 'Choose a JSONL or import zip file.', show=True)
            return

        prefs['last_jsonl_path'] = values['path']
        prefs['simplify_tags'] = values['simplify_tags']
        prefs['drop_unmarked'] = values['drop_unmarked']
        prefs['update_existing'] = values['update_existing']

        from calibre_plugins.fanfic_organizer.job_plans import plan_import
        from calibre_plugins.fanfic_organizer.jsonl_loader import load_import_source

        try:
            records, bundle_root, cleanup = load_import_source(values['path'])
        except Exception as exc:
            error_dialog(
                self.gui,
                'Fanfic Organizer',
                'Could not read that import file.',
                det_msg=str(exc),
                show=True,
            )
            return
        if not records:
            error_dialog(
                self.gui, 'Fanfic Organizer', 'The import file contains no records.', show=True
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
                'drop_unmarked': values['drop_unmarked'],
                'update_existing': values['update_existing'],
                'include_series': bool(prefs.get('import_full_series', False)),
            },
            bundle_root=bundle_root,
            cleanup_dir=str(cleanup) if cleanup else None,
        )
        self.jobs().start_prepared(job_dir)

    def show_process_library_dialog(self):
        from pathlib import Path

        from PyQt5.Qt import QApplication, Qt

        from calibre_plugins.fanfic_organizer.job_plans import plan_library_job
        from calibre_plugins.fanfic_organizer.library_job import (
            estimate_library_job,
            format_library_estimate,
            load_request_interval,
            options_from_prefs,
            prefs_from_options,
        )
        from calibre_plugins.fanfic_organizer.selected import (
            copy_book_epub,
            export_selected_epubs_for_cover,
            library_job_ready_items,
            load_library_books,
        )
        from calibre_plugins.fanfic_organizer.tag_complete import tag_cache_path
        from calibre_plugins.fanfic_organizer.tag_purge import resolve_scope_ids
        from calibre_plugins.fanfic_organizer.user_dirs import config_dir

        db = self.gui.current_db
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            book_ids = resolve_scope_ids(db, '', use_virtual_library=True)
            books = load_library_books(db, book_ids)
        except Exception as exc:
            QApplication.restoreOverrideCursor()
            error_dialog(
                self.gui,
                'Fanfic Organizer',
                'Could not read this library.',
                det_msg=str(exc),
                show=True,
            )
            return
        QApplication.restoreOverrideCursor()

        if not books:
            error_dialog(
                self.gui,
                'Fanfic Organizer',
                'This library has no books to process.',
                show=True,
            )
            return

        cache = tag_cache_path()
        interval = load_request_interval(config_dir() / 'config.yaml')
        saved = options_from_prefs(prefs)

        def estimate_text(opts) -> str:
            estimate = estimate_library_job(
                books,
                opts,
                cache_path=cache,
                request_interval=interval,
            )
            return format_library_estimate(estimate, opts)

        dialog = ProcessLibraryDialog(
            self.gui,
            estimate_text=estimate_text(saved),
            options=saved,
        )
        dialog.set_estimate_callback(estimate_text)
        dialog.set_estimate_text(estimate_text(dialog.values()))
        if not dialog.exec_():
            return

        chosen = dialog.values()
        for key, value in prefs_from_options(chosen).items():
            prefs[key] = value
        if not chosen.any_selected():
            return

        ready, skipped = library_job_ready_items(books, chosen)
        if not ready:
            extra = ''
            if skipped:
                extra = '\n\n' + '\n'.join(
                    f'{item.get("title")}: {item.get("reason")}'
                    for item in skipped[:8]
                )
            error_dialog(
                self.gui,
                'Fanfic Organizer',
                'None of the books in this library can run those jobs.' + extra,
                show=True,
            )
            return

        job_dir = self.jobs().prepare_job_dir('library')
        if job_dir is None:
            return
        epub_dir = Path(job_dir) / 'work' / 'bundle' / 'epubs'
        epub_dir.mkdir(parents=True, exist_ok=True)
        if chosen.import_series and chosen.download_epubs:
            for item in ready:
                work_id = str((item.get('record') or {}).get('work_id') or '').strip()
                if work_id and item.get('has_epub'):
                    copy_book_epub(
                        db, item['book_id'], epub_dir / f'{work_id}.epub'
                    )
        if chosen.generate_covers:
            ready = export_selected_epubs_for_cover(db, ready, epub_dir)

        job_options = merge_plugin_settings(
            chosen.to_dict(), plugin_runtime_settings()
        )
        if chosen.download_epubs:
            job_options['cover'] = bool(chosen.cover_on_download)
        spec = plan_library_job(ready, skipped, job_dir, job_options)
        if not spec.get('steps'):
            import shutil

            shutil.rmtree(job_dir, ignore_errors=True)
            error_dialog(
                self.gui,
                'Process library',
                'Nothing to do with the current options for this library.',
                show=True,
            )
            return
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
        prefs['drop_unmarked'] = values['drop_unmarked']
        prefs['update_existing'] = values['update_existing']

        from calibre_plugins.fanfic_organizer.job_plans import plan_scrape

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
                'Fanfic Organizer',
                'Select one or more books in the library first.',
                show=True,
            )
            return

        from calibre_plugins.fanfic_organizer.selected import load_selected_similar_records
        from calibre_plugins.fanfic_organizer.similar import facets_from_records

        ready, skipped = load_selected_similar_records(self.gui.current_db, book_ids)
        if not ready:
            extra = ''
            if skipped:
                extra = '\n\n' + '\n'.join(
                    f'{item.get("title")}: {item.get("reason")}' for item in skipped[:8]
                )
            error_dialog(
                self.gui,
                'Fanfic Organizer',
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
        prefs['drop_unmarked'] = values['drop_unmarked']
        prefs['update_existing'] = values['update_existing']

        from calibre_plugins.fanfic_organizer.job_plans import plan_scrape

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
                'Fanfic Organizer',
                'Select one or more books in the library first.',
                show=True,
            )
            return

        from calibre_plugins.fanfic_organizer.epub_plan import REASON_HAS_EPUB, REASON_NO_AO3
        from calibre_plugins.fanfic_organizer.selected import load_selected_for_epub_download

        ready, skipped = load_selected_for_epub_download(
            self.gui.current_db, book_ids
        )
        already = [item for item in skipped if item.get('reason') == REASON_HAS_EPUB]
        no_id = [item for item in skipped if item.get('reason') == REASON_NO_AO3]
        if not ready:
            if already and not no_id:
                info_dialog(
                    self.gui,
                    'Fanfic Organizer',
                    'Selected books already have an EPUB. Nothing to download.',
                    show=True,
                )
                return
            error_dialog(
                self.gui,
                'Fanfic Organizer',
                'None of the selected books have an AO3 URL or work id to download.',
                show=True,
            )
            return

        from calibre_plugins.fanfic_organizer.job_plans import plan_download_selected

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
                'Fanfic Organizer',
                'Select one or more books in the library first.',
                show=True,
            )
            return

        from pathlib import Path

        from calibre_plugins.fanfic_organizer.cover_ui import load_cover_dict
        from calibre_plugins.fanfic_organizer.job_plans import plan_cover_selected
        from calibre_plugins.fanfic_organizer.selected import (
            export_selected_epubs_for_cover,
            load_selected_for_covers,
        )

        ready, skipped = load_selected_for_covers(self.gui.current_db, book_ids)
        if not ready:
            error_dialog(
                self.gui,
                'Fanfic Organizer',
                'None of the selected books have a title to put on a cover.',
                show=True,
            )
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

    def open_selected_in_ao3(self):
        book_ids = list(self.gui.library_view.get_selected_ids())
        if not book_ids:
            error_dialog(
                self.gui,
                'Fanfic Organizer',
                'Select one or more books in the library first.',
                show=True,
            )
            return

        from calibre_plugins.fanfic_organizer.cleaned import ao3_work_url_from_book_fields

        try:
            from PyQt5.Qt import QDesktopServices, QUrl
        except ImportError:
            from PyQt5.QtGui import QDesktopServices, QUrl

        db = self.gui.current_db
        urls: list[str] = []
        seen: set[str] = set()
        for book_id in book_ids:
            identifiers = {}
            comments = ''
            try:
                mi = db.get_metadata(book_id, index_is_id=True)
                identifiers = mi.get_identifiers() or {}
                comments = str(getattr(mi, 'comments', None) or '')
            except Exception:
                continue
            url = ao3_work_url_from_book_fields(identifiers, comments)
            if not url:
                continue
            key = url.rstrip('/')
            if key in seen:
                continue
            seen.add(key)
            urls.append(url)

        if not urls:
            error_dialog(
                self.gui,
                'Fanfic Organizer',
                'None of the selected books have an AO3 work id or URL.',
                show=True,
            )
            return

        for url in urls:
            QDesktopServices.openUrl(QUrl(url))

    def complete_selected_books(self):
        book_ids = list(self.gui.library_view.get_selected_ids())
        if not book_ids:
            error_dialog(
                self.gui,
                'Fanfic Organizer',
                'Select one or more books in the library first.',
                show=True,
            )
            return

        from pathlib import Path

        from calibre_plugins.fanfic_organizer.job_plans import plan_complete_selected
        from calibre_plugins.fanfic_organizer.selected import (
            copy_book_epub,
            load_selected_records,
        )

        ready, skipped = load_selected_records(self.gui.current_db, book_ids)
        if not ready:
            error_dialog(
                self.gui,
                'Fanfic Organizer',
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

    def fill_selected_from_ao3(self):
        book_ids = list(self.gui.library_view.get_selected_ids())
        if not book_ids:
            error_dialog(
                self.gui,
                'Fanfic Organizer',
                'Select one or more books in the library first.',
                show=True,
            )
            return

        from pathlib import Path

        from calibre_plugins.fanfic_organizer.job_plans import plan_identify_selected
        from calibre_plugins.fanfic_organizer.selected import (
            export_selected_epubs_for_cover,
            load_selected_for_identify,
        )

        ready, skipped = load_selected_for_identify(self.gui.current_db, book_ids)
        if not ready:
            extra = ''
            if skipped:
                extra = '\n\n' + '\n'.join(
                    f'{item.get("title")}: {item.get("reason")}' for item in skipped[:8]
                )
            error_dialog(
                self.gui,
                'Fanfic Organizer',
                'None of the selected books have an AO3 URL, EPUB, or title '
                'to identify from.' + extra,
                show=True,
            )
            return
        job_dir = self.jobs().prepare_job_dir('identify')
        if job_dir is None:
            return
        epub_dir = Path(job_dir) / 'work' / 'bundle' / 'epubs'
        ready = export_selected_epubs_for_cover(
            self.gui.current_db, ready, epub_dir
        )
        plan_identify_selected(
            ready,
            skipped,
            job_dir,
            merge_plugin_settings(
                {
                    'download_epubs': bool(prefs.get('download_epubs', True)),
                    'simplify_tags': bool(prefs.get('simplify_tags', False)),
                    'include_series': bool(prefs.get('import_full_series', False)),
                    'update_existing': True,
                },
                plugin_runtime_settings(),
            ),
        )
        self.jobs().start_prepared(job_dir)

    def import_series_for_selected(self):
        book_ids = list(self.gui.library_view.get_selected_ids())
        if not book_ids:
            error_dialog(
                self.gui,
                'Fanfic Organizer',
                'Select one or more books in the library first.',
                show=True,
            )
            return

        from calibre_plugins.fanfic_organizer.job_plans import plan_import_series
        from calibre_plugins.fanfic_organizer.selected import load_selected_records

        ready, skipped = load_selected_records(self.gui.current_db, book_ids)
        if not ready:
            error_dialog(
                self.gui,
                'Fanfic Organizer',
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
                'Fanfic Organizer',
                'Select one or more books in the library first.',
                show=True,
            )
            return

        from calibre_plugins.fanfic_organizer.job_plans import plan_fill_series
        from calibre_plugins.fanfic_organizer.selected import load_selected_records

        ready, skipped = load_selected_records(self.gui.current_db, book_ids)
        if not ready:
            error_dialog(
                self.gui,
                'Fanfic Organizer',
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
                'Fanfic Organizer',
                'Select one or more books in the library first.',
                show=True,
            )
            return

        from calibre_plugins.fanfic_organizer.job_plans import plan_simplify_selected
        from calibre_plugins.fanfic_organizer.selected import load_selected_records

        ready, skipped = load_selected_records(self.gui.current_db, book_ids)
        if not ready:
            error_dialog(
                self.gui,
                'Fanfic Organizer',
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

    def recompute_collections_for_selected(self, *args):
        book_ids = list(self.gui.library_view.get_selected_ids())
        if not book_ids:
            error_dialog(
                self.gui,
                'Fanfic Organizer',
                'Select one or more books in the library first.',
                show=True,
            )
            return

        from calibre_plugins.fanfic_organizer.job_plans import plan_simplify_selected
        from calibre_plugins.fanfic_organizer.selected import load_selected_for_collections

        ready, skipped = load_selected_for_collections(self.gui.current_db, book_ids)
        if not ready:
            error_dialog(
                self.gui,
                'Fanfic Organizer',
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
                'Fanfic Organizer',
                'Select one or more books in the library first.',
                show=True,
            )
            return

        from calibre_plugins.fanfic_organizer.collection_edit import (
            EditSelectedCollectionsDialog,
        )

        dialog = EditSelectedCollectionsDialog(self.gui, book_ids)
        dialog.exec_()

    def apply_collection_rules_to_selected(self, *args):
        self.recompute_collections_for_selected(*args)

    def add_selected_books_to_collection(self, *args, collection_name=''):
        book_ids = list(self.gui.library_view.get_selected_ids())
        if not book_ids:
            error_dialog(
                self.gui,
                'Fanfic Organizer',
                'Select one or more books in the library first.',
                show=True,
            )
            return

        name = str(collection_name or '').strip()
        if not name:
            from calibre_plugins.fanfic_organizer.collection_edit import (
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
                'Fanfic Organizer',
                'Type a collection name first.',
                show=True,
            )
            return

        from calibre_plugins.fanfic_organizer.collection_rules import (
            build_collections_pin_argv,
        )
        from calibre_plugins.fanfic_organizer.enrich import EnrichCancelled, run_ao3kit
        from calibre_plugins.fanfic_organizer.selected import pin_targets_from_selected

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
                'Fanfic Organizer',
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
                        'Fanfic Organizer',
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

        self.recompute_collections_for_selected()

    def warm_tag_cache(self):
        db = self.gui.current_db
        from calibre_plugins.fanfic_organizer.tag_purge import scope_book_ids

        book_ids = scope_book_ids(db, '')
        from PyQt5.Qt import QApplication, Qt

        from calibre_plugins.fanfic_organizer.enrich import (
            EnrichCancelled,
            resolve_ao3kit_runtime,
            run_ao3kit,
        )
        from calibre_plugins.fanfic_organizer.scrape_run import (
            build_warm_start_argv,
            merge_plugin_settings,
        )
        from calibre_plugins.fanfic_organizer.selected import load_records_for_tag_warm
        from calibre_plugins.fanfic_organizer.tag_warm import (
            parse_warm_status_json,
            unique_tag_names_from_records,
            write_names_file,
            warm_names_path,
        )

        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            project, _python, error = resolve_ao3kit_runtime()
            if error or project is None:
                error_dialog(
                    self.gui,
                    'Fanfic Organizer',
                    'Could not find ao3kit. Install fanfic-organizer.zip from GitHub '
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
                    'Fanfic Organizer',
                    'No tags or fandoms found on books in this library.',
                    show=True,
                )
                return

            seed = warm_names_path(project)
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
                'Fanfic Organizer',
                'Could not start the background tag cache.',
                det_msg=(stderr or stdout or f'exit {code}').strip(),
                show=True,
            )
            return

        if status.get('running') or already:
            self.jobs().attach('warm')

    def stop_tag_cache_warm(self):
        from calibre_plugins.fanfic_organizer.enrich import EnrichCancelled, run_ao3kit
        from calibre_plugins.fanfic_organizer.scrape_run import build_warm_stop_argv
        from calibre_plugins.fanfic_organizer.tag_warm import (
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
                'Fanfic Organizer',
                'Could not stop the background tag cache.',
                det_msg=details or summary,
                show=True,
            )
            return
        if details:
            info_dialog(
                self.gui, 'Fanfic Organizer', summary, det_msg=details, show=True
            )
        else:
            info_dialog(self.gui, 'Fanfic Organizer', summary, show=True)

    def show_tag_cache_log(self):
        self.jobs().attach('warm')

    def show_tag_graph(self):
        db = self.gui.current_db
        from calibre_plugins.fanfic_organizer.tag_purge import (
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
        if scope == 'selected':
            empty_error = 'No tags or fandoms found on the selected books.'
        else:
            empty_error = 'No tags or fandoms found on books in this library.'
        from PyQt5.Qt import QApplication, Qt

        from calibre_plugins.fanfic_organizer.enrich import (
            EnrichCancelled,
            resolve_ao3kit_runtime,
            run_ao3kit,
        )
        from calibre_plugins.fanfic_organizer.scrape_run import (
            build_tag_graph_argv,
            live_graph_reload_argv,
        )
        from calibre_plugins.fanfic_organizer.selected import load_records_for_tag_warm
        from calibre_plugins.fanfic_organizer.graph_live import (
            graph_html_path,
            graph_jsonl_path,
        )
        from calibre_plugins.fanfic_organizer.tag_warm import write_graph_jsonl

        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            project, _python, error = resolve_ao3kit_runtime()
            if error or project is None:
                error_dialog(
                    self.gui,
                    'Fanfic Organizer',
                    'Could not find ao3kit. Install fanfic-organizer.zip from GitHub '
                    'Releases, or set Project path in plugin settings.',
                    det_msg=error or '',
                    show=True,
                )
                return

            records = load_records_for_tag_warm(db, book_ids)
            if not records:
                error_dialog(
                    self.gui,
                    'Fanfic Organizer',
                    empty_error,
                    show=True,
                )
                return

            jsonl = graph_jsonl_path(project)
            output = graph_html_path(project)
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
                'Fanfic Organizer',
                'Could not build the tag graph.',
                det_msg=summary or f'exit {code}',
                show=True,
            )
            return
        if output.is_file():
            from PyQt5.Qt import QDesktopServices, QUrl

            QDesktopServices.openUrl(QUrl.fromLocalFile(str(output)))

    def show_tag_mappings_dialog(self, *args):
        dialog = TagMappingsDialog(self.gui)
        dialog.exec_()
        pin_name = str(getattr(dialog, 'pin_collection', '') or '').strip()
        if getattr(dialog, 'edit_selection', False):
            self.edit_collections_of_selected()
        elif pin_name:
            self.add_selected_books_to_collection(
                collection_name=pin_name
            )
        elif getattr(dialog, 'apply_selection', False):
            self.recompute_collections_for_selected()

    def show_tag_purge_dialog(self, *args):
        book_ids = []
        try:
            from calibre_plugins.fanfic_organizer.tag_purge import selected_ids_from_gui

            book_ids = selected_ids_from_gui(self.gui)
        except Exception:
            try:
                book_ids = list(self.gui.library_view.get_selected_ids())
            except Exception:
                book_ids = []
        dialog = TagPurgeDialog(self.gui, book_ids=book_ids)
        dialog.exec_()
