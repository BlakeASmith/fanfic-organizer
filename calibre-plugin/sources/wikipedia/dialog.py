# -*- coding: utf-8 -*-
"""Wikipedia search / fetch import dialog."""

from __future__ import annotations

from PyQt5.Qt import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QLabel,
    QLineEdit,
    QVBoxLayout,
)

from calibre.gui2 import error_dialog

from calibre_plugins.fanfic_organizer.prefs import prefs
from calibre_plugins.fanfic_organizer.sources.wikipedia.run import (
    wikipedia_search_is_usable,
)


def _form_line(placeholder: str = '', text: str = '') -> QLineEdit:
    line = QLineEdit()
    if placeholder:
        line.setPlaceholderText(placeholder)
    if text:
        line.setText(text)
    return line


class WikipediaSearchDialog(QDialog):
    """Search or fetch Wikipedia articles and import into this library."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle('Import from Wikipedia')
        self.setMinimumWidth(480)
        self.resize(520, 440)

        layout = QVBoxLayout(self)
        intro = QLabel(
            'Search Wikipedia or paste an article URL, then import matches '
            'into the <b>currently open</b> Calibre library. Articles get a '
            'Wikipedia identifier and publisher; categories become Tags. '
            'With Build EPUB on, each article is rendered to an EPUB '
            '(MediaWiki HTML) and stamped with a generated cover. Wiki links open in a browser when online.'
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        form = QFormLayout()
        self.query = _form_line(
            'Doctor Who',
            prefs.get('last_wikipedia_query') or '',
        )
        self.url = _form_line(
            'https://en.wikipedia.org/wiki/…',
            prefs.get('last_wikipedia_url') or '',
        )
        self.lang = _form_line('en', prefs.get('last_wikipedia_lang') or 'en')
        self.max_results = _form_line(
            '25',
            str(prefs.get('last_wikipedia_max_results') or '25'),
        )
        form.addRow('Search query', self.query)
        form.addRow('Article URL', self.url)
        form.addRow('Language', self.lang)
        form.addRow('Max results', self.max_results)
        layout.addLayout(form)

        import_box = QGroupBox('Import options')
        import_form = QFormLayout(import_box)
        self.build_epub = QCheckBox('Build EPUB from article HTML')
        self.build_epub.setChecked(
            bool(prefs.get('wikipedia_build_epub', True))
        )
        self.build_epub.setToolTip(
            'Fetches the rendered article and packs it into an EPUB under '
            'the job folder, then attaches it in Calibre.'
        )
        self.epub_images = QCheckBox('Include images in EPUB')
        self.epub_images.setChecked(
            bool(prefs.get('wikipedia_epub_images', False))
        )
        self.epub_images.setToolTip(
            'Download Wikimedia thumbnails into the EPUB for offline reading. '
            'Slower and larger files.'
        )
        self.update_existing = QCheckBox(
            'Update existing books (same Wikipedia id)'
        )
        self.update_existing.setChecked(bool(prefs.get('update_existing', True)))
        import_form.addRow(self.build_epub)
        import_form.addRow(self.epub_images)
        import_form.addRow(self.update_existing)
        layout.addWidget(import_box)

        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel
        )
        ok_btn = buttons.button(QDialogButtonBox.Ok)
        if ok_btn is not None:
            ok_btn.setText('Search and import')
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def accept(self) -> None:
        values = self.values()
        if not wikipedia_search_is_usable(values):
            error_dialog(
                self,
                'Fanfic Organizer',
                'Enter a search query or paste a Wikipedia article URL.',
                show=True,
            )
            return
        super().accept()

    def values(self) -> dict:
        return {
            'query': self.query.text().strip(),
            'url': self.url.text().strip(),
            'lang': self.lang.text().strip() or 'en',
            'max_results': self.max_results.text().strip(),
            'update_existing': self.update_existing.isChecked(),
            'download_epubs': self.build_epub.isChecked(),
            'wikipedia_epub_images': self.epub_images.isChecked(),
            'simplify_tags': False,
            'drop_unmarked': False,
        }
