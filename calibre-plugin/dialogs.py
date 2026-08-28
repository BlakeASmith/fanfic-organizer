# -*- coding: utf-8 -*-

from __future__ import annotations

import json

from PyQt5.Qt import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    Qt,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)
from PyQt5.QtWidgets import QFileDialog, QInputDialog, QTabWidget
from PyQt5.QtCore import QTimer

from calibre.gui2 import error_dialog, info_dialog, question_dialog

from calibre_plugins.fanfic_organizer.prefs import prefs
from calibre_plugins.fanfic_organizer.scrape_run import (
    SORT_OPTIONS,
    ids_to_csv,
    scrape_search_is_usable,
)
from calibre_plugins.fanfic_organizer.tag_complete import (
    attach_collection_match_completer,
    attach_tag_completer,
    combined_tag_extras,
)


def _set_combo_data(combo: QComboBox, value) -> None:
    idx = combo.findData(value)
    if idx >= 0:
        combo.setCurrentIndex(idx)


class ImportJsonlDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle('Import AO3 JSONL or zip')
        self.setMinimumWidth(520)

        layout = QVBoxLayout()
        self.setLayout(layout)

        intro = QLabel(
            'Import a JSONL scrape or an ao3-import.zip into the '
            '<b>currently open</b> Calibre library. To keep an existing '
            'collection unchanged, switch Calibre to a new empty library first.\n\n'
            'Creates Fandom / Relationships / Collections / Original Tags / '
            'word count columns if they are missing. Cleaned tags go in '
            'Calibre\'s Tags field; Original Tags keeps the pre-clean list.'
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        path_row = QHBoxLayout()
        self.path = QLineEdit()
        self.path.setPlaceholderText('/path/to/results.jsonl or import.zip')
        if prefs['last_jsonl_path']:
            self.path.setText(prefs['last_jsonl_path'])
        browse = QPushButton('Browse...')
        browse.clicked.connect(self.browse)
        path_row.addWidget(self.path)
        path_row.addWidget(browse)
        layout.addLayout(path_row)

        self.update_existing = QCheckBox(
            'Update existing books matched by AO3 work id or URL'
        )
        self.update_existing.setChecked(bool(prefs.get('update_existing', True)))
        layout.addWidget(self.update_existing)

        self.simplify_tags = QCheckBox(
            'Simplify tags, fandoms & relationships (AO3 canonical + user rules)'
        )
        self.simplify_tags.setChecked(bool(prefs.get('simplify_tags', False)))
        self.simplify_tags.setToolTip(
            'Runs tag simplification using the bundled toolkit. '
            'Collapses AO3 synonyms on Tags, Fandom, and Relationships. '
            'Needs network access for uncached tags. '
            'Collection and tag rules: Tags and collections → '
            'Collections & tag rules.'
        )
        layout.addWidget(self.simplify_tags)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def browse(self):
        start = self.path.text().strip() or prefs['last_jsonl_path']
        path, _ = QFileDialog.getOpenFileName(
            self,
            'Select AO3 import file',
            start,
            'AO3 import (*.jsonl *.zip);;JSON Lines (*.jsonl);;Zip archives (*.zip);;All files (*)',
        )
        if path:
            self.path.setText(path)

    def values(self) -> dict:
        return {
            'path': self.path.text().strip(),
            'update_existing': self.update_existing.isChecked(),
            'simplify_tags': self.simplify_tags.isChecked(),
        }


class ProcessLibraryDialog(QDialog):
    """Choose whole-library jobs without selecting every book."""

    def __init__(self, parent, *, estimate_text: str, options=None):
        super().__init__(parent)
        self.setWindowTitle('Process library')
        self.setMinimumWidth(520)
        self.resize(560, 640)

        from calibre_plugins.fanfic_organizer.library_job import (
            LibraryJobOptions,
        )

        self._options_cls = LibraryJobOptions
        options = options or LibraryJobOptions()

        layout = QVBoxLayout(self)
        intro = QLabel(
            'Apply Fanfic Organizer jobs to <b>every book in the currently '
            'open library</b> (including the virtual library, if any). You do '
            'not need to select all rows — that can freeze a large library.\n\n'
            'Pick what to run, then start one background job. The estimate '
            'uses the library and the local tag cache only; it does not load '
            'AO3 URLs.'
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        tasks = QGroupBox('Work to do')
        tasks_layout = QVBoxLayout(tasks)
        self.simplify_tags = QCheckBox(
            'Simplify tags, fandoms & relationships'
        )
        self.simplify_tags.setChecked(bool(options.simplify_tags))
        self.simplify_tags.setToolTip(
            'AO3 synonym collapse plus your tag rules. Uncached names are '
            'fetched from AO3; names already in the tag cache stay local.'
        )
        tasks_layout.addWidget(self.simplify_tags)

        self.fill_series = QCheckBox('Fill Series on books already in the library')
        self.fill_series.setChecked(bool(options.fill_series))
        self.fill_series.setToolTip(
            'Look up AO3 series membership for books that are missing a '
            'series id, name, or part number. Does not import extra works.'
        )
        tasks_layout.addWidget(self.fill_series)

        self.import_series = QCheckBox('Import the rest of each series')
        self.import_series.setChecked(bool(options.import_series))
        self.import_series.setToolTip(
            'Fetch every other work on the same AO3 series and add missing '
            'parts to this library. Search filters do not apply. Can add '
            'many books.'
        )
        tasks_layout.addWidget(self.import_series)

        self.download_epubs = QCheckBox('Download missing native EPUBs')
        self.download_epubs.setChecked(bool(options.download_epubs))
        self.download_epubs.setToolTip(
            'Download AO3 EPUBs for books that have a work id and no EPUB. '
            'Existing files are never replaced.'
        )
        tasks_layout.addWidget(self.download_epubs)

        self.generate_covers = QCheckBox('Generate covers')
        self.generate_covers.setChecked(bool(options.generate_covers))
        self.generate_covers.setToolTip(
            'Stamp a generated cover onto existing EPUBs (and Calibre '
            'thumbnails). Local; no AO3.'
        )
        tasks_layout.addWidget(self.generate_covers)

        self.recompute_collections = QCheckBox('Recompute collections from rules')
        self.recompute_collections.setChecked(bool(options.recompute_collections))
        self.recompute_collections.setToolTip(
            'Apply collection rules to every book. Included automatically '
            'when simplify is checked. Local; no AO3.'
        )
        tasks_layout.addWidget(self.recompute_collections)
        layout.addWidget(tasks)

        settings = QGroupBox('This job')
        settings_layout = QVBoxLayout(settings)
        self.update_existing = QCheckBox(
            'Update existing books matched by AO3 work id or URL'
        )
        self.update_existing.setChecked(bool(options.update_existing))
        settings_layout.addWidget(self.update_existing)
        self.cover_on_download = QCheckBox(
            'Generate covers on newly downloaded EPUBs'
        )
        self.cover_on_download.setChecked(bool(options.cover_on_download))
        settings_layout.addWidget(self.cover_on_download)
        layout.addWidget(settings)

        estimate_box = QGroupBox('Estimate')
        estimate_layout = QVBoxLayout(estimate_box)
        self.estimate = QPlainTextEdit()
        self.estimate.setReadOnly(True)
        self.estimate.setPlainText(estimate_text)
        try:
            self.estimate.setMinimumHeight(180)
        except Exception:
            pass
        estimate_layout.addWidget(self.estimate)
        layout.addWidget(estimate_box)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.start_btn = buttons.button(QDialogButtonBox.Ok)
        self.start_btn.setText('Start job')
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        for widget in (
            self.simplify_tags,
            self.fill_series,
            self.import_series,
            self.download_epubs,
            self.generate_covers,
            self.recompute_collections,
        ):
            widget.toggled.connect(self._sync_dependent)
        self._sync_dependent()
        self._estimate_callback = None

    def set_estimate_text(self, text: str) -> None:
        self.estimate.setPlainText(text)

    def set_estimate_callback(self, callback) -> None:
        self._estimate_callback = callback
        for widget in (
            self.simplify_tags,
            self.fill_series,
            self.import_series,
            self.download_epubs,
            self.generate_covers,
            self.recompute_collections,
        ):
            widget.toggled.connect(self._refresh_estimate)

    def _refresh_estimate(self, *_args) -> None:
        if self._estimate_callback is None:
            return
        text = self._estimate_callback(self.values())
        if text:
            self.set_estimate_text(text)

    def _sync_dependent(self, *_args) -> None:
        import_on = self.import_series.isChecked()
        if import_on:
            self.fill_series.setChecked(True)
        self.fill_series.setEnabled(not import_on)
        simplify_on = self.simplify_tags.isChecked()
        self.recompute_collections.setEnabled(not simplify_on)
        if simplify_on:
            self.recompute_collections.setChecked(True)
        self.cover_on_download.setEnabled(self.download_epubs.isChecked())
        self.update_existing.setEnabled(self.import_series.isChecked())
        self.start_btn.setEnabled(self.values().any_selected())

    def values(self):
        return self._options_cls(
            simplify_tags=self.simplify_tags.isChecked(),
            fill_series=self.fill_series.isChecked()
            or self.import_series.isChecked(),
            import_series=self.import_series.isChecked(),
            download_epubs=self.download_epubs.isChecked(),
            generate_covers=self.generate_covers.isChecked(),
            recompute_collections=self.recompute_collections.isChecked()
            or self.simplify_tags.isChecked(),
            cover_on_download=self.cover_on_download.isChecked(),
            update_existing=self.update_existing.isChecked(),
        )


def _form_line(placeholder: str = '', text: str = '') -> QLineEdit:
    widget = QLineEdit()
    if placeholder:
        widget.setPlaceholderText(placeholder)
    if text:
        widget.setText(text)
    return widget


class ScrapeSearchDialog(QDialog):
    """Search AO3 (same criteria as CLI/web) and import into this library."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle('Search AO3 and import')
        self.setMinimumWidth(560)
        self.resize(620, 740)
        self._use_form_criteria = False
        self._filling = False
        self._list_path = ''

        outer = QVBoxLayout()
        self.setLayout(outer)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        try:
            from PyQt5.Qt import QFrame

            scroll.setFrameShape(QFrame.NoFrame)
        except Exception:
            pass
        body = QWidget()
        layout = QVBoxLayout(body)
        scroll.setWidget(body)
        outer.addWidget(scroll)

        intro = QLabel(
            'Search AO3 like the CLI, then import matches into the '
            '<b>currently open</b> Calibre library. Uses the toolkit bundled '
            'in this plugin so host-wide rate limiting still applies.\n\n'
            'AO3 login is in Plugin settings. Paste a search, collection, '
            'user works, or series URL, or fill the form. Click Fill from URL '
            'to preview and edit criteria. A collection home URL uses the full '
            'works listing. Switch to a new empty library first if you do not '
            'want to write an existing collection.'
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        url_row = QHBoxLayout()
        self.url = _form_line(
            'https://archiveofourown.org/works?…, /collections/…, /users/…/works, or /series/…',
            prefs.get('last_scrape_url') or '',
        )
        self.url.textChanged.connect(self._on_url_changed)
        fill = QPushButton('Fill from URL')
        fill.setToolTip(
            'Parse the AO3 URL into the form fields (no network scrape).'
        )
        fill.clicked.connect(self.fill_from_url)
        url_row.addWidget(self.url)
        url_row.addWidget(fill)
        layout.addLayout(url_row)

        criteria_box = QGroupBox('Search criteria')
        criteria_form = QFormLayout(criteria_box)
        self.tag_id = _form_line(
            'Harry Potter - J. K. Rowling',
            prefs.get('last_tag_id') or '',
        )
        self.query = _form_line('amy/rory', prefs.get('last_query') or '')
        self.sort_column = QComboBox()
        for value, label in SORT_OPTIONS:
            self.sort_column.addItem(label, value)
        _set_combo_data(self.sort_column, 'kudos_count')
        self.complete = QComboBox()
        self.complete.addItem('Any', '')
        self.complete.addItem('Complete only', 'true')
        self.complete.addItem('In progress only', 'false')
        self.language_id = _form_line('en', 'en')
        self.words_from = _form_line('min words')
        self.words_to = _form_line('max words')
        self.date_from = _form_line('YYYY-MM-DD')
        self.date_to = _form_line('YYYY-MM-DD')
        self.other_tag_names = _form_line('comma-separated tag names')
        self.excluded_tag_names = _form_line('comma-separated tag names')
        extras = combined_tag_extras(parent)
        attach_tag_completer(self.tag_id, extra=extras)
        attach_tag_completer(self.other_tag_names, extra=extras, csv=True)
        attach_tag_completer(self.excluded_tag_names, extra=extras, csv=True)
        criteria_form.addRow('Fandom / tag', self.tag_id)
        criteria_form.addRow('Search query', self.query)
        criteria_form.addRow('Sort by', self.sort_column)
        criteria_form.addRow('Complete works (AO3)', self.complete)
        criteria_form.addRow('Language', self.language_id)
        criteria_form.addRow('Words from', self.words_from)
        criteria_form.addRow('Words to', self.words_to)
        criteria_form.addRow('Date from', self.date_from)
        criteria_form.addRow('Date to', self.date_to)
        criteria_form.addRow('Other tags', self.other_tag_names)
        criteria_form.addRow('Excluded tags', self.excluded_tag_names)
        layout.addWidget(criteria_box)

        advanced = QGroupBox('Advanced tag IDs (optional)')
        advanced_form = QFormLayout(advanced)
        self.relationship_ids = _form_line('1110, 2220')
        self.freeform_ids = _form_line('27594097')
        self.character_ids = _form_line()
        advanced_form.addRow('Relationship IDs', self.relationship_ids)
        advanced_form.addRow('Freeform IDs', self.freeform_ids)
        advanced_form.addRow('Character IDs', self.character_ids)
        layout.addWidget(advanced)

        filters = QGroupBox('Result filters')
        filters_form = QFormLayout(filters)
        self.max_results = _form_line(
            'no limit',
            str(prefs.get('last_max_results') or '25'),
        )
        self.start_page = _form_line('1', '1')
        self.min_score = _form_line('none')
        self.min_kudos = _form_line()
        self.min_words = _form_line()
        self.complete_only = QCheckBox(
            'Only include works with all planned chapters posted (7/7)'
        )
        filters_form.addRow('Max results', self.max_results)
        filters_form.addRow('Start page', self.start_page)
        filters_form.addRow('Min quality score', self.min_score)
        filters_form.addRow('Min kudos', self.min_kudos)
        filters_form.addRow('Min words', self.min_words)
        filters_form.addRow(self.complete_only)
        layout.addWidget(filters)

        import_box = QGroupBox('Import')
        import_layout = QVBoxLayout(import_box)
        self.download_epubs = QCheckBox('Download native EPUBs into this library')
        self.download_epubs.setChecked(bool(prefs.get('download_epubs', True)))
        self.download_epubs.setToolTip(
            'Search and download native EPUBs in one ao3kit scrape run. Uncheck to '
            'import metadata only (no book files). Default is set in plugin settings.'
        )
        self.update_existing = QCheckBox(
            'Update existing books matched by AO3 work id or URL'
        )
        self.update_existing.setChecked(bool(prefs.get('update_existing', True)))
        self.simplify_tags = QCheckBox(
            'Simplify tags, fandoms & relationships (AO3 canonical + user rules)'
        )
        self.simplify_tags.setChecked(bool(prefs.get('simplify_tags', False)))
        self.simplify_tags.setToolTip(
            'Runs `python -m ao3kit tags enrich` after the search. Default is '
            'set in plugin settings. Collapses AO3 synonyms on Tags, Fandom, '
            'and Relationships. Collection and tag rules: '
            'Tags and collections → Collections & tag rules.'
        )
        import_layout.addWidget(self.download_epubs)
        import_layout.addWidget(self.update_existing)
        import_layout.addWidget(self.simplify_tags)
        layout.addWidget(import_box)

        for widget in (
            self.tag_id,
            self.query,
            self.language_id,
            self.words_from,
            self.words_to,
            self.date_from,
            self.date_to,
            self.other_tag_names,
            self.excluded_tag_names,
            self.relationship_ids,
            self.freeform_ids,
            self.character_ids,
            self.start_page,
        ):
            widget.textChanged.connect(self._on_criteria_edited)
        self.sort_column.currentIndexChanged.connect(self._on_criteria_edited)
        self.complete.currentIndexChanged.connect(self._on_criteria_edited)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        ok_btn = buttons.button(QDialogButtonBox.Ok)
        if ok_btn is not None:
            ok_btn.setText('Search and import')
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)

    def _on_url_changed(self, _text: str = '') -> None:
        if self._filling:
            return
        self._use_form_criteria = False
        self._list_path = ''

    def _on_criteria_edited(self, *_args) -> None:
        if self._filling:
            return
        self._use_form_criteria = True

    def fill_from_url(self) -> None:
        url = self.url.text().strip()
        if not url:
            error_dialog(
                self,
                'Fanfic Organizer',
                'Paste an AO3 works-search or series URL first.',
                show=True,
            )
            return

        from calibre_plugins.fanfic_organizer.enrich import EnrichCancelled, run_ao3kit
        from calibre_plugins.fanfic_organizer.scrape_run import build_parse_url_argv

        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            code, stdout, stderr = run_ao3kit(build_parse_url_argv(url))
        except EnrichCancelled:
            return
        finally:
            QApplication.restoreOverrideCursor()

        if code != 0:
            error_dialog(
                self,
                'Fanfic Organizer',
                'Could not parse that search URL with ao3kit.',
                det_msg=(stderr or stdout or f'exit {code}').strip(),
                show=True,
            )
            return

        try:
            payload = json.loads(stdout)
        except ValueError as exc:
            error_dialog(
                self,
                'Fanfic Organizer',
                'ao3kit returned invalid parse-url JSON.',
                det_msg=f'{exc}\n{stdout}',
                show=True,
            )
            return

        self._apply_parse_payload(payload)

    def _apply_parse_payload(self, payload: dict) -> None:
        if payload.get('kind') == 'series':
            series_id = payload.get('series_id') or ''
            self._filling = True
            try:
                self.start_page.setText(str(payload.get('start_page') or 1))
            finally:
                self._filling = False
            self._list_path = ''
            self._use_form_criteria = False
            return
        if payload.get('kind') == 'bookmarks':
            self._filling = True
            try:
                self.start_page.setText(str(payload.get('start_page') or 1))
            finally:
                self._filling = False
            self._list_path = str(payload.get('list_path') or '')
            self._use_form_criteria = False
            return
        criteria = payload.get('criteria') or {}
        self._filling = True
        try:
            self.tag_id.setText(str(criteria.get('tag_id') or ''))
            self.query.setText(str(criteria.get('query') or ''))
            _set_combo_data(
                self.sort_column,
                criteria.get('sort_column') or 'kudos_count',
            )
            complete = criteria.get('complete')
            if complete is True:
                _set_combo_data(self.complete, 'true')
            elif complete is False:
                _set_combo_data(self.complete, 'false')
            else:
                _set_combo_data(self.complete, '')
            self.language_id.setText(str(criteria.get('language_id') or ''))
            self.words_from.setText(
                '' if criteria.get('words_from') is None else str(criteria['words_from'])
            )
            self.words_to.setText(
                '' if criteria.get('words_to') is None else str(criteria['words_to'])
            )
            self.date_from.setText(str(criteria.get('date_from') or ''))
            self.date_to.setText(str(criteria.get('date_to') or ''))
            self.other_tag_names.setText(str(criteria.get('other_tag_names') or ''))
            self.excluded_tag_names.setText(str(criteria.get('excluded_tag_names') or ''))
            self.relationship_ids.setText(ids_to_csv(criteria.get('relationship_ids')))
            self.freeform_ids.setText(ids_to_csv(criteria.get('freeform_ids')))
            self.character_ids.setText(ids_to_csv(criteria.get('character_ids')))
            self.start_page.setText(str(payload.get('start_page') or 1))
        finally:
            self._filling = False
        self._list_path = str(payload.get('list_path') or '')
        # Keep using the original URL until the user edits a criteria field,
        # matching scrape --parse-only URL fill.
        self._use_form_criteria = False

    def accept(self) -> None:
        values = self.values()
        if not scrape_search_is_usable(values):
            error_dialog(
                self,
                'Fanfic Organizer',
                'Paste an AO3 search, collection, user works, or series URL, '
                'or enter a fandom/tag or query.',
                show=True,
            )
            return
        super().accept()

    def values(self) -> dict:
        return {
            'url': self.url.text().strip(),
            'list_path': self._list_path,
            'use_form_criteria': self._use_form_criteria,
            'tag_id': self.tag_id.text().strip(),
            'query': self.query.text().strip(),
            'sort_column': self.sort_column.currentData(),
            'complete': self.complete.currentData(),
            'language_id': self.language_id.text().strip(),
            'words_from': self.words_from.text().strip(),
            'words_to': self.words_to.text().strip(),
            'date_from': self.date_from.text().strip(),
            'date_to': self.date_to.text().strip(),
            'other_tag_names': self.other_tag_names.text().strip(),
            'excluded_tag_names': self.excluded_tag_names.text().strip(),
            'relationship_ids': self.relationship_ids.text().strip(),
            'freeform_ids': self.freeform_ids.text().strip(),
            'character_ids': self.character_ids.text().strip(),
            'max_results': self.max_results.text().strip(),
            'start_page': self.start_page.text().strip(),
            'min_score': self.min_score.text().strip(),
            'min_kudos': self.min_kudos.text().strip(),
            'min_words': self.min_words.text().strip(),
            'complete_only': self.complete_only.isChecked(),
            'download_epubs': self.download_epubs.isChecked(),
            'update_existing': self.update_existing.isChecked(),
            'simplify_tags': self.simplify_tags.isChecked(),
        }


def _checked_flag():
    try:
        return Qt.ItemIsUserCheckable
    except AttributeError:
        return Qt.ItemFlag.ItemIsUserCheckable


def _checked_state():
    try:
        return Qt.Checked
    except AttributeError:
        return Qt.CheckState.Checked


def _unchecked_state():
    try:
        return Qt.Unchecked
    except AttributeError:
        return Qt.CheckState.Unchecked


class TagPurgeDialog(QDialog):
    """Preview rare Tags-column values and remove the checked set."""

    def __init__(self, gui, parent=None, book_ids=None):
        super().__init__(parent or gui)
        self.gui = gui
        from calibre_plugins.fanfic_organizer.tag_purge import (
            initial_scope_ids,
            library_book_count,
            selected_ids_from_gui,
            shown_ids_from_gui,
        )

        passed = [int(book_id) for book_id in (book_ids or [])]
        selected = passed or selected_ids_from_gui(gui)
        shown = shown_ids_from_gui(gui)
        self._scope_ids, self._scope_kind = initial_scope_ids(
            selected=selected,
            shown=shown,
            library_count=library_book_count(gui),
        )
        self._library_snapshots = []
        self._planned = []
        self.setWindowTitle('Tag Purge')
        self.setMinimumSize(560, 640)
        self.resize(600, 700)

        layout = QVBoxLayout(self)

        n_scope = len(self._scope_ids)
        if self._scope_kind == 'selected' and n_scope:
            noun = 'book' if n_scope == 1 else 'books'
            scope = (
                f'The checklist is seeded from the {n_scope} selected {noun}.'
            )
            check_label = (
                f'Seed list from the {n_scope} selected '
                f'book{"s" if n_scope != 1 else ""}'
            )
        elif self._scope_kind == 'shown' and n_scope:
            scope = (
                f'The checklist is seeded from the {n_scope} books currently '
                'shown (search / tag browser / virtual library).'
            )
            check_label = f'Seed list from the {n_scope} books currently shown'
        else:
            scope = (
                'The checklist includes tags from the currently open library.'
            )
            check_label = 'Seed list from the current library view'
        intro = QLabel(
            'Remove tags that appear on only a few works. Only Calibre\'s '
            '<b>Tags</b> column is changed — Fandom, Relationships, '
            'Collections, and Original Tags are left alone.\n\n'
            f'{scope} Uncheck below to seed from every book.\n\n'
            'Set the maximum number of works, then click <b>Show tags</b>. '
            'Type in the filter box to fuzzy-search tag names. Check the '
            'tags to remove and click <b>Purge</b>.'
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        disclaimer = QLabel(
            '<b>Important:</b> The list is filled from the selected or shown '
            'books, but the number next to each tag is how many works in the '
            '<b>entire library</b> have that tag. <b>Purge removes those tags '
            'from every book in this library</b>, not only the selection.'
        )
        disclaimer.setWordWrap(True)
        disclaimer.setTextFormat(Qt.RichText if hasattr(Qt, 'RichText') else Qt.TextFormat.RichText)
        try:
            disclaimer.setStyleSheet(
                'QLabel { background-color: palette(midlight); padding: 8px; }'
            )
        except Exception:
            pass
        layout.addWidget(disclaimer)

        max_row = QHBoxLayout()
        max_row.addWidget(QLabel('Maximum works per tag'))
        self.max_works = QSpinBox()
        self.max_works.setMinimum(1)
        self.max_works.setMaximum(9999)
        self.max_works.setValue(1)
        self.max_works.setToolTip(
            'Show tags that appear on this many works or fewer. '
            '1 lists tags used on a single work; 2 lists tags used on '
            'one or two works.'
        )
        max_row.addWidget(self.max_works)
        self.show_btn = QPushButton('Show tags')
        self.show_btn.setDefault(True)
        self.show_btn.clicked.connect(self.refresh_list)
        max_row.addWidget(self.show_btn)
        max_row.addStretch(1)
        layout.addLayout(max_row)

        self.selected_only = QCheckBox(check_label)
        self.selected_only.setChecked(bool(self._scope_ids))
        self.selected_only.setVisible(bool(self._scope_ids))
        self.selected_only.setToolTip(
            'Fill the checklist from tags on these books. The count beside '
            'each tag and Purge still apply to the entire library.'
        )
        layout.addWidget(self.selected_only)

        filter_row = QHBoxLayout()
        filter_row.addWidget(QLabel('Filter tags'))
        self.name_filter = _form_line('Fluff, slow brn')
        self.name_filter.setToolTip(
            'Fuzzy-match tag names. Comma-separated terms match any of them. '
            'Does not change counts or which books Purge writes to.'
        )
        filter_row.addWidget(self.name_filter, 1)
        layout.addLayout(filter_row)

        self.status = QLabel('Click Show tags to list rare tags.')
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

        select_row = QHBoxLayout()
        select_all = QPushButton('Select all')
        select_all.clicked.connect(lambda: self._set_all_checked(True))
        select_none = QPushButton('Select none')
        select_none.clicked.connect(lambda: self._set_all_checked(False))
        select_row.addWidget(select_all)
        select_row.addWidget(select_none)
        select_row.addStretch(1)
        layout.addLayout(select_row)

        self.tag_list = QListWidget()
        try:
            self.tag_list.setSelectionMode(QListWidget.NoSelection)
        except AttributeError:
            pass
        self.tag_list.itemChanged.connect(lambda *_args: self._sync_purge_enabled())
        layout.addWidget(self.tag_list, 1)
        self.selected_only.stateChanged.connect(lambda *_args: self.refresh_list())
        self.name_filter.textChanged.connect(lambda *_args: self._apply_name_filter())

        buttons = QDialogButtonBox()
        self.purge_btn = buttons.addButton('Purge', QDialogButtonBox.DestructiveRole)
        self.purge_btn.setAutoDefault(False)
        self.purge_btn.setEnabled(False)
        self.purge_btn.clicked.connect(self.purge)
        close_btn = buttons.addButton(QDialogButtonBox.Close)
        close_btn.setAutoDefault(False)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self.refresh_list()

    def _checked_tag_names(self) -> list[str]:
        names: list[str] = []
        checked = _checked_state()
        for index in range(self.tag_list.count()):
            item = self.tag_list.item(index)
            if item is None or item.checkState() != checked:
                continue
            name = item.data(Qt.UserRole)
            if name:
                names.append(str(name))
        return names

    def _set_all_checked(self, checked: bool) -> None:
        state = _checked_state() if checked else _unchecked_state()
        for index in range(self.tag_list.count()):
            item = self.tag_list.item(index)
            if item is not None:
                item.setCheckState(state)
        self._sync_purge_enabled()

    def _sync_purge_enabled(self) -> None:
        self.purge_btn.setEnabled(bool(self._checked_tag_names()))

    def _scope_status_suffix(self) -> str:
        if not (self._scope_ids and self.selected_only.isChecked()):
            return ''
        if self._scope_kind == 'shown':
            return ', seeded from currently shown books'
        return ', seeded from selected books'

    def _fill_tag_list(self, planned) -> None:
        self.tag_list.blockSignals(True)
        self.tag_list.clear()
        checkable = _checked_flag()
        checked = _checked_state()
        for name, count in planned:
            noun = 'work' if count == 1 else 'works'
            item = QListWidgetItem(f'{name}  ({count} {noun})')
            item.setFlags(item.flags() | checkable)
            item.setCheckState(checked)
            item.setData(Qt.UserRole, name)
            self.tag_list.addItem(item)
        self.tag_list.blockSignals(False)
        self._sync_purge_enabled()

    def _apply_name_filter(self) -> None:
        from calibre_plugins.fanfic_organizer.tag_purge import filter_tags_by_name

        visible = filter_tags_by_name(self._planned, self.name_filter.text())
        self._fill_tag_list(visible)
        max_works = int(self.max_works.value())
        noun = 'work' if max_works == 1 else 'works'
        extra = self._scope_status_suffix()
        n_library = len(self._library_snapshots)
        query = self.name_filter.text().strip()
        shown = len(visible)
        total = len(self._planned)
        if total and query and shown != total:
            count_bit = f'Showing {shown} of {total} tag(s)'
        elif self._planned:
            count_bit = f'{total} tag(s) appear on at most {max_works} {noun}'
        else:
            count_bit = (
                f'No tags appear on at most {max_works} {noun}'
            )
        self.status.setText(
            f'{count_bit} in the library ({n_library} book(s){extra}). '
            'Purge applies to the whole library.'
        )

    def refresh_list(self) -> None:
        from calibre_plugins.fanfic_organizer.tag_purge import (
            load_snapshots,
            plan_tag_purge,
            resolve_scope_ids,
        )

        db = self.gui.current_db
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            try:
                library_ids = resolve_scope_ids(
                    db, '', use_virtual_library=False
                )
                self._library_snapshots = load_snapshots(db, library_ids)
                source = None
                if self._scope_ids and self.selected_only.isChecked():
                    seed_ids = set(self._scope_ids)
                    source = [
                        book
                        for book in self._library_snapshots
                        if book.book_id in seed_ids
                    ]
                self._planned = plan_tag_purge(
                    self._library_snapshots,
                    max_works=int(self.max_works.value()),
                    source=source,
                )
            except Exception as exc:
                error_dialog(
                    self,
                    'Fanfic Organizer',
                    'Could not read tags from this library.',
                    det_msg=str(exc),
                    show=True,
                )
                return
        finally:
            QApplication.restoreOverrideCursor()

        self._apply_name_filter()

    def purge(self) -> None:
        from calibre_plugins.fanfic_organizer.importer import (
            refresh_library_ui,
            set_book_tags,
        )
        from calibre_plugins.fanfic_organizer.tag_purge import purge_updates

        names = self._checked_tag_names()
        if not names:
            error_dialog(
                self,
                'Fanfic Organizer',
                'Check one or more tags to purge.',
                show=True,
            )
            return

        updates = purge_updates(self._library_snapshots, names)
        if not updates:
            info_dialog(
                self,
                'Fanfic Organizer',
                'None of the books in this library still have those tags.',
                show=True,
            )
            self.refresh_list()
            return

        preview = ', '.join(names[:8])
        extra = '' if len(names) <= 8 else f' (+{len(names) - 8} more)'
        if not question_dialog(
            self,
            'Fanfic Organizer',
            (
                f'Remove {len(names)} tag(s) from the Tags column on every '
                f'book in this library that has them ({len(updates)} book(s))?\n\n'
                f'{preview}{extra}\n\n'
                'This is not limited to the selected books. Fandom, '
                'Relationships, Collections, and Original Tags are not changed.'
            ),
        ):
            return

        db = self.gui.current_db
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            try:
                for book_id, tags in updates:
                    set_book_tags(db, book_id, tags)
                refresh_library_ui(
                    self.gui, [book_id for book_id, _tags in updates]
                )
            except Exception as exc:
                error_dialog(
                    self,
                    'Fanfic Organizer',
                    'Failed while removing tags.',
                    det_msg=str(exc),
                    show=True,
                )
                return
        finally:
            QApplication.restoreOverrideCursor()

        info_dialog(
            self,
            'Fanfic Organizer',
            f'Removed {len(names)} tag(s) from {len(updates)} book(s).',
            show=True,
        )
        self.refresh_list()


class _FacetPicker(QWidget):
    """Dropdown of seed-work values plus a list of tags included in the search."""

    def __init__(
        self,
        options: list[str],
        counts: dict[str, int] | None = None,
        preselected: list[str] | None = None,
        parent=None,
    ):
        super().__init__(parent)
        self._counts = dict(counts or {})
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        row = QHBoxLayout()
        self.combo = QComboBox()
        self.combo.setEditable(True)
        try:
            self.combo.setInsertPolicy(QComboBox.NoInsert)
        except AttributeError:
            self.combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.combo.addItem('')
        for name in options:
            extra = self._counts.get(name, 1)
            label = f'{name} ({extra})' if extra > 1 else name
            self.combo.addItem(label, name)
        add = QPushButton('Add')
        add.clicked.connect(self.add_current)
        add_all = QPushButton('Add all')
        add_all.clicked.connect(self.add_all)
        add_all.setEnabled(bool(options))
        clear = QPushButton('Clear')
        clear.clicked.connect(self.clear)
        row.addWidget(self.combo, 1)
        row.addWidget(add)
        row.addWidget(add_all)
        row.addWidget(clear)
        layout.addLayout(row)

        self.chosen = QListWidget()
        self.chosen.setMaximumHeight(92)
        self.chosen.itemDoubleClicked.connect(self._remove_item)
        layout.addWidget(self.chosen)
        hint = QLabel('Double-click a selected value to remove it.')
        hint.setStyleSheet('color: #666; font-size: 11px;')
        layout.addWidget(hint)

        line = self.combo.lineEdit()
        if line is not None:
            line.returnPressed.connect(self.add_current)
        for name in preselected or []:
            self._add_name(name)

    def _add_name(self, name: str) -> None:
        name = str(name or '').strip()
        if not name:
            return
        existing = {
            self.chosen.item(i).text().casefold() for i in range(self.chosen.count())
        }
        if name.casefold() in existing:
            return
        self.chosen.addItem(name)

    def add_current(self) -> None:
        data = self.combo.currentData()
        text = data if isinstance(data, str) and data else self.combo.currentText()
        self._add_name(str(text or '').strip())

    def add_all(self) -> None:
        for index in range(self.combo.count()):
            data = self.combo.itemData(index)
            if isinstance(data, str) and data.strip():
                self._add_name(data)

    def clear(self) -> None:
        self.chosen.clear()

    def _remove_item(self, item: QListWidgetItem) -> None:
        row = self.chosen.row(item)
        if row >= 0:
            self.chosen.takeItem(row)

    def selected(self) -> list[str]:
        return [
            self.chosen.item(i).text().strip()
            for i in range(self.chosen.count())
            if self.chosen.item(i).text().strip()
        ]


class SimilarSearchDialog(QDialog):
    """Craft an AO3 search from selected library book metadata."""

    def __init__(self, parent, facets, titles: list[str] | None = None):
        super().__init__(parent)
        from calibre_plugins.fanfic_organizer.similar import SimilarSelect

        self.facets = facets
        self.setWindowTitle('Search similar on AO3')
        self.setMinimumWidth(560)
        self.resize(640, 780)

        outer = QVBoxLayout()
        self.setLayout(outer)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        try:
            from PyQt5.Qt import QFrame

            scroll.setFrameShape(QFrame.NoFrame)
        except Exception:
            pass
        body = QWidget()
        layout = QVBoxLayout(body)
        scroll.setWidget(body)
        outer.addWidget(scroll)

        n = len(titles or facets.titles or [])
        shown = titles or list(facets.titles or [])
        preview = ', '.join(shown[:4])
        if len(shown) > 4:
            preview += ', …'
        intro = QLabel(
            f'Search AO3 for works similar to {n} selected book'
            f'{"s" if n != 1 else ""}'
            f'{": " + preview if preview else ""}.\n\n'
            'Dropdowns are filled from fandoms, authors, relationships, '
            'characters, and tags on the selection (merged if several books). '
            'AO3 requires <b>every</b> tag you add, so start from a fandom '
            'and add only a ship or two. Fandoms are pre-selected.'
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        default = SimilarSelect.default_for(facets)
        counts = facets.counts or {}
        pickers = QGroupBox('Include in search')
        pick_form = QFormLayout(pickers)
        self.fandoms = _FacetPicker(
            list(facets.fandoms), counts.get('fandoms'), default.fandoms
        )
        self.authors = _FacetPicker(list(facets.authors), counts.get('authors'))
        self.relationships = _FacetPicker(
            list(facets.relationships), counts.get('relationships')
        )
        self.characters = _FacetPicker(
            list(facets.characters), counts.get('characters')
        )
        self.tags = _FacetPicker(list(facets.tags), counts.get('tags'))
        self.excluded = _FacetPicker(
            list(facets.tags) + list(facets.relationships),
            {},
        )
        pick_form.addRow('Fandoms', self.fandoms)
        pick_form.addRow('Authors', self.authors)
        pick_form.addRow('Relationships', self.relationships)
        pick_form.addRow('Characters', self.characters)
        pick_form.addRow('Additional tags', self.tags)
        pick_form.addRow('Exclude tags', self.excluded)
        layout.addWidget(pickers)

        extra = QGroupBox('Also')
        extra_form = QFormLayout(extra)
        self.extra_query = _form_line('optional extra AO3 query')
        self.sort_column = QComboBox()
        for value, label in SORT_OPTIONS:
            self.sort_column.addItem(label, value)
        _set_combo_data(self.sort_column, 'kudos_count')
        self.complete = QComboBox()
        self.complete.addItem('Any', '')
        self.complete.addItem('Complete only', 'true')
        self.complete.addItem('In progress only', 'false')
        self.language_id = _form_line('en', 'en')
        extra_form.addRow('Search query', self.extra_query)
        extra_form.addRow('Sort by', self.sort_column)
        extra_form.addRow('Complete works (AO3)', self.complete)
        extra_form.addRow('Language', self.language_id)
        layout.addWidget(extra)

        filters = QGroupBox('Result filters')
        filters_form = QFormLayout(filters)
        self.max_results = _form_line(
            'no limit',
            str(prefs.get('last_max_results') or '25'),
        )
        self.min_score = _form_line('none')
        self.min_kudos = _form_line()
        self.min_words = _form_line()
        self.complete_only = QCheckBox(
            'Only include works with all planned chapters posted (7/7)'
        )
        filters_form.addRow('Max results', self.max_results)
        filters_form.addRow('Min quality score', self.min_score)
        filters_form.addRow('Min kudos', self.min_kudos)
        filters_form.addRow('Min words', self.min_words)
        filters_form.addRow(self.complete_only)
        layout.addWidget(filters)

        import_box = QGroupBox('Import')
        import_layout = QVBoxLayout(import_box)
        self.download_epubs = QCheckBox('Download native EPUBs into this library')
        self.download_epubs.setChecked(bool(prefs.get('download_epubs', True)))
        self.update_existing = QCheckBox(
            'Update existing books matched by AO3 work id or URL'
        )
        self.update_existing.setChecked(bool(prefs.get('update_existing', True)))
        self.simplify_tags = QCheckBox(
            'Simplify tags, fandoms & relationships (AO3 canonical + user rules)'
        )
        self.simplify_tags.setChecked(bool(prefs.get('simplify_tags', False)))
        import_layout.addWidget(self.download_epubs)
        import_layout.addWidget(self.update_existing)
        import_layout.addWidget(self.simplify_tags)
        layout.addWidget(import_box)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        ok_btn = buttons.button(QDialogButtonBox.Ok)
        if ok_btn is not None:
            ok_btn.setText('Search and import')
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)

    def _select(self):
        from calibre_plugins.fanfic_organizer.similar import SimilarSelect

        return SimilarSelect(
            authors=self.authors.selected(),
            fandoms=self.fandoms.selected(),
            relationships=self.relationships.selected(),
            characters=self.characters.selected(),
            tags=self.tags.selected(),
            excluded_tags=self.excluded.selected(),
            extra_query=self.extra_query.text().strip(),
        )

    def accept(self) -> None:
        values = self.values()
        if not scrape_search_is_usable(values):
            error_dialog(
                self,
                'Fanfic Organizer',
                'Add a fandom, author, tag, or query so AO3 has something to search.',
                show=True,
            )
            return
        super().accept()

    def values(self) -> dict:
        from calibre_plugins.fanfic_organizer.similar import selection_to_fields

        fields = selection_to_fields(self._select())
        return {
            'url': '',
            'use_form_criteria': True,
            'tag_id': fields['tag_id'],
            'query': fields['query'],
            'creators': fields['creators'],
            'sort_column': self.sort_column.currentData(),
            'complete': self.complete.currentData(),
            'language_id': self.language_id.text().strip(),
            'words_from': '',
            'words_to': '',
            'date_from': '',
            'date_to': '',
            'other_tag_names': fields['other_tag_names'],
            'excluded_tag_names': fields['excluded_tag_names'],
            'relationship_ids': '',
            'freeform_ids': '',
            'character_ids': '',
            'max_results': self.max_results.text().strip(),
            'start_page': '1',
            'min_score': self.min_score.text().strip(),
            'min_kudos': self.min_kudos.text().strip(),
            'min_words': self.min_words.text().strip(),
            'complete_only': self.complete_only.isChecked(),
            'download_epubs': self.download_epubs.isChecked(),
            'update_existing': self.update_existing.isChecked(),
            'simplify_tags': self.simplify_tags.isChecked(),
        }


class CollectionRulesPage(QWidget):
    """Collection membership rules. The Calibre column is a computed view."""

    def __init__(self, dialog):
        super().__init__(dialog)
        self._dialog = dialog
        self._rows: list[dict] = []
        self._edit_id = ''
        self._loading = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 8, 0, 0)
        intro = QLabel(
            'Collections are computed from these rules. Recompute anytime to '
            'replace the Collections column — that does not fetch AO3 or '
            'change tags. <b>Edit collections of selected</b> shows which rules '
            'put each book in a collection. Adding a book by hand (in Calibre '
            'or with <b>Add selected books</b>) is saved as a per-work rule '
            'so it lands there again.\n\n'
            'Use <b>Never</b> to keep matching books out. Removing a collection '
            'in Calibre alone does not stick after recompute.'
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        self.empty = QLabel(
            'No collection rules yet. Add a tag, fandom, author, or a single '
            'work, then recompute the selected books.'
        )
        self.empty.setWordWrap(True)
        layout.addWidget(self.empty)

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(
            ['On', 'When', 'Collection', 'Kind']
        )
        header = self.table.horizontalHeader()
        header.setStretchLastSection(True)
        try:
            header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
            header.setSectionResizeMode(1, QHeaderView.Stretch)
            header.setSectionResizeMode(2, QHeaderView.Stretch)
            header.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        except Exception:
            pass
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        self.table.cellDoubleClicked.connect(self._on_double_click)
        self.table.itemChanged.connect(self._on_item_changed)
        self.table.itemSelectionChanged.connect(self._sync_row_buttons)
        layout.addWidget(self.table)

        row_btns = QHBoxLayout()
        self.up_btn = QPushButton('Move up')
        self.down_btn = QPushButton('Move down')
        self.edit_btn = QPushButton('Edit')
        self.delete_btn = QPushButton('Remove')
        self.up_btn.clicked.connect(lambda: self._move(up=True))
        self.down_btn.clicked.connect(lambda: self._move(up=False))
        self.edit_btn.clicked.connect(self._edit_selected)
        self.delete_btn.clicked.connect(self._delete_selected)
        for btn in (self.up_btn, self.down_btn, self.edit_btn, self.delete_btn):
            row_btns.addWidget(btn)
        row_btns.addStretch(1)
        layout.addLayout(row_btns)

        form_box = QGroupBox('New collection rule')
        self.form_box = form_box
        form = QFormLayout(form_box)
        self.collections = QLineEdit()
        self.collections.setPlaceholderText('River Song')
        self.collections.setToolTip(
            'Calibre collection name. Leave blank on a tag/fandom/author rule '
            'to use the match text.'
        )
        form.addRow('Collection', self.collections)

        if_row = QWidget()
        if_layout = QHBoxLayout(if_row)
        if_layout.setContentsMargins(0, 0, 0, 0)
        self.match = QComboBox()
        for value, label in _collection_mod().MATCH_CHOICES:
            self.match.addItem(label, value)
        self.match.currentIndexChanged.connect(self._sync_match_fields)
        self.values = QLineEdit()
        attach_collection_match_completer(self.values, self.match, self._dialog)
        if_layout.addWidget(self.match)
        if_layout.addWidget(self.values, 1)
        form.addRow('When', if_row)

        self.mode = QComboBox()
        for value, label in _collection_mod().MODE_CHOICES:
            self.mode.addItem(label, value)
        form.addRow('Then', self.mode)

        add_row = QHBoxLayout()
        self.save_btn = QPushButton('Add rule')
        self.save_btn.clicked.connect(self._save_form)
        self.cancel_edit_btn = QPushButton('Cancel')
        self.cancel_edit_btn.clicked.connect(self._clear_form)
        self.cancel_edit_btn.setVisible(False)
        add_row.addWidget(self.save_btn)
        add_row.addWidget(self.cancel_edit_btn)
        add_row.addStretch(1)
        form.addRow(add_row)
        layout.addWidget(form_box)

        action_row = QHBoxLayout()
        self.recompute_btn = QPushButton('Recompute selected books…')
        self.recompute_btn.setToolTip(
            'Replace Collections on the books currently selected in Calibre.'
        )
        self.recompute_btn.clicked.connect(self._recompute_selection)
        self.edit_sel_btn = QPushButton('Edit collections of selected…')
        self.edit_sel_btn.setToolTip(
            'See which rules put each selected book in a collection, then pin, '
            'exclude, or change those rules.'
        )
        self.edit_sel_btn.clicked.connect(self._edit_selection)
        self.pin_btn = QPushButton('Add selected books to a collection…')
        self.pin_btn.setToolTip(
            'Save a per-work rule for each selected book, then recompute.'
        )
        self.pin_btn.clicked.connect(self._pin_selection)
        action_row.addWidget(self.recompute_btn)
        action_row.addWidget(self.edit_sel_btn)
        action_row.addWidget(self.pin_btn)
        action_row.addStretch(1)
        layout.addLayout(action_row)

        self._sync_match_fields()
        self._reload()

    def _selected_id(self) -> str:
        row = self._selected_row()
        if row is None:
            return ''
        return str(row.get('id') or '')

    def _selected_row(self):
        index = self.table.currentRow()
        if index < 0 or index >= len(self._rows):
            return None
        return self._rows[index]

    def _on_double_click(self, row: int, column: int) -> None:
        if column == 0:
            return
        self.table.selectRow(row)
        self._edit_selected()

    def _on_item_changed(self, item: QTableWidgetItem) -> None:
        if self._loading or item is None or item.column() != 0:
            return
        row = item.row()
        if row < 0 or row >= len(self._rows):
            return
        enabled = bool(self._rows[row].get('enabled', True))
        checked = item.checkState() == _checked_state()
        if checked == enabled:
            return
        self.table.selectRow(row)
        rule_id = str(self._rows[row].get('id') or '')
        if not rule_id:
            return
        self._dialog._run(_collection_mod().build_collections_toggle_argv(rule_id))
        self._reload()

    def _sync_row_buttons(self) -> None:
        has_row = self._selected_row() is not None
        for btn in (self.up_btn, self.down_btn, self.edit_btn, self.delete_btn):
            btn.setEnabled(has_row)

    def _sync_match_fields(self) -> None:
        match = str(self.match.currentData() or 'mentions')
        placeholders = {
            'mentions': 'River Song',
            'is_ci': 'exact tag name',
            'fandom_mentions': 'The Pitt',
            'author_ci': 'author name',
            'work_id': 'AO3 work id',
            'calibre_uuid': 'Calibre book UUID',
        }
        self.values.setPlaceholderText(placeholders.get(match, ''))
        if match in {'work_id', 'calibre_uuid'}:
            self.collections.setPlaceholderText('collection name (required)')
        else:
            self.collections.setPlaceholderText(
                'leave blank to use the match text'
            )

    def _reload(self) -> None:
        from calibre_plugins.fanfic_organizer.collection_rules import (
            format_collection,
            format_kind,
            format_when,
        )

        payload = self._dialog._run(_collection_mod().build_collections_list_argv())
        if payload is None:
            return
        if not isinstance(payload, list):
            error_dialog(
                self,
                'Fanfic Organizer',
                'Could not load collection rules.',
                det_msg=repr(payload),
                show=True,
            )
            return
        selected = self._selected_id()
        self._rows = payload
        self.empty.setVisible(not self._rows)
        self._loading = True
        self.table.blockSignals(True)
        try:
            self.table.setRowCount(len(self._rows))
            checkable = _checked_flag()
            for index, row in enumerate(self._rows):
                enabled = row.get('enabled', True)
                on_item = QTableWidgetItem()
                on_item.setFlags(on_item.flags() | checkable)
                on_item.setCheckState(
                    _checked_state() if enabled else _unchecked_state()
                )
                self.table.setItem(index, 0, on_item)
                self.table.setItem(index, 1, QTableWidgetItem(format_when(row)))
                self.table.setItem(
                    index, 2, QTableWidgetItem(format_collection(row) or '—')
                )
                self.table.setItem(index, 3, QTableWidgetItem(format_kind(row)))
                if str(row.get('id') or '') == selected:
                    self.table.selectRow(index)
            if self._rows and self.table.currentRow() < 0:
                self.table.selectRow(0)
        finally:
            self.table.blockSignals(False)
            self._loading = False
        self._sync_row_buttons()

    def _form_values(self) -> dict:
        match = str(self.match.currentData() or 'mentions')
        return {
            'match': match,
            'values': self.values.text().strip(),
            'collections': self.collections.text().strip(),
            'mode': str(self.mode.currentData() or 'include'),
            'pin': match in {'work_id', 'calibre_uuid'},
        }

    def _save_form(self) -> None:
        fields = self._form_values()
        if not fields['values']:
            error_dialog(
                self, 'Fanfic Organizer', 'Type something to match first.', show=True
            )
            return
        if fields['match'] in {'work_id', 'calibre_uuid'} and not fields['collections']:
            error_dialog(
                self, 'Fanfic Organizer', 'Type a collection name first.', show=True
            )
            return
        mod = _collection_mod()
        if self._edit_id:
            current = next(
                (item for item in self._rows if item.get('id') == self._edit_id),
                {},
            )
            argv = mod.build_collections_set_argv(
                self._edit_id,
                enabled=bool(current.get('enabled', True)),
                description=str(current.get('description') or ''),
                **fields,
            )
        else:
            argv = mod.build_collections_add_argv(**fields)
        if self._dialog._run(argv) is None:
            return
        self._clear_form()
        self._reload()

    def _clear_form(self) -> None:
        self._edit_id = ''
        self.values.clear()
        self.collections.clear()
        self.match.setCurrentIndex(0)
        self.mode.setCurrentIndex(0)
        self.save_btn.setText('Add rule')
        self.form_box.setTitle('New collection rule')
        self.cancel_edit_btn.setVisible(False)
        self._sync_match_fields()

    def _edit_selected(self) -> None:
        rule_id = self._selected_id()
        if not rule_id:
            error_dialog(self, 'Fanfic Organizer', 'Select a rule to edit.', show=True)
            return
        row = next((item for item in self._rows if item.get('id') == rule_id), None)
        if row is None:
            return
        self._edit_id = rule_id
        idx = self.match.findData(str(row.get('match') or 'mentions'))
        if idx >= 0:
            self.match.setCurrentIndex(idx)
        idx = self.mode.findData(str(row.get('mode') or 'include'))
        if idx >= 0:
            self.mode.setCurrentIndex(idx)
        values = row.get('values') or []
        self.values.setText(
            values if isinstance(values, str) else ', '.join(str(item) for item in values)
        )
        self.collections.setText(_collection_mod().format_collection(row))
        self.save_btn.setText('Save rule')
        self.form_box.setTitle('Edit collection rule')
        self.cancel_edit_btn.setVisible(True)
        self._sync_match_fields()

    def _delete_selected(self) -> None:
        from calibre_plugins.fanfic_organizer.collection_rules import format_rule_summary

        rule_id = self._selected_id()
        row = self._selected_row()
        if not rule_id or row is None:
            error_dialog(self, 'Fanfic Organizer', 'Select a rule to remove.', show=True)
            return
        if not question_dialog(
            self,
            'Fanfic Organizer',
            f'Remove this collection rule?\n\n{format_rule_summary(row)}',
        ):
            return
        if self._dialog._run(_collection_mod().build_collections_remove_argv(rule_id)) is None:
            return
        if self._edit_id == rule_id:
            self._clear_form()
        self._reload()

    def _move(self, *, up: bool) -> None:
        rule_id = self._selected_id()
        if not rule_id:
            error_dialog(self, 'Fanfic Organizer', 'Select a rule to move.', show=True)
            return
        if self._dialog._run(
            _collection_mod().build_collections_move_argv(rule_id, up=up)
        ) is None:
            return
        self._reload()

    def _recompute_selection(self) -> None:
        self._dialog.apply_selection = True
        self._dialog.pin_collection = ''
        self._dialog.edit_selection = False
        self._dialog.accept()

    def _pin_selection(self) -> None:
        from calibre_plugins.fanfic_organizer.collection_edit import prompt_collection_name
        from calibre_plugins.fanfic_organizer.collection_rules import (
            collection_names_from_rules,
            merge_collection_names,
        )
        from calibre_plugins.fanfic_organizer.selected import library_collection_names

        db = getattr(self._dialog.parent(), 'current_db', None)
        names = merge_collection_names(
            collection_names_from_rules(self._rows),
            library_collection_names(db) if db is not None else [],
        )
        name = prompt_collection_name(
            self,
            names,
            prompt=(
                'Add the selected books to this collection (pick an existing '
                'name or type a new one):'
            ),
        )
        if not name:
            return
        self._dialog.pin_collection = name
        self._dialog.apply_selection = False
        self._dialog.edit_selection = False
        self._dialog.accept()

    def _edit_selection(self) -> None:
        self._dialog.edit_selection = True
        self._dialog.apply_selection = False
        self._dialog.pin_collection = ''
        self._dialog.accept()


class TagMappingsDialog(QDialog):
    """Collection membership rules and tag keep / rename / drop."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle('Collections & tag rules')
        self.setMinimumSize(680, 580)
        self.resize(760, 640)
        self._rows: list[dict] = []
        self._edit_id = ''
        self._loading = False
        self.apply_selection = False
        self.pin_collection = ''
        self.edit_selection = False

        layout = QVBoxLayout(self)
        intro = QLabel(
            'Collections are computed from rules (Collections tab). Recompute '
            'anytime. Adding one book to a collection is a per-work rule so '
            'it comes back. Tag keep / rename / remove is on the Tag rules tab.'
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        tabs = QTabWidget()
        self.collection_page = CollectionRulesPage(self)
        tabs.addTab(self.collection_page, 'Collections')

        tag_tab = QWidget()
        tag_layout = QVBoxLayout(tag_tab)
        tag_layout.setContentsMargins(0, 8, 0, 0)
        tag_intro = QLabel(
            'Change how a tag is stored. Collection membership belongs on the '
            'Collections tab. Older tag rules that named a collection still apply.'
        )
        tag_intro.setWordWrap(True)
        tag_layout.addWidget(tag_intro)

        self.empty = QLabel(
            'No tag rules yet. Add a keep, rename, or remove rule.'
        )
        self.empty.setWordWrap(True)
        tag_layout.addWidget(self.empty)

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(
            ['On', 'When a tag', 'What happens', 'Collection']
        )
        for col, tip in enumerate(
            (
                'Uncheck to ignore a rule without deleting it.',
                'Which tags this rule looks for.',
                'What to do with the tag itself.',
                'Legacy: still applied if a tag rule named a collection.',
            )
        ):
            header_item = self.table.horizontalHeaderItem(col)
            if header_item is not None:
                header_item.setToolTip(tip)
        header = self.table.horizontalHeader()
        header.setStretchLastSection(True)
        try:
            header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
            header.setSectionResizeMode(1, QHeaderView.Stretch)
            header.setSectionResizeMode(2, QHeaderView.Stretch)
        except Exception:
            pass
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        self.table.cellDoubleClicked.connect(self._on_double_click)
        self.table.itemChanged.connect(self._on_item_changed)
        self.table.itemSelectionChanged.connect(self._sync_row_buttons)
        tag_layout.addWidget(self.table)

        row_btns = QHBoxLayout()
        self.up_btn = QPushButton('Move up')
        self.down_btn = QPushButton('Move down')
        self.edit_btn = QPushButton('Edit')
        self.delete_btn = QPushButton('Remove')
        self.up_btn.setToolTip('Rules at the top are applied first.')
        self.down_btn.setToolTip('Rules at the top are applied first.')
        self.edit_btn.setToolTip('Or double-click a row.')
        self.up_btn.clicked.connect(lambda: self._move(up=True))
        self.down_btn.clicked.connect(lambda: self._move(up=False))
        self.edit_btn.clicked.connect(self._edit_selected)
        self.delete_btn.clicked.connect(self._delete_selected)
        for btn in (self.up_btn, self.down_btn, self.edit_btn, self.delete_btn):
            row_btns.addWidget(btn)
        row_btns.addStretch(1)
        tag_layout.addLayout(row_btns)

        form_box = QGroupBox('New tag rule')
        self.form_box = form_box
        form = QFormLayout(form_box)
        if_row = QWidget()
        if_layout = QHBoxLayout(if_row)
        if_layout.setContentsMargins(0, 0, 0, 0)
        self.match = QComboBox()
        for value, label in _mapping_mod().MATCH_CHOICES:
            self.match.addItem(label, value)
        self.match.setToolTip(
            'contains: the tag name includes this text, or AO3’s usual name '
            'is this.\nis exactly: only this exact tag (or AO3’s usual name).'
        )
        self.values = QLineEdit()
        self.values.setPlaceholderText('River Song')
        self.values.setToolTip('The text to look for in a tag.')
        extras = combined_tag_extras(parent)
        attach_tag_completer(self.values, extra=extras, csv=True)
        if_layout.addWidget(self.match)
        if_layout.addWidget(self.values, 1)
        form.addRow('When a tag', if_row)

        self.collection_wrap = QWidget()
        wrap_form = QFormLayout(self.collection_wrap)
        wrap_form.setContentsMargins(0, 0, 0, 0)
        self.collections = QLineEdit()
        self.collections.setPlaceholderText('optional legacy collection')
        wrap_form.addRow('Also add to collection', self.collections)
        self.collection_wrap.setVisible(False)
        form.addRow(self.collection_wrap)

        tag_row = QWidget()
        tag_layout_fields = QHBoxLayout(tag_row)
        tag_layout_fields.setContentsMargins(0, 0, 0, 0)
        self.action = QComboBox()
        for value, label in _mapping_mod().ACTION_CHOICES:
            if value == 'collect':
                continue
            self.action.addItem(label, value)
        self.action.setToolTip(
            'Keep this spelling: keep your wording (for example Jegulus).\n'
            'Rename it: replace the tag with another name.\n'
            'Remove it: don’t store this tag on the book.'
        )
        self.action.currentIndexChanged.connect(self._sync_action_fields)
        self.map_to = QLineEdit()
        self.map_to.setPlaceholderText('new tag name')
        self.map_to.setToolTip('The name to store instead.')
        attach_tag_completer(self.map_to, extra=extras)
        tag_layout_fields.addWidget(self.action)
        tag_layout_fields.addWidget(self.map_to, 1)
        form.addRow('With the tag itself', tag_row)

        add_row = QHBoxLayout()
        self.save_btn = QPushButton('Add rule')
        self.save_btn.clicked.connect(self._save_form)
        self.cancel_edit_btn = QPushButton('Cancel')
        self.cancel_edit_btn.clicked.connect(self._clear_form)
        self.cancel_edit_btn.setVisible(False)
        add_row.addWidget(self.save_btn)
        add_row.addWidget(self.cancel_edit_btn)
        add_row.addStretch(1)
        form.addRow(add_row)

        preview_row = QWidget()
        preview_layout = QHBoxLayout(preview_row)
        preview_layout.setContentsMargins(0, 0, 0, 0)
        self.preview_tag = QLineEdit()
        self.preview_tag.setPlaceholderText('Melody Pond')
        self.preview_tag.setToolTip(
            'Type a tag from a book to see the cleaned name.'
        )
        attach_tag_completer(self.preview_tag, extra=extras)
        preview_btn = QPushButton('Try')
        preview_btn.setToolTip('Show what Simplify would do with this tag.')
        preview_btn.clicked.connect(self._preview)
        preview_layout.addWidget(self.preview_tag)
        preview_layout.addWidget(preview_btn)
        form.addRow('Try a tag', preview_row)
        self.preview_out = QLabel('')
        self.preview_out.setWordWrap(True)
        form.addRow(self.preview_out)
        tag_layout.addWidget(form_box)
        tabs.addTab(tag_tab, 'Tag rules')
        layout.addWidget(tabs)

        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        close_btn = buttons.button(QDialogButtonBox.Close)
        if close_btn is not None:
            close_btn.clicked.connect(self.accept)
        layout.addWidget(buttons)

        self._sync_action_fields()
        self._reload()

    def _selected_id(self) -> str:
        row = self._selected_row()
        if row is None:
            return ''
        return str(row.get('id') or '')

    def _selected_row(self):
        index = self.table.currentRow()
        if index < 0 or index >= len(self._rows):
            return None
        return self._rows[index]

    def _on_double_click(self, row: int, column: int) -> None:
        if column == 0:
            return
        self.table.selectRow(row)
        self._edit_selected()

    def _on_item_changed(self, item: QTableWidgetItem) -> None:
        if self._loading or item is None or item.column() != 0:
            return
        row = item.row()
        if row < 0 or row >= len(self._rows):
            return
        enabled = bool(self._rows[row].get('enabled', True))
        checked = item.checkState() == _checked_state()
        if checked == enabled:
            return
        self.table.selectRow(row)
        mapping_id = str(self._rows[row].get('id') or '')
        if not mapping_id:
            return
        self._run(_mapping_mod().build_mappings_toggle_argv(mapping_id))
        self._reload()

    def _sync_row_buttons(self) -> None:
        has_row = self._selected_row() is not None
        for btn in (self.up_btn, self.down_btn, self.edit_btn, self.delete_btn):
            btn.setEnabled(has_row)

    def _reload(self) -> None:
        from calibre_plugins.fanfic_organizer.tag_mappings import format_then, format_when

        payload = self._run(_mapping_mod().build_mappings_list_argv())
        if payload is None:
            return
        if not isinstance(payload, list):
            error_dialog(
                self,
                'Fanfic Organizer',
                'Could not load your rules.',
                det_msg=repr(payload),
                show=True,
            )
            return
        selected = self._selected_id()
        self._rows = payload
        self.empty.setVisible(not self._rows)
        self._loading = True
        self.table.blockSignals(True)
        try:
            self.table.setRowCount(len(self._rows))
            checkable = _checked_flag()
            for index, row in enumerate(self._rows):
                enabled = row.get('enabled', True)
                collections = row.get('collections') or []
                if isinstance(collections, str):
                    coll_text = collections
                else:
                    coll_text = ', '.join(str(item) for item in collections)
                if not coll_text.strip():
                    coll_text = '—'
                on_item = QTableWidgetItem()
                on_item.setFlags(on_item.flags() | checkable)
                on_item.setCheckState(
                    _checked_state() if enabled else _unchecked_state()
                )
                on_item.setToolTip(
                    'Uncheck to ignore this rule without deleting it.'
                )
                self.table.setItem(index, 0, on_item)
                self.table.setItem(index, 1, QTableWidgetItem(format_when(row)))
                self.table.setItem(index, 2, QTableWidgetItem(format_then(row)))
                self.table.setItem(index, 3, QTableWidgetItem(coll_text))
                if str(row.get('id') or '') == selected:
                    self.table.selectRow(index)
            if self._rows and self.table.currentRow() < 0:
                self.table.selectRow(0)
        finally:
            self.table.blockSignals(False)
            self._loading = False
        self._sync_row_buttons()

    def _run(self, args: list[str]):
        from calibre_plugins.fanfic_organizer.enrich import EnrichCancelled, run_ao3kit

        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            code, stdout, stderr = run_ao3kit(args)
        except EnrichCancelled:
            return None
        finally:
            QApplication.restoreOverrideCursor()
        if code != 0:
            error_dialog(
                self,
                'Fanfic Organizer',
                'Could not update that rule.',
                det_msg=(stderr or stdout or f'exit {code}').strip(),
                show=True,
            )
            return None
        text = (stdout or '').strip()
        if not text:
            return []
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            error_dialog(
                self,
                'Fanfic Organizer',
                'Could not read the saved rules.',
                det_msg=text,
                show=True,
            )
            return None

    def _ensure_action_in_combo(self, action: str) -> None:
        if self.action.findData(action) >= 0:
            return
        for value, label in _mapping_mod().ACTION_CHOICES:
            if value == action:
                self.action.addItem(label, value)
                return

    def _sync_action_fields(self) -> None:
        is_rename = self.action.currentData() == 'map_to'
        self.map_to.setVisible(is_rename)
        self.map_to.setEnabled(is_rename)
        is_collect = self.action.currentData() == 'collect'
        has_text = bool(self.collections.text().strip())
        self.collection_wrap.setVisible(is_collect or has_text)

    def _form_values(self) -> dict:
        action = str(self.action.currentData() or 'keep_separate')
        collections = self.collections.text().strip()
        if not collections and self._edit_id and not self.collection_wrap.isVisible():
            current = next(
                (item for item in self._rows if item.get('id') == self._edit_id),
                {},
            )
            raw = current.get('collections') or []
            collections = (
                raw if isinstance(raw, str) else ', '.join(str(item) for item in raw)
            )
        return {
            'match': str(self.match.currentData() or 'mentions'),
            'values': self.values.text().strip(),
            'action': action,
            'map_to': self.map_to.text().strip(),
            'collections': collections,
            'stop': action == 'keep_separate',
        }

    def _save_form(self) -> None:
        fields = self._form_values()
        if not fields['values']:
            error_dialog(self, 'Fanfic Organizer', 'Type a tag name first.', show=True)
            return
        if fields['action'] == 'map_to' and not fields['map_to']:
            error_dialog(self, 'Fanfic Organizer', 'Type the new tag name.', show=True)
            return
        mod = _mapping_mod()
        if self._edit_id:
            current = next(
                (item for item in self._rows if item.get('id') == self._edit_id),
                {},
            )
            argv = mod.build_mappings_set_argv(
                self._edit_id,
                enabled=bool(current.get('enabled', True)),
                **fields,
            )
        else:
            argv = mod.build_mappings_add_argv(**fields)
        if self._run(argv) is None:
            return
        self._clear_form()
        self._reload()

    def _clear_form(self) -> None:
        self._edit_id = ''
        self.values.clear()
        self.map_to.clear()
        self.collections.clear()
        self.match.setCurrentIndex(0)
        self.action.setCurrentIndex(0)
        self.save_btn.setText('Add rule')
        self.form_box.setTitle('New tag rule')
        self.cancel_edit_btn.setVisible(False)
        self.collection_wrap.setVisible(False)
        self._sync_action_fields()

    def _edit_selected(self) -> None:
        mapping_id = self._selected_id()
        if not mapping_id:
            error_dialog(self, 'Fanfic Organizer', 'Select a rule to edit.', show=True)
            return
        row = next((item for item in self._rows if item.get('id') == mapping_id), None)
        if row is None:
            return
        mod = _mapping_mod()
        self._edit_id = mapping_id
        kind = mod.ui_match_kind(str(row.get('match') or 'mentions'))
        idx = self.match.findData(kind)
        if idx >= 0:
            self.match.setCurrentIndex(idx)
        action = str(row.get('action') or 'keep_separate')
        self._ensure_action_in_combo(action)
        idx = self.action.findData(action)
        if idx >= 0:
            self.action.setCurrentIndex(idx)
        values = row.get('values') or []
        self.values.setText(
            values if isinstance(values, str) else ', '.join(str(item) for item in values)
        )
        self.map_to.setText(str(row.get('map_to') or ''))
        collections = row.get('collections') or []
        self.collections.setText(
            collections
            if isinstance(collections, str)
            else ', '.join(str(item) for item in collections)
        )
        self.save_btn.setText('Save rule')
        self.form_box.setTitle('Edit tag rule')
        self.cancel_edit_btn.setVisible(True)
        has_coll = bool(self.collections.text().strip()) or action == 'collect'
        self.collection_wrap.setVisible(has_coll)
        self._sync_action_fields()

    def _delete_selected(self) -> None:
        from calibre_plugins.fanfic_organizer.tag_mappings import format_rule_summary

        mapping_id = self._selected_id()
        row = self._selected_row()
        if not mapping_id or row is None:
            error_dialog(self, 'Fanfic Organizer', 'Select a rule to remove.', show=True)
            return
        summary = format_rule_summary(row)
        if not question_dialog(
            self,
            'Fanfic Organizer',
            f'Remove this rule?\n\n{summary}',
        ):
            return
        if self._run(_mapping_mod().build_mappings_remove_argv(mapping_id)) is None:
            return
        if self._edit_id == mapping_id:
            self._clear_form()
        self._reload()

    def _move(self, *, up: bool) -> None:
        mapping_id = self._selected_id()
        if not mapping_id:
            error_dialog(self, 'Fanfic Organizer', 'Select a rule to move.', show=True)
            return
        if self._run(_mapping_mod().build_mappings_move_argv(mapping_id, up=up)) is None:
            return
        self._reload()

    def _preview(self) -> None:
        from calibre_plugins.fanfic_organizer.tag_mappings import format_preview

        tag = self.preview_tag.text().strip()
        if not tag:
            error_dialog(self, 'Fanfic Organizer', 'Type a tag to try.', show=True)
            return
        payload = self._run(_mapping_mod().build_mappings_preview_argv(tag))
        if payload is None:
            return
        if not isinstance(payload, dict):
            error_dialog(self, 'Fanfic Organizer', 'Could not preview that tag.', show=True)
            return
        self.preview_out.setText(format_preview(payload))



def _mapping_mod():
    from calibre_plugins.fanfic_organizer import tag_mappings

    return tag_mappings


def _collection_mod():
    from calibre_plugins.fanfic_organizer import collection_rules

    return collection_rules


class WarmLogDialog(QDialog):
    """Live tail of the background tag-cache log file."""

    def __init__(self, parent, *, log_path, status_path):
        super().__init__(parent)
        self._log_path = log_path
        self._status_path = status_path
        self.setWindowTitle('Background tag cache log')
        self.setMinimumSize(640, 420)
        self.resize(760, 520)
        try:
            self.setWindowModality(Qt.NonModal)
        except Exception:
            pass
        self.setAttribute(Qt.WA_DeleteOnClose)

        layout = QVBoxLayout(self)
        self.header = QLabel()
        self.header.setWordWrap(True)
        layout.addWidget(self.header)

        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        try:
            self.log.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        except AttributeError:
            self.log.setLineWrapMode(QPlainTextEdit.NoWrap)
        layout.addWidget(self.log)

        buttons = QDialogButtonBox()
        self.refresh_btn = buttons.addButton('Refresh', QDialogButtonBox.ActionRole)
        self.refresh_btn.clicked.connect(self.reload)
        buttons.addButton(QDialogButtonBox.Close).clicked.connect(self.close)
        layout.addWidget(buttons)

        self._timer = QTimer(self)
        self._timer.setInterval(2000)
        self._timer.timeout.connect(self.reload)
        self._timer.start()
        self.reload()

    def reload(self) -> None:
        from calibre_plugins.fanfic_organizer.tag_warm import (
            format_warm_log_header,
            read_log_tail,
            read_status_file,
        )

        status = read_status_file(self._status_path)
        self.header.setText(format_warm_log_header(status, self._log_path))
        text = read_log_tail(self._log_path)
        if not text:
            text = (
                'No log yet. Start Tags and collections → Warm tag cache first.\n'
                f'Expected file: {self._log_path}'
            )
        bar = self.log.verticalScrollBar()
        follow = bar.value() >= bar.maximum() - 8
        self.log.setPlainText(text)
        if follow:
            bar.setValue(bar.maximum())

