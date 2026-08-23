# -*- coding: utf-8 -*-

import json

from PyQt5.Qt import (
    QApplication,
    QCheckBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSize,
    Qt,
    QVBoxLayout,
    QWidget,
)

from calibre.gui2 import error_dialog, info_dialog

from calibre_plugins.ao3_scraper.columns import (
    LAYOUT_COLUMN_SPECS,
    apply_layout_columns,
    layout_columns_present,
)
from calibre_plugins.ao3_scraper.prefs import prefs


def _echo_password(widget: QLineEdit) -> None:
    try:
        widget.setEchoMode(QLineEdit.Password)
    except AttributeError:
        widget.setEchoMode(QLineEdit.EchoMode.Password)


def _hint(text: str) -> QLabel:
    label = QLabel(text)
    label.setWordWrap(True)
    return label


class ConfigWidget(QWidget):
    def __init__(self, plugin_action):
        super().__init__()
        self.plugin_action = plugin_action

        layout = QVBoxLayout()
        self.setLayout(layout)

        intro = QLabel(
            'Use this plugin on a <b>new</b> Calibre library. Search AO3, '
            'Import, Download EPUB, Simplify, and Tag Purge write to whichever '
            'library is currently open.\n\n'
            'Search, download, and tag cleanup run from the toolkit bundled in '
            'this plugin (same scrape / download / enrich commands as the CLI, '
            'including the host-wide AO3 rate limiter). A git checkout is '
            'optional.\n\n'
            'Fanfic columns (same labels as FanFicFare): #fandom, '
            '#relationships, #collections, #wordcount, plus #originaltags '
            'for the pre-clean AO3 tags. Cleaned tags go in Calibre\'s Tags '
            'field. AO3 series membership fills Calibre\'s built-in Series '
            'column. Columns are created on import, or when you check the box '
            'below. Count Pages columns (page count / readability) are left '
            'to that plugin.'
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        library = QGroupBox('Library')
        library_layout = QVBoxLayout(library)
        self.create_layout = QCheckBox(
            'Create missing fanfic columns in the currently open library'
        )
        self.create_layout.setChecked(False)
        self.create_layout.setToolTip(
            'Safe if the columns already exist (no-op). Use this on a new '
            'empty library before importing, or let the first import create them.'
        )
        library_layout.addWidget(self.create_layout)
        self.status = QLabel()
        self.status.setWordWrap(True)
        library_layout.addWidget(self.status)
        layout.addWidget(library)

        login = QGroupBox('AO3 login (optional)')
        login_form = QFormLayout(login)
        self.username = QLineEdit()
        self.username.setText(str(prefs.get('ao3_username') or ''))
        self.username.setPlaceholderText('or AO3_USERNAME in ao3kit .env')
        self.password = QLineEdit()
        _echo_password(self.password)
        self.password.setText(str(prefs.get('ao3_password') or ''))
        self.password.setPlaceholderText('or AO3_PASSWORD in ao3kit .env')
        pwd_row = QWidget()
        pwd_layout = QHBoxLayout(pwd_row)
        pwd_layout.setContentsMargins(0, 0, 0, 0)
        pwd_layout.addWidget(self.password)
        self.test_login_btn = QPushButton('Test login')
        self.test_login_btn.setToolTip(
            'Log in to AO3 with the username and password above (does not '
            'save settings). Uses the bundled toolkit (or the checkout below).'
        )
        self.test_login_btn.clicked.connect(self.test_login)
        pwd_layout.addWidget(self.test_login_btn)
        login_form.addRow('Username', self.username)
        login_form.addRow('Password', pwd_row)
        self.login_status = QLabel('')
        self.login_status.setWordWrap(True)
        login_form.addRow(self.login_status)
        login_form.addRow(
            _hint(
                'Used for Search AO3, EPUB download, tag simplification, and '
                'background tag cache. Leave both blank for anonymous access, '
                'or to use credentials from the ao3kit project .env file. '
                'Stored in this plugin\'s Calibre preferences. Click Test '
                'login to confirm the account before saving.'
            )
        )
        layout.addWidget(login)

        defaults = QGroupBox('Search and import defaults')
        defaults_form = QFormLayout(defaults)
        self.max_results = QLineEdit()
        self.max_results.setText(str(prefs.get('last_max_results') or '25'))
        self.max_results.setPlaceholderText('25')
        self.max_results.setToolTip(
            'Default cap for Search AO3. The search dialog still lets you '
            'change this per run.'
        )
        defaults_form.addRow('Default max results', self.max_results)

        self.download_epubs = QCheckBox('Download native EPUBs into this library')
        self.download_epubs.setChecked(bool(prefs.get('download_epubs', True)))
        self.download_epubs.setToolTip(
            'Default for Search AO3. Uncheck in the search dialog for a '
            'one-off metadata-only import.'
        )
        defaults_form.addRow(self.download_epubs)

        self.simplify_tags = QCheckBox(
            'Simplify tags, fandoms & relationships (AO3 canonical + user rules)'
        )
        self.simplify_tags.setChecked(bool(prefs.get('simplify_tags', False)))
        self.simplify_tags.setToolTip(
            'Default for Search AO3 and JSONL/zip import. Needs network '
            'access for uncached tags. Collapses AO3 '
            'synonyms on Tags, Fandom, and Relationships, and appends fandom '
            'metatags to the Fandom column (e.g. Marvel for Spider-Man). Extra '
            'collection rules are under Tags and collections → '
            'Collections & tag rules in the plugin menu '
            '(.ao3kit/collections.yaml). Tag keep / rename / '
            'drop lives in mappings.yaml.'
        )
        defaults_form.addRow(self.simplify_tags)

        self.update_existing = QCheckBox(
            'Update existing books matched by AO3 work id or URL'
        )
        self.update_existing.setChecked(bool(prefs.get('update_existing', True)))
        defaults_form.addRow(self.update_existing)

        self.import_full_series = QCheckBox(
            'Always import the rest of the series when adding a series work'
        )
        self.import_full_series.setChecked(bool(prefs.get('import_full_series', False)))
        self.import_full_series.setToolTip(
            'Whenever Search AO3, Search similar, a series URL, or JSONL/zip '
            'import adds a work that is part of an AO3 series, also fetch and '
            'import every other part. Search filters do not apply to those '
            'extra works. Can add many books and EPUB downloads.'
        )
        defaults_form.addRow(self.import_full_series)

        self.remember_collection_adds = QCheckBox(
            'When recomputing collections, keep hand-added membership as per-work rules'
        )
        self.remember_collection_adds.setChecked(self._ao3kit_remember_adds())
        self.remember_collection_adds.setToolTip(
            'If you add a book to a collection in Calibre, the next recompute '
            'saves that as a rule for that work so it stays. Turn off to treat '
            'the Collections column as a computed view only. Stored in '
            '.ao3kit/config.yaml (collections_remember_manual_adds).'
        )
        defaults_form.addRow(self.remember_collection_adds)
        layout.addWidget(defaults)

        covers = QGroupBox('EPUB covers')
        covers_form = QFormLayout(covers)
        cover = self._load_cover_settings()
        self.generate_covers = QCheckBox(
            'Generate covers when downloading native EPUBs'
        )
        self.generate_covers.setChecked(bool(cover.get('enabled', True)))
        self.generate_covers.setToolTip(
            'Stamps a title/author cover into each downloaded EPUB (same idea '
            'as the AO3 cover tool). Style is stored in .ao3kit/config.yaml. '
            'Selected books → Generate covers restamps library files.'
        )
        self.replace_covers = QCheckBox('Replace a cover already in the EPUB')
        self.replace_covers.setChecked(bool(cover.get('replace_existing', True)))
        self.set_calibre_cover = QCheckBox(
            'Set the Calibre book cover from the generated image'
        )
        self.set_calibre_cover.setChecked(bool(cover.get('set_calibre_cover', True)))
        self._cover_style = dict(cover)
        style_btn = QPushButton('Cover style…')
        style_btn.setToolTip(
            'Fields, colours, font, and size. Fandom-seeded colours stay the '
            'same for every fic in that fandom.'
        )
        style_btn.clicked.connect(self.edit_cover_style)
        covers_form.addRow(self.generate_covers)
        covers_form.addRow(self.replace_covers)
        covers_form.addRow(self.set_calibre_cover)
        covers_form.addRow(style_btn)
        covers_form.addRow(
            _hint(
                'Default look is title + author on a dark fandom-coloured '
                'gradient (600×900, Georgia). Click Cover style to show '
                'fandom/relationship lines, pick a palette, or pin colours '
                'per fandom.'
            )
        )
        layout.addWidget(covers)

        runtime = QGroupBox('Advanced (optional)')
        runtime_form = QFormLayout(runtime)
        self.ao3kit_project = QLineEdit()
        self.ao3kit_project.setText(prefs.get('ao3kit_project') or '')
        self.ao3kit_project.setPlaceholderText('leave blank to use the bundled toolkit')
        runtime_form.addRow('Project path', self.ao3kit_project)
        runtime_form.addRow(
            _hint(
                'Leave blank unless you are developing from a git checkout. '
                'The GitHub plugin zip already includes ao3kit.'
            )
        )
        self.ao3kit_python = QLineEdit()
        self.ao3kit_python.setText(prefs.get('ao3kit_python') or '')
        self.ao3kit_python.setPlaceholderText('leave blank to use Calibre\'s Python')
        runtime_form.addRow('Python', self.ao3kit_python)
        runtime_form.addRow(
            _hint(
                'Optional. Default is Calibre\'s calibre-debug (bundled zip) '
                'or python3 on PATH / the project venv (checkout).'
            )
        )
        layout.addWidget(runtime)

        self.refresh_status()

    def test_login(self) -> None:
        username = self.username.text().strip()
        password = self.password.text()
        if not username or not password:
            error_dialog(
                self,
                'AO3 Scraper',
                'Enter both username and password, then click Test login.',
                show=True,
            )
            return

        from calibre_plugins.ao3_scraper.enrich import EnrichCancelled, run_ao3kit
        from calibre_plugins.ao3_scraper.prefs import prefs as plugin_prefs
        from calibre_plugins.ao3_scraper.scrape_run import build_login_test_argv

        saved_project = plugin_prefs.get('ao3kit_project') or ''
        saved_python = plugin_prefs.get('ao3kit_python') or ''
        plugin_prefs['ao3kit_project'] = self.ao3kit_project.text().strip()
        plugin_prefs['ao3kit_python'] = self.ao3kit_python.text().strip()

        self.login_status.setText('Testing AO3 login…')
        self.test_login_btn.setEnabled(False)
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            code, stdout, stderr = run_ao3kit(
                build_login_test_argv(username, password),
            )
        except EnrichCancelled:
            self.login_status.setText('')
            return
        finally:
            plugin_prefs['ao3kit_project'] = saved_project
            plugin_prefs['ao3kit_python'] = saved_python
            QApplication.restoreOverrideCursor()
            self.test_login_btn.setEnabled(True)

        if code == 0:
            summary = f'Logged in as {username}.'
            self.login_status.setText(summary)
            info_dialog(self, 'AO3 Scraper', summary, show=True)
            return

        detail = (stderr or stdout or f'exit {code}').strip()
        self.login_status.setText('Login failed.')
        error_dialog(
            self,
            'AO3 Scraper',
            'AO3 login failed. Check the username and password.',
            det_msg=detail,
            show=True,
        )

    def sizeHint(self):
        return QSize(560, 980)

    def refresh_status(self):
        db = self.plugin_action.gui.current_db
        present = layout_columns_present(db)
        parts = []
        for spec in LAYOUT_COLUMN_SPECS:
            mark = 'yes' if present.get(spec['role']) else 'missing'
            parts.append(f"{spec['lookup']} {mark}")
        self.status.setText('Current library: ' + ', '.join(parts) + '.')

    def save_settings(self):
        gui = self.plugin_action.gui
        if self.create_layout.isChecked():
            apply_layout_columns(gui)
        prefs['setup_complete'] = True
        prefs['ao3kit_project'] = self.ao3kit_project.text().strip()
        prefs['ao3kit_python'] = self.ao3kit_python.text().strip()
        prefs['ao3_username'] = self.username.text().strip()
        prefs['ao3_password'] = self.password.text()
        prefs['last_max_results'] = self.max_results.text().strip() or '25'
        prefs['download_epubs'] = self.download_epubs.isChecked()
        prefs['simplify_tags'] = self.simplify_tags.isChecked()
        prefs['update_existing'] = self.update_existing.isChecked()
        prefs['import_full_series'] = self.import_full_series.isChecked()
        self._save_ao3kit_remember_adds(self.remember_collection_adds.isChecked())
        self._save_cover_settings()
        self.create_layout.setChecked(False)
        self.refresh_status()

    def _ao3kit_remember_adds(self) -> bool:
        try:
            from calibre_plugins.ao3_scraper.enrich import run_ao3kit

            code, stdout, _stderr = run_ao3kit(['config', 'show'])
            if code == 0 and (stdout or '').strip():
                data = json.loads(stdout)
                if isinstance(data, dict):
                    return bool(data.get('collections_remember_manual_adds', True))
        except Exception:
            pass
        return True

    def _save_ao3kit_remember_adds(self, remember: bool) -> None:
        try:
            from calibre_plugins.ao3_scraper.enrich import run_ao3kit

            code, stdout, stderr = run_ao3kit(
                [
                    'config',
                    'set',
                    'collections_remember_manual_adds',
                    'true' if remember else 'false',
                ]
            )
        except Exception as exc:
            error_dialog(
                self,
                'AO3 Scraper',
                'Could not save the collection recompute setting in ao3kit.',
                det_msg=str(exc),
                show=True,
            )
            return
        if code != 0:
            error_dialog(
                self,
                'AO3 Scraper',
                'Could not save the collection recompute setting in ao3kit.',
                det_msg=(stderr or stdout or f'exit {code}').strip(),
                show=True,
            )

    def validate(self):
        username = self.username.text().strip()
        password = self.password.text()
        if (username and not password) or (password and not username):
            error_dialog(
                self,
                'AO3 Scraper',
                'Both username and password are required to log in to AO3 '
                '(or leave both blank to use .env / anonymous).',
                show=True,
            )
            return False
        return True

    def edit_cover_style(self) -> None:
        from calibre_plugins.ao3_scraper.cover_ui import CoverStyleDialog

        dialog = CoverStyleDialog(self, self._cover_style)
        if not dialog.exec_():
            return
        self._cover_style.update(dialog.values())

    def _load_cover_settings(self) -> dict:
        from calibre_plugins.ao3_scraper.cover_ui import load_cover_dict

        return load_cover_dict()

    def _save_cover_settings(self) -> None:
        from calibre_plugins.ao3_scraper.cover_ui import save_cover_dict

        cover = dict(self._cover_style)
        cover['enabled'] = self.generate_covers.isChecked()
        cover['replace_existing'] = self.replace_covers.isChecked()
        cover['set_calibre_cover'] = self.set_calibre_cover.isChecked()
        try:
            code, stdout, stderr = save_cover_dict(cover)
        except Exception as exc:
            error_dialog(
                self,
                'AO3 Scraper',
                'Could not save cover settings in ao3kit.',
                det_msg=str(exc),
                show=True,
            )
            return
        if code != 0:
            error_dialog(
                self,
                'AO3 Scraper',
                'Could not save cover settings in ao3kit.',
                det_msg=(stderr or stdout or f'exit {code}').strip(),
                show=True,
            )
