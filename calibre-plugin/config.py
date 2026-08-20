# -*- coding: utf-8 -*-

from PyQt5.Qt import QLabel, QVBoxLayout, QWidget

from calibre_plugins.ao3_scraper.columns import (
    RAW_METADATA_LOOKUP,
    RAW_METADATA_NAME,
    column_exists,
    ensure_raw_metadata_column,
)
from calibre_plugins.ao3_scraper.prefs import prefs


class ConfigWidget(QWidget):
    def __init__(self, plugin_action):
        super().__init__()
        self.plugin_action = plugin_action

        layout = QVBoxLayout()
        self.setLayout(layout)

        intro = QLabel(
            'On first setup this plugin creates the AO3 Raw Metadata custom column '
            f'({RAW_METADATA_LOOKUP}). Import JSONL files prepared outside Calibre '
            'with scrape_ao3.py or the web UI.'
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        self.status = QLabel()
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

        self.refresh_status()

    def refresh_status(self):
        db = self.plugin_action.gui.current_db
        if column_exists(db):
            self.status.setText(
                f'Setup complete. Raw metadata column {RAW_METADATA_LOOKUP} '
                f'({RAW_METADATA_NAME}) is available.'
            )
        else:
            self.status.setText(
                f'Raw metadata column {RAW_METADATA_LOOKUP} has not been created yet. '
                'Click OK to create it.'
            )

    def save_settings(self):
        db = self.plugin_action.gui.current_db
        ensure_raw_metadata_column(db)
        prefs['setup_complete'] = True
        self.refresh_status()

    def validate(self):
        return True
