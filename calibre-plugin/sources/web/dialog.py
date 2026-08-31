# -*- coding: utf-8 -*-
"""URL / HTML / multi-page web-compile import dialog (Tampermonkey-first)."""

from __future__ import annotations

from pathlib import Path

from PyQt5.Qt import (
    QCheckBox,
    QComboBox,
    QDesktopServices,
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
    QUrl,
    QVBoxLayout,
)
from PyQt5.QtWidgets import QFileDialog

from calibre.gui2 import error_dialog, info_dialog

from calibre_plugins.fanfic_organizer.prefs import prefs
from calibre_plugins.fanfic_organizer.sources.web.run import web_import_is_usable

TAMPERMONKEY_HOME = 'https://www.tampermonkey.net/'
USERSCRIPT_NAME = 'fanfic-organizer-webcompile.user.js'


def _form_line(placeholder: str = '', text: str = '') -> QLineEdit:
    line = QLineEdit()
    if placeholder:
        line.setPlaceholderText(placeholder)
    if text:
        line.setText(text)
    return line


class WebImportDialog(QDialog):
    """Import a page or compile multiple pages (prefer Tampermonkey crawl)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle('Import from URL or HTML')
        self.setMinimumWidth(580)
        self.resize(640, 720)

        layout = QVBoxLayout(self)

        mode_row = QHBoxLayout()
        mode_row.addWidget(QLabel('Mode'))
        self.mode = QComboBox()
        self.mode.addItem('Multi-page compile (recommended)', 'compile')
        self.mode.addItem('Single page', 'single')
        saved_mode = prefs.get('last_web_mode') or 'compile'
        idx = self.mode.findData(saved_mode)
        self.mode.setCurrentIndex(idx if idx >= 0 else 0)
        self.mode.currentIndexChanged.connect(self._sync_mode)
        mode_row.addWidget(self.mode, 1)
        layout.addLayout(mode_row)

        # --- Multi-page (Tampermonkey primary) ---
        self.compile_box = QGroupBox('Multi-page compile')
        compile_layout = QVBoxLayout(self.compile_box)

        how = QLabel(
            '<b>Preferred:</b> crawl in the browser with the companion '
            '<b>Tampermonkey</b> script (works on JavaScript-rendered sites), '
            'export a JSON bundle, then import it here.'
        )
        how.setWordWrap(True)
        compile_layout.addWidget(how)

        steps = QLabel(
            '<ol style="margin-left:0;padding-left:1.2em;">'
            '<li>Install Tampermonkey in your browser '
            '(button below opens the site).</li>'
            '<li>Save / install this plugin’s userscript '
            '(<b>Save userscript…</b>), then open the saved '
            '<code>.user.js</code> file so Tampermonkey offers to install it.</li>'
            '<li>On the first page of the work, use the Tampermonkey menu: '
            '<i>Web compile: start crawl from this page</i> '
            '(or <i>add this page only</i> for a manual list).</li>'
            '<li>When finished, <i>Web compile: export JSON bundle</i>, '
            'then choose that file below and click Import.</li>'
            '</ol>'
        )
        steps.setWordWrap(True)
        compile_layout.addWidget(steps)

        tm_row = QHBoxLayout()
        open_tm = QPushButton('Open Tampermonkey site…')
        open_tm.setToolTip(TAMPERMONKEY_HOME)
        open_tm.clicked.connect(self._open_tampermonkey)
        save_tm = QPushButton('Save userscript…')
        save_tm.setToolTip(
            'Writes fanfic-organizer-webcompile.user.js. Open that file in the '
            'browser after Tampermonkey is installed to add the script.'
        )
        save_tm.clicked.connect(self._save_userscript)
        tm_row.addWidget(open_tm)
        tm_row.addWidget(save_tm)
        tm_row.addStretch(1)
        compile_layout.addLayout(tm_row)

        bundle_row = QHBoxLayout()
        self.bundle_path = _form_line(
            'Tampermonkey bundle.json',
            prefs.get('last_web_bundle_path') or '',
        )
        browse_bundle = QPushButton('Browse…')
        browse_bundle.clicked.connect(self._browse_bundle)
        bundle_row.addWidget(self.bundle_path, 1)
        bundle_row.addWidget(browse_bundle)
        compile_layout.addWidget(QLabel('Crawl bundle (from Tampermonkey export)'))
        compile_layout.addLayout(bundle_row)

        advanced = QGroupBox('Optional: static crawl (no JavaScript)')
        advanced.setCheckable(True)
        advanced.setChecked(bool(prefs.get('last_web_static_crawl', False)))
        advanced.toggled.connect(self._sync_expand)
        self.static_box = advanced
        adv_layout = QVBoxLayout(advanced)
        adv_note = QLabel(
            'Only use this for plain HTML sites. Expansion follows links on '
            'fetched pages; dynamic sites will look empty — use Tampermonkey instead.'
        )
        adv_note.setWordWrap(True)
        adv_layout.addWidget(adv_note)
        adv_form = QFormLayout()
        self.seeds = QTextEdit()
        self.seeds.setPlaceholderText('One seed URL per line')
        self.seeds.setMaximumHeight(70)
        self.seeds.setPlainText(prefs.get('last_web_seeds') or '')
        adv_form.addRow('Seed URLs', self.seeds)
        self.full_list = QCheckBox(
            'These are the full link list (do not expand / follow links)'
        )
        self.full_list.setChecked(bool(prefs.get('last_web_full_list', False)))
        self.full_list.toggled.connect(self._sync_expand)
        adv_form.addRow(self.full_list)
        self.expand = QComboBox()
        self.expand.addItem('Same domain', 'same_domain')
        self.expand.addItem('Specific domains…', 'domains')
        self.expand.addItem('Free (any http/https link)', 'free')
        self.expand.addItem('None (seeds only)', 'none')
        exp = prefs.get('last_web_expand') or 'same_domain'
        eidx = self.expand.findData(exp)
        self.expand.setCurrentIndex(eidx if eidx >= 0 else 0)
        self.expand.currentIndexChanged.connect(self._sync_expand)
        adv_form.addRow('Link expansion', self.expand)
        self.domains = _form_line(
            'example.com, docs.example.com',
            prefs.get('last_web_domains') or '',
        )
        adv_form.addRow('Allowed domains', self.domains)
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
        adv_form.addRow(limits)
        adv_layout.addLayout(adv_form)
        compile_layout.addWidget(advanced)

        self.book_title = _form_line(
            'Optional book title',
            prefs.get('last_web_book_title') or '',
        )
        title_form = QFormLayout()
        title_form.addRow('Book title', self.book_title)
        compile_layout.addLayout(title_form)
        layout.addWidget(self.compile_box)

        # --- Single page ---
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
        single_note = QLabel(
            'Static HTML only. For JS sites, switch to multi-page and use '
            'Tampermonkey.'
        )
        single_note.setWordWrap(True)
        single_form.addRow(single_note)
        layout.addWidget(self.single_box)

        import_box = QGroupBox('Import options')
        import_form = QFormLayout(import_box)
        self.build_epub = QCheckBox('Build EPUB from extracted HTML')
        self.build_epub.setChecked(bool(prefs.get('web_build_epub', True)))
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
        return str(self.mode.currentData() or 'compile')

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
        enabled = self.static_box.isChecked() and not self.full_list.isChecked()
        self.expand.setEnabled(self.static_box.isChecked() and not self.full_list.isChecked())
        self.domains.setEnabled(enabled and is_domains)
        self.seeds.setEnabled(self.static_box.isChecked())
        self.full_list.setEnabled(self.static_box.isChecked())
        self.max_pages.setEnabled(self.static_box.isChecked())
        self.max_depth.setEnabled(self.static_box.isChecked())

    def _open_tampermonkey(self) -> None:
        QDesktopServices.openUrl(QUrl(TAMPERMONKEY_HOME))

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
        src = self._locate_userscript(USERSCRIPT_NAME)
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
            str(Path.home() / 'Downloads' / USERSCRIPT_NAME),
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
            'Saved userscript to:\n'
            f'{dest}\n\n'
            'Next: open that file in your browser (Tampermonkey should prompt '
            'to install). Then crawl pages and export a JSON bundle.',
            show=True,
        )
        try:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(dest).resolve())))
        except Exception:
            pass

    @staticmethod
    def _locate_userscript(name: str) -> Path | None:
        """Find the companion .user.js without importing crawl/requests."""
        plugin_root = Path(__file__).resolve().parents[2]
        local = plugin_root / 'resources' / 'webcompile' / name
        if local.is_file():
            return local
        try:
            from calibre_plugins.fanfic_organizer.runtime import (
                ensure_ao3kit_importable,
            )

            ensure_ao3kit_importable()
            from webcompile.userscript import resolve_userscript

            found = resolve_userscript(plugin_dir=plugin_root)
            if found is not None and Path(found).is_file():
                return Path(found)
        except Exception:
            pass
        return None

    def accept(self) -> None:
        self._sync_expand()
        values = self.values()
        if not web_import_is_usable(values):
            error_dialog(
                self,
                'Fanfic Organizer',
                'Choose a Tampermonkey crawl bundle, enter seed URLs for a '
                'static crawl, or (single-page mode) a URL / HTML file.',
                show=True,
            )
            return
        if (
            values.get('mode') == 'compile'
            and values.get('expand') == 'domains'
            and not values.get('domains')
            and not values.get('bundle_path')
            and not values.get('full_list')
            and values.get('use_static_crawl')
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
        use_static = bool(self.static_box.isChecked())
        seeds = []
        if use_static:
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
        full_list = bool(self.full_list.isChecked()) if use_static else False
        expand = 'none'
        if use_static:
            expand = (
                'none' if full_list else str(self.expand.currentData() or 'same_domain')
            )
        return {
            'mode': mode,
            'url': self.url.text().strip(),
            'html_path': self.html_path.text().strip(),
            'seeds': seeds,
            'use_static_crawl': use_static,
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
