# -*- coding: utf-8 -*-
"""Generic URL / saved-HTML import dialog."""

from __future__ import annotations

from PyQt5.Qt import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
)
from PyQt5.QtWidgets import QFileDialog

from calibre.gui2 import error_dialog

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
    """Fetch a URL or import browser-exported HTML into this library."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle('Import from URL or HTML')
        self.setMinimumWidth(520)
        self.resize(560, 480)

        layout = QVBoxLayout(self)
        intro = QLabel(
            'Import a <b>static HTML</b> page into the currently open library. '
            'Fanfic Organizer tries to extract title, author, summary, date, '
            'and main content, then builds an EPUB when enabled.'
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        warn = QLabel(
            '<b>Limitation:</b> this often fails on JavaScript-rendered '
            '(dynamic) sites. For those, use your browser’s '
            '<i>Save Page</i> / <i>Download page</i>, then choose the saved '
            'HTML file below (optionally set the original URL for the '
            'identifier).'
        )
        warn.setWordWrap(True)
        warn.setStyleSheet('color: #6a4a00;')
        layout.addWidget(warn)

        form = QFormLayout()
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
        form.addRow('Page URL', self.url)
        form.addRow('Saved HTML file', html_row)
        layout.addLayout(form)

        import_box = QGroupBox('Import options')
        import_form = QFormLayout(import_box)
        self.build_epub = QCheckBox('Build EPUB from extracted HTML')
        self.build_epub.setChecked(bool(prefs.get('web_build_epub', True)))
        self.build_epub.setToolTip(
            'Packs extracted article HTML into an EPUB under the job folder '
            'and attaches it in Calibre.'
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

    def accept(self) -> None:
        values = self.values()
        if not web_import_is_usable(values):
            error_dialog(
                self,
                'Fanfic Organizer',
                'Enter a page URL and/or choose a saved HTML file.',
                show=True,
            )
            return
        super().accept()

    def values(self) -> dict:
        return {
            'url': self.url.text().strip(),
            'html_path': self.html_path.text().strip(),
            'update_existing': self.update_existing.isChecked(),
            'download_epubs': self.build_epub.isChecked(),
            'simplify_tags': False,
            'drop_unmarked': False,
        }
