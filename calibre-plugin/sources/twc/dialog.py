# -*- coding: utf-8 -*-
"""Import Transformative Works and Cultures (TWC) journal dialog."""

from __future__ import annotations

from PyQt5.Qt import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QVBoxLayout,
)

from calibre.gui2 import error_dialog

from calibre_plugins.fanfic_organizer.prefs import prefs
from calibre_plugins.fanfic_organizer.sources.twc.run import twc_import_is_usable


def _form_line(placeholder: str = '', text: str = '') -> QLineEdit:
    line = QLineEdit()
    if placeholder:
        line.setPlaceholderText(placeholder)
    if text:
        line.setText(text)
    return line


class TwcImportDialog(QDialog):
    """Import a TWC issue or single article into the open Calibre library."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle('Import from TWC journal')
        self.setMinimumWidth(480)
        self.resize(520, 320)

        layout = QVBoxLayout(self)
        intro = QLabel(
            'Paste a <b>Transformative Works and Cultures</b> issue or article URL '
            'from <i>journal.transformativeworks.org</i>. A whole issue imports every '
            'article (HTML galleys). Keywords become Tags; publisher is TWC. '
            'With Build EPUB on, each article is packed into an EPUB with a generated cover.'
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        form = QFormLayout()
        self.url = _form_line(
            'https://journal.transformativeworks.org/index.php/twc/issue/view/…',
            prefs.get('last_twc_url') or '',
        )
        form.addRow('Issue or article URL', self.url)
        layout.addLayout(form)

        self.build_epub = QCheckBox('Build EPUB from HTML galleys')
        self.build_epub.setChecked(bool(prefs.get('twc_build_epub', True)))
        layout.addWidget(self.build_epub)

        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel
        )
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _accept(self) -> None:
        if not twc_import_is_usable(self.values()):
            error_dialog(
                self,
                'TWC import',
                'Paste a TWC issue or article URL.',
                show=True,
            )
            return
        self.accept()

    def values(self) -> dict:
        return {
            'url': self.url.text().strip(),
            'download_epubs': self.build_epub.isChecked(),
            'update_existing': True,
        }
