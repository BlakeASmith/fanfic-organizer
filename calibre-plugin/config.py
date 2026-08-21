# -*- coding: utf-8 -*-

from PyQt5.Qt import QLabel, QLineEdit, QVBoxLayout, QWidget

from calibre_plugins.ao3_scraper.columns import (
    CLEANED_METADATA_LOOKUP,
    CLEANED_METADATA_NAME,
    RAW_METADATA_LOOKUP,
    RAW_METADATA_NAME,
    column_exists,
    ensure_plugin_columns,
)
from calibre_plugins.ao3_scraper.prefs import prefs


class ConfigWidget(QWidget):
    def __init__(self, plugin_action):
        super().__init__()
        self.plugin_action = plugin_action

        layout = QVBoxLayout()
        self.setLayout(layout)

        intro = QLabel(
            'On setup / import this plugin creates custom columns for '
            f'{RAW_METADATA_NAME} ({RAW_METADATA_LOOKUP}) and '
            f'{CLEANED_METADATA_NAME} ({CLEANED_METADATA_LOOKUP}). '
            'Tag simplification shells out to ao3kit (`tags enrich`).'
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        layout.addWidget(QLabel('ao3kit project path (folder containing ao3kit/)'))
        self.ao3kit_project = QLineEdit()
        self.ao3kit_project.setText(prefs.get('ao3kit_project') or '')
        self.ao3kit_project.setPlaceholderText('/Users/you/emily/ao3')
        layout.addWidget(self.ao3kit_project)

        layout.addWidget(QLabel('Python for ao3kit (optional; default: python3 on PATH)'))
        self.ao3kit_python = QLineEdit()
        self.ao3kit_python.setText(prefs.get('ao3kit_python') or '')
        self.ao3kit_python.setPlaceholderText('/path/to/python3')
        layout.addWidget(self.ao3kit_python)

        self.status = QLabel()
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

        self.refresh_status()

    def refresh_status(self):
        db = self.plugin_action.gui.current_db
        raw_ok = column_exists(db, RAW_METADATA_LOOKUP)
        cleaned_ok = column_exists(db, CLEANED_METADATA_LOOKUP)
        if raw_ok and cleaned_ok:
            self.status.setText(
                f'Setup complete. Columns {RAW_METADATA_LOOKUP} and '
                f'{CLEANED_METADATA_LOOKUP} are available.'
            )
        else:
            missing = []
            if not raw_ok:
                missing.append(RAW_METADATA_LOOKUP)
            if not cleaned_ok:
                missing.append(CLEANED_METADATA_LOOKUP)
            self.status.setText(
                'Missing column(s): '
                + ', '.join(missing)
                + '. Click OK to create them.'
            )

    def save_settings(self):
        db = self.plugin_action.gui.current_db
        ensure_plugin_columns(db)
        prefs['setup_complete'] = True
        prefs['ao3kit_project'] = self.ao3kit_project.text().strip()
        prefs['ao3kit_python'] = self.ao3kit_python.text().strip()
        self.refresh_status()

    def validate(self):
        return True
