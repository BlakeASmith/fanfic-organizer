# -*- coding: utf-8 -*-
"""Generic URL / saved-HTML / multi-page web-compile import dialog."""

from __future__ import annotations

from pathlib import Path

from PyQt5.Qt import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QTextEdit,
    QVBoxLayout,
)
from PyQt5.QtWidgets import QFileDialog

from calibre.gui2 import error_dialog, info_dialog

from calibre_plugins.fanfic_organizer.prefs import prefs
from calibre_plugins.fanfic_organizer.sources.web.run import web_import_is_usable


def _form_line(placeholder: str = '', text: str = '') -> QLineEdit:
    line = QLineEdit()
    if placeholder:
        line.setPlaceholderText(placeholder)
    if text:
        line.setText(text)
    return line


class WebImportDialog(QDialog):
    """Fetch a URL, import HTML, or compile multiple pages into one EPUB."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle('Import from URL or HTML')
        self.setMinimumWidth(560)
        self.resize(620, 640)

        layout = QVBoxLayout(self)
        intro = QLabel(
            'Import a <b>static HTML</b> page — or compile several linked pages '
            'into one EPUB with a table of contents — into the currently open '
            'library.'
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        warn = QLabel(
            '<b>Limitation:</b> the built-in crawler does not run JavaScript. '
            'For dynamic sites, install the companion <b>Tampermonkey</b> '
            'script (button below), export a JSON bundle from the browser, '
            'and import that bundle here.'
        )
        warn.setWordWrap(True)
        warn.setStyleSheet('color: #6a4a00;')
        layout.addWidget(warn)

        mode_row = QHBoxLayout()
        mode_row.addWidget(QLabel('Mode'))
        self.mode = QComboBox()
        self.mode.addItem('Single page', 'single')
        self.mode.addItem('Multi-page compile (unified EPUB)', 'compile')
        saved_mode = prefs.get('last_web_mode') or 'single'
        idx = self.mode.findData(saved_mode)
        self.mode.setCurrentIndex(idx if idx >= 0 else 0)
        self.mode.currentIndexChanged.connect(self._sync_mode)
        mode_row.addWidget(self.mode, 1)
        layout.addLayout(mode_row)

        # --- Single-page fields ---
        self.single_box = QGroupBox('Single page')
        single_form = QFormLayout(self.single_box)
        self.url = _form_line(
            'https://example.com/article',
            prefs.get('last_web_url') or '',
        )
        html_row = QHBoxLayout()
        self.html_path = _form_line(
            'Saved page.html',
            prefs.get('last_web_html_path') or '',
        )
        browse = QPushButton('Browse…')
        browse.clicked.connect(self._browse_html)
        html_row.addWidget(self.html_path, 1)
        html_row.addWidget(browse)
        single_form.addRow('Page URL', self.url)
        single_form.addRow('Saved HTML file', html_row)
        layout.addWidget(self.single_box)

        # --- Multi-page fields ---
        self.compile_box = QGroupBox('Multi-page compile')
        compile_layout = QVBoxLayout(self.compile_box)
        compile_form = QFormLayout()
        self.seeds = QTextEdit()
        self.seeds.setPlaceholderText(
            'One URL per line (seeds). Leave empty if using a full link list '
            'or a Tampermonkey bundle.'
        )
        self.seeds.setMaximumHeight(90)
        self.seeds.setPlainText(prefs.get('last_web_seeds') or '')
        compile_form.addRow('Seed URLs', self.seeds)

        self.full_list = QCheckBox(
            'These are the full link list (do not expand / follow links)'
        )
        self.full_list.setChecked(bool(prefs.get('last_web_full_list', False)))
        self.full_list.toggled.connect(self._sync_expand)
        compile_form.addRow(self.full_list)

        self.expand = QComboBox()
        self.expand.addItem('Same domain', 'same_domain')
        self.expand.addItem('Specific domains…', 'domains')
        self.expand.addItem('Free (any http/https link)', 'free')
        self.expand.addItem('None (seeds only)', 'none')
        exp = prefs.get('last_web_expand') or 'same_domain'
        eidx = self.expand.findData(exp)
        self.expand.setCurrentIndex(eidx if eidx >= 0 else 0)
        self.expand.currentIndexChanged.connect(self._sync_expand)
        compile_form.addRow('Link expansion', self.expand)

        self.domains = _form_line(
            'example.com, docs.example.com',
            prefs.get('last_web_domains') or '',
        )
        compile_form.addRow('Allowed domains', self.domains)

        limits = QHBoxLayout()
        self.max_pages = QSpinBox()
        self.max_pages.setRange(1, 500)
        self.max_pages.setValue(int(prefs.get('last_web_max_pages') or 50))
        self.max_depth = QSpinBox()
        self.max_depth.setRange(0, 20)
        self.max_depth.setValue(int(prefs.get('last_web_max_depth') or 2))
        limits.addWidget(QLabel('Max pages'))
        limits.addWidget(self.max_pages)
        limits.addWidget(QLabel('Max depth'))
        limits.addWidget(self.max_depth)
        limits.addStretch(1)
        compile_form.addRow(limits)

        self.book_title = _form_line(
            'Optional book title',
            prefs.get('last_web_book_title') or '',
        )
        compile_form.addRow('Book title', self.book_title)
        compile_layout.addLayout(compile_form)

        bundle_row = QHBoxLayout()
        self.bundle_path = _form_line(
            'Tampermonkey bundle.json',
            prefs.get('last_web_bundle_path') or '',
        )
        browse_bundle = QPushButton('Browse…')
        browse_bundle.clicked.connect(self._browse_bundle)
        bundle_row.addWidget(self.bundle_path, 1)
        bundle_row.addWidget(browse_bundle)
        compile_layout.addWidget(QLabel('Or import Tampermonkey / crawl bundle'))
        compile_layout.addLayout(bundle_row)

        tm_row = QHBoxLayout()
        save_tm = QPushButton('Save Tampermonkey script…')
        save_tm.setToolTip(
            'Writes the companion userscript. Install it in Tampermonkey, '
            'crawl JS-rendered pages in the browser, export JSON, then choose '
            'that file above.'
        )
        save_tm.clicked.connect(self._save_userscript)
        tm_row.addWidget(save_tm)
        tm_row.addStretch(1)
        compile_layout.addLayout(tm_row)
        layout.addWidget(self.compile_box)

        import_box = QGroupBox('Import options')
        import_form = QFormLayout(import_box)
        self.build_epub = QCheckBox('Build EPUB from extracted HTML')
        self.build_epub.setChecked(bool(prefs.get('web_build_epub', True)))
        self.build_epub.setToolTip(
            'Packs extracted HTML into an EPUB under the job folder and '
            'attaches it in Calibre. Multi-page mode always builds one unified EPUB.'
        )
        self.update_existing = QCheckBox(
            'Update existing books (same web id / URL)'
        )
        self.update_existing.setChecked(bool(prefs.get('update_existing', True)))
        import_form.addRow(self.build_epub)
        import_form.addRow(self.update_existing)
        layout.addWidget(import_box)

        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel
        )
        ok_btn = buttons.button(QDialogButtonBox.Ok)
        if ok_btn is not None:
            ok_btn.setText('Import')
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._sync_mode()
        self._sync_expand()

    def _mode_id(self) -> str:
        return str(self.mode.currentData() or 'single')

    def _sync_mode(self) -> None:
        compile_mode = self._mode_id() == 'compile'
        self.single_box.setVisible(not compile_mode)
        self.compile_box.setVisible(compile_mode)
        if compile_mode:
            self.build_epub.setChecked(True)
            self.build_epub.setEnabled(False)
        else:
            self.build_epub.setEnabled(True)

    def _sync_expand(self) -> None:
        is_domains = str(self.expand.currentData() or '') == 'domains'
        self.domains.setEnabled(is_domains and not self.full_list.isChecked())
        self.expand.setEnabled(not self.full_list.isChecked())

    def _browse_html(self) -> None:
        start = self.html_path.text().strip() or prefs.get('last_web_html_path') or ''
        path, _ = QFileDialog.getOpenFileName(
            self,
            'Select saved HTML page',
            start,
            'HTML files (*.html *.htm);;All files (*)',
        )
        if path:
            self.html_path.setText(path)

    def _browse_bundle(self) -> None:
        start = (
            self.bundle_path.text().strip()
            or prefs.get('last_web_bundle_path')
            or ''
        )
        path, _ = QFileDialog.getOpenFileName(
            self,
            'Select crawl bundle JSON',
            start,
            'JSON bundles (*.json);;All files (*)',
        )
        if path:
            self.bundle_path.setText(path)

    def _save_userscript(self) -> None:
        try:
            from ao3kit.webcompile.userscript import (
                USERSCRIPT_NAME,
                resolve_userscript,
            )
        except Exception as exc:
            error_dialog(
                self,
                'Fanfic Organizer',
                f'Could not load userscript helper: {exc}',
                show=True,
            )
            return
        src = resolve_userscript()
        if src is None or not Path(src).is_file():
            error_dialog(
                self,
                'Fanfic Organizer',
                'Tampermonkey userscript not found in this install.',
                show=True,
            )
            return
        dest, _ = QFileDialog.getSaveFileName(
            self,
            'Save Tampermonkey userscript',
            str(Path.home() / USERSCRIPT_NAME),
            'User scripts (*.user.js);;All files (*)',
        )
        if not dest:
            return
        try:
            Path(dest).write_text(
                Path(src).read_text(encoding='utf-8'), encoding='utf-8'
            )
        except OSError as exc:
            error_dialog(
                self,
                'Fanfic Organizer',
                f'Could not write script: {exc}',
                show=True,
            )
            return
        info_dialog(
            self,
            'Fanfic Organizer',
            'Saved userscript. Open it with Tampermonkey (or drag onto the '
            'Tampermonkey dashboard), then use the menu commands on a page to '
            'crawl and export a JSON bundle.',
            show=True,
        )

    def accept(self) -> None:
        # Keep expand controls consistent with full-list checkbox.
        self._sync_expand()
        values = self.values()
        if not web_import_is_usable(values):
            error_dialog(
                self,
                'Fanfic Organizer',
                'Enter a page URL, choose a saved HTML file, provide seed URLs, '
                'or select a Tampermonkey crawl bundle.',
                show=True,
            )
            return
        if (
            values.get('mode') == 'compile'
            and values.get('expand') == 'domains'
            and not values.get('domains')
            and not values.get('bundle_path')
            and not values.get('full_list')
        ):
            error_dialog(
                self,
                'Fanfic Organizer',
                'Specific-domains expansion needs at least one allowed domain.',
                show=True,
            )
            return
        super().accept()

    def values(self) -> dict:
        mode = self._mode_id()
        seeds = [
            line.strip()
            for line in self.seeds.toPlainText().splitlines()
            if line.strip()
        ]
        domains = [
            part.strip()
            for part in self.domains.text().split(',')
            if part.strip()
        ]
        full_list = bool(self.full_list.isChecked())
        expand = 'none' if full_list else str(self.expand.currentData() or 'same_domain')
        return {
            'mode': mode,
            'url': self.url.text().strip(),
            'html_path': self.html_path.text().strip(),
            'seeds': seeds,
            'full_list': full_list,
            'expand': expand,
            'domains': domains,
            'max_pages': int(self.max_pages.value()),
            'max_depth': int(self.max_depth.value()),
            'book_title': self.book_title.text().strip(),
            'bundle_path': self.bundle_path.text().strip(),
            'update_existing': self.update_existing.isChecked(),
            'download_epubs': True
            if mode == 'compile'
            else self.build_epub.isChecked(),
            'simplify_tags': False,
            'drop_unmarked': False,
        }
