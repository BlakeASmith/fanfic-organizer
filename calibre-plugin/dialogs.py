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
        self.setWindowTitle('Import AO3 JSONL')
        self.setMinimumWidth(520)

        layout = QVBoxLayout()
        self.setLayout(layout)

        intro = QLabel(
            'Import works from a JSONL file created by scrape_ao3.py or the web UI. '
            'Run your search outside Calibre first, then bring the results file here.'
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        path_row = QHBoxLayout()
        self.path = QLineEdit()
        self.path.setPlaceholderText('/path/to/results.jsonl')
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

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def browse(self):
        start = self.path.text().strip() or prefs['last_jsonl_path']
        path, _ = QFileDialog.getOpenFileName(
            self,
            'Select AO3 JSONL file',
            start,
            'JSON Lines (*.jsonl);;All files (*)',
        )
        if path:
            self.path.setText(path)

    def values(self) -> dict:
        return {
            'path': self.path.text().strip(),
            'update_existing': self.update_existing.isChecked(),
        }
