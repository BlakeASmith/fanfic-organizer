# -*- coding: utf-8 -*-

from __future__ import annotations

from PyQt5.Qt import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
)
from PyQt5.QtWidgets import QFileDialog

from calibre_plugins.ao3_scraper.prefs import prefs


class ImportJsonlDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle('Import AO3 JSONL or zip')
        self.setMinimumWidth(520)

        layout = QVBoxLayout()
        self.setLayout(layout)

        intro = QLabel(
            'Import a JSONL scrape or an ao3-import.zip from download_epubs.py. '
            'The zip carries AO3 native EPUB files named by work id.'
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

        self.update_existing = QCheckBox('Update existing books matched by AO3 work id')
        self.update_existing.setChecked(True)
        layout.addWidget(self.update_existing)

        self.simplify_tags = QCheckBox(
            'Simplify tags (AO3 canonical + user rules via ao3kit)'
        )
        self.simplify_tags.setChecked(bool(prefs.get('simplify_tags', True)))
        self.simplify_tags.setToolTip(
            'Runs `python -m ao3kit tags enrich` using AO3KIT_HOME / ao3kit_project. '
            'Requires the ao3kit checkout and network access for uncached tags.'
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
