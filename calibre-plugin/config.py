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

from calibre_plugins.fanfic_organizer.columns import (
    LAYOUT_COLUMN_SPECS,
    apply_layout_columns,
    layout_columns_present,
)
from calibre_plugins.fanfic_organizer.prefs import prefs


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
            '#relationships, #collections, #summary, #wordcount, plus #originaltags '
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

        pacing = QGroupBox('AO3 request pacing')
        pacing_form = QFormLayout(pacing)
        pacing_values = self._load_pacing_settings()
        self.min_request_interval = QLineEdit()
        self.min_request_interval.setText(
            self._format_pacing_seconds(pacing_values.get('min_request_interval', 1.5))
        )
        self.min_request_interval.setPlaceholderText('1.5')
        self.min_request_interval.setToolTip(
            'Minimum seconds between AO3 work, search, download, and tag '
            'requests on this computer. Shared across the plugin and CLI via '
            'the host-wide rate limiter (XDG config min_request_interval). '
            'The limiter still backs off on 429 responses.'
        )
        pacing_form.addRow('Min request interval (s)', self.min_request_interval)
        self.tag_warm_interval = QLineEdit()
        self.tag_warm_interval.setText(
            self._format_pacing_seconds(pacing_values.get('tag_warm_interval', 10.0))
        )
        self.tag_warm_interval.setPlaceholderText('10')
        self.tag_warm_interval.setToolTip(
            'Extra pause after each tag-profile fetch during background tag '
            'cache warming (XDG config tag_warm_interval). Does not slow '
            'Search or Download when the warmer is idle.'
        )
        pacing_form.addRow('Tag cache warm interval (s)', self.tag_warm_interval)
        pacing_form.addRow(
            _hint(
                'Backoff & scaling — tune how the host-wide limiter speeds up '
                'and slows down. Stored under ``rate:`` in XDG config.yaml.'
            )
        )
        rate_values = pacing_values.get('rate') if isinstance(
            pacing_values.get('rate'), dict
        ) else {}
        self._rate_widgets: dict[str, QLineEdit] = {}
        rate_fields = [
            (
                'max_interval',
                'Max cruise interval (s)',
                '60',
                'Upper cap for work/search/download spacing after pressure backoff.',
            ),
            (
                'tag_max_interval',
                'Tag max interval (s)',
                '8',
                'Upper cap for tag-profile spacing after backoff.',
            ),
            (
                'jitter',
                'Timing jitter (± fraction)',
                '0.08',
                'Random spread applied to each wait (0 = none, 0.5 = ±50%).',
            ),
            (
                'retry_after_tag_multiplier',
                '429 tag backoff ×',
                '2',
                'Multiply tag interval after a 429 (Retry-After pause).',
            ),
            (
                'retry_after_tag_floor',
                '429 tag floor (s)',
                '2',
                'Minimum tag interval after a 429.',
            ),
            (
                'default_retry_after',
                'Default 429 pause (s)',
                '2',
                'Pause when AO3 omits Retry-After on tag fetches.',
            ),
            (
                'pressure_base_multiplier',
                'Pressure base backoff ×',
                '1.2',
                'Multiply work/search/download interval on 5xx / Cloudflare.',
            ),
            (
                'pressure_tag_multiplier',
                'Pressure tag backoff ×',
                '1.5',
                'Multiply tag interval on 5xx / Cloudflare.',
            ),
            (
                'pressure_floor',
                'Pressure floor (s)',
                '1.5',
                'Minimum interval after edge pressure.',
            ),
            (
                'success_streak',
                'Success streak to speed up',
                '8',
                'Healthy tag responses before easing the tag lane.',
            ),
            (
                'success_speed_factor',
                'Speed-up factor (× tag)',
                '0.85',
                'Multiply tag interval after a success streak (less than 1).',
            ),
        ]
        for key, label, placeholder, tooltip in rate_fields:
            widget = QLineEdit()
            widget.setPlaceholderText(placeholder)
            widget.setToolTip(tooltip)
            default = rate_values.get(key, placeholder)
            widget.setText(self._format_rate_value(key, default))
            self._rate_widgets[key] = widget
            pacing_form.addRow(label, widget)
        pacing_form.addRow(
            _hint(
                'CLI: python -m ao3kit config set rate.pressure_tag_multiplier 2'
            )
        )
        layout.addWidget(pacing)

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
            '(XDG config dir / fanfic-organizer / collections.yaml). Tag keep / rename / '
            'drop lives in mappings.yaml.'
        )
        defaults_form.addRow(self.simplify_tags)

        self.drop_unmarked = QCheckBox(
            'Drop non-canonical tags after mapping (default for simplify)'
        )
        self.drop_unmarked.setChecked(bool(prefs.get('drop_unmarked', True)))
        self.drop_unmarked.setToolTip(
            'When simplifying tags, fandoms, or relationships, remove tags '
            'that AO3 does not list as canonical or synonymous after your '
            'mapping rules run. Stored in XDG config (drop_unmarked). Search, '
            'import, and Process library dialogs can override this per run.'
        )
        defaults_form.addRow(self.drop_unmarked)

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
            'XDG config (collections_remember_manual_adds).'
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
            'as the AO3 cover tool). Style is stored in XDG config. '
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
            'Fields, colours, font, size, layout, and contrast. Fandom-seeded '
            'colours stay the same for every fic in that fandom.'
        )
        style_btn.clicked.connect(self.edit_cover_style)
        covers_form.addRow(self.generate_covers)
        covers_form.addRow(self.replace_covers)
        covers_form.addRow(self.set_calibre_cover)
        covers_form.addRow(style_btn)
        covers_form.addRow(
            _hint(
                'Default look is title + author on a dark fandom-coloured '
                'gradient (600×900, Georgia). Long titles shrink to fit. '
                'Click Cover style for layout, outline, overlay, and '
                'per-fandom colours.'
            )
        )
        layout.addWidget(covers)

        koreader = QGroupBox('KOReader (Kobo)')
        koreader_layout = QVBoxLayout(koreader)
        koreader_form = QFormLayout()
        self.koreader_path = QLineEdit()
        self.koreader_path.setText(str(prefs.get('koreader_path') or '.adds/koreader'))
        self.koreader_path.setPlaceholderText('.adds/koreader')
        self.koreader_path.setToolTip(
            'Folder on the device where KOReader stores settings and plugins.'
        )
        koreader_form.addRow('KOReader folder', self.koreader_path)
        koreader_layout.addLayout(koreader_form)
        koreader_layout.addWidget(
            _hint(
                'Optional. After Calibre finishes sending books to your Kobo, '
                'use Fanfic Organizer → Deploy to KOReader… (only enabled when '
                'the connected device is a Kobo or Android storage with KOReader '
                'already set up). Writes fanfic.collections.json from the '
                '#collections column. In KOReader: Search → Fanfic collections.'
            )
        )
        layout.addWidget(koreader)

        runtime = QGroupBox('Advanced (optional)')
        runtime_form = QFormLayout(runtime)
        self.ao3kit_project = QLineEdit()
        self.ao3kit_project.setText(prefs.get('ao3kit_project') or '')
        self.ao3kit_project.setPlaceholderText(
            'leave blank to use makeplugin install or the bundled zip'
        )
        runtime_form.addRow('Project path', self.ao3kit_project)
        runtime_form.addRow(
            _hint(
                'Leave blank unless you need to override. '
                '`python makeplugin.py install` records this checkout; the '
                'GitHub plugin zip already includes ao3kit. You can also set '
                'AO3KIT_PROJECT.'
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
                'Fanfic Organizer',
                'Enter both username and password, then click Test login.',
                show=True,
            )
            return

        from calibre_plugins.fanfic_organizer.enrich import EnrichCancelled, run_ao3kit
        from calibre_plugins.fanfic_organizer.prefs import prefs as plugin_prefs
        from calibre_plugins.fanfic_organizer.scrape_run import build_login_test_argv

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
            info_dialog(self, 'Fanfic Organizer', summary, show=True)
            return

        detail = (stderr or stdout or f'exit {code}').strip()
        self.login_status.setText('Login failed.')
        error_dialog(
            self,
            'Fanfic Organizer',
            'AO3 login failed. Check the username and password.',
            det_msg=detail,
            show=True,
        )

    def sizeHint(self):
        return QSize(560, 1180)

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
        prefs['drop_unmarked'] = self.drop_unmarked.isChecked()
        prefs['update_existing'] = self.update_existing.isChecked()
        prefs['import_full_series'] = self.import_full_series.isChecked()
        self._save_ao3kit_remember_adds(self.remember_collection_adds.isChecked())
        self._save_drop_unmarked(self.drop_unmarked.isChecked())
        self._save_pacing_settings()
        self._save_cover_settings()
        prefs['koreader_path'] = self.koreader_path.text().strip() or '.adds/koreader'
        self.create_layout.setChecked(False)
        self.refresh_status()

    def _ao3kit_remember_adds(self) -> bool:
        try:
            from calibre_plugins.fanfic_organizer.enrich import run_ao3kit

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
            from calibre_plugins.fanfic_organizer.enrich import run_ao3kit

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
                'Fanfic Organizer',
                'Could not save the collection recompute setting in ao3kit.',
                det_msg=str(exc),
                show=True,
            )
            return
        if code != 0:
            error_dialog(
                self,
                'Fanfic Organizer',
                'Could not save the collection recompute setting in ao3kit.',
                det_msg=(stderr or stdout or f'exit {code}').strip(),
                show=True,
            )

    def _save_drop_unmarked(self, drop: bool) -> None:
        try:
            from calibre_plugins.fanfic_organizer.enrich import run_ao3kit

            code, stdout, stderr = run_ao3kit(
                [
                    'config',
                    'set',
                    'drop_unmarked',
                    'true' if drop else 'false',
                ]
            )
        except Exception as exc:
            error_dialog(
                self,
                'Fanfic Organizer',
                'Could not save the drop-non-canonical setting in ao3kit.',
                det_msg=str(exc),
                show=True,
            )
            return
        if code != 0:
            error_dialog(
                self,
                'Fanfic Organizer',
                'Could not save the drop-non-canonical setting in ao3kit.',
                det_msg=(stderr or stdout or f'exit {code}').strip(),
                show=True,
            )

    def validate(self):
        username = self.username.text().strip()
        password = self.password.text()
        if (username and not password) or (password and not username):
            error_dialog(
                self,
                'Fanfic Organizer',
                'Both username and password are required to log in to AO3 '
                '(or leave both blank to use .env / anonymous).',
                show=True,
            )
            return False
        if not self._validate_pacing_fields():
            return False
        return True

    @staticmethod
    def _format_pacing_seconds(value: object) -> str:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return ''
        if number <= 0:
            return ''
        text = f'{number:g}'
        return text if text else ''

    def _parse_pacing_seconds(self, widget: QLineEdit, label: str) -> float | None:
        raw = widget.text().strip()
        if not raw:
            placeholder = widget.placeholderText().strip()
            raw = placeholder or '0'
        try:
            value = float(raw)
        except ValueError:
            error_dialog(
                self,
                'Fanfic Organizer',
                f'{label} must be a number of seconds.',
                show=True,
            )
            return None
        if value <= 0:
            error_dialog(
                self,
                'Fanfic Organizer',
                f'{label} must be greater than zero.',
                show=True,
            )
            return None
        return value

    @staticmethod
    def _format_rate_value(key: str, value: object) -> str:
        try:
            if key == 'success_streak':
                number = int(float(value))
                return str(number) if number > 0 else ''
            number = float(value)
        except (TypeError, ValueError):
            return ''
        if number <= 0:
            return ''
        if key == 'success_speed_factor' or key == 'jitter':
            text = f'{number:g}'
        else:
            text = f'{number:g}'
        return text if text else ''

    def _parse_rate_fields(self) -> dict[str, float | int] | None:
        parsed: dict[str, float | int] = {}
        int_keys = {'success_streak'}
        for key, widget in self._rate_widgets.items():
            label = key.replace('_', ' ')
            raw = widget.text().strip() or widget.placeholderText().strip()
            try:
                if key in int_keys:
                    value: float | int = int(float(raw))
                else:
                    value = float(raw)
            except ValueError:
                error_dialog(
                    self,
                    'Fanfic Organizer',
                    f'{label} must be a number.',
                    show=True,
                )
                return None
            if value <= 0:
                error_dialog(
                    self,
                    'Fanfic Organizer',
                    f'{label} must be greater than zero.',
                    show=True,
                )
                return None
            if key == 'success_speed_factor' and value > 1.0:
                error_dialog(
                    self,
                    'Fanfic Organizer',
                    'Speed-up factor must be at most 1 (multiply tag interval down).',
                    show=True,
                )
                return None
            if key == 'jitter' and value > 0.5:
                error_dialog(
                    self,
                    'Fanfic Organizer',
                    'Timing jitter must be at most 0.5 (±50%).',
                    show=True,
                )
                return None
            parsed[key] = value
        return parsed

    def _validate_pacing_fields(self) -> bool:
        return (
            self._parse_pacing_seconds(
                self.min_request_interval, 'Min request interval'
            )
            is not None
            and self._parse_pacing_seconds(
                self.tag_warm_interval, 'Tag cache warm interval'
            )
            is not None
            and self._parse_rate_fields() is not None
        )

    def _load_pacing_settings(self) -> dict:
        try:
            from calibre_plugins.fanfic_organizer.enrich import run_ao3kit

            code, stdout, _stderr = run_ao3kit(['config', 'show'])
            if code == 0 and (stdout or '').strip():
                data = json.loads(stdout)
                if isinstance(data, dict):
                    return data
        except Exception:
            pass
        return {
            'min_request_interval': 1.5,
            'tag_warm_interval': 10.0,
            'rate': {
                'tag_soft_interval': 1.5,
                'tag_max_interval': 8.0,
                'max_interval': 60.0,
                'jitter': 0.08,
                'retry_after_tag_multiplier': 2.0,
                'retry_after_tag_floor': 2.0,
                'default_retry_after': 2.0,
                'pressure_base_multiplier': 1.2,
                'pressure_tag_multiplier': 1.5,
                'pressure_floor': 1.5,
                'success_streak': 8,
                'success_speed_factor': 0.85,
            },
        }

    def _save_pacing_settings(self) -> None:
        min_interval = self._parse_pacing_seconds(
            self.min_request_interval, 'Min request interval'
        )
        warm_interval = self._parse_pacing_seconds(
            self.tag_warm_interval, 'Tag cache warm interval'
        )
        rate_values = self._parse_rate_fields()
        if min_interval is None or warm_interval is None or rate_values is None:
            return
        try:
            from calibre_plugins.fanfic_organizer.enrich import run_ao3kit

            for key, value in (
                ('min_request_interval', min_interval),
                ('tag_warm_interval', warm_interval),
            ):
                code, stdout, stderr = run_ao3kit(
                    ['config', 'set', key, f'{value:g}']
                )
                if code != 0:
                    error_dialog(
                        self,
                        'Fanfic Organizer',
                        'Could not save AO3 request pacing in ao3kit.',
                        det_msg=(stderr or stdout or f'exit {code}').strip(),
                        show=True,
                    )
                    return
            for key, value in rate_values.items():
                code, stdout, stderr = run_ao3kit(
                    ['config', 'set', f'rate.{key}', f'{value:g}']
                )
                if code != 0:
                    error_dialog(
                        self,
                        'Fanfic Organizer',
                        'Could not save AO3 rate limit settings in ao3kit.',
                        det_msg=(stderr or stdout or f'exit {code}').strip(),
                        show=True,
                    )
                    return
            # Refresh in-process limiter cache when ao3kit runs in this checkout.
            try:
                from ao3kit.rate import refresh_rate_settings_from_config

                refresh_rate_settings_from_config()
            except Exception:
                pass
        except Exception as exc:
            error_dialog(
                self,
                'Fanfic Organizer',
                'Could not save AO3 request pacing in ao3kit.',
                det_msg=str(exc),
                show=True,
            )

    def edit_cover_style(self) -> None:
        from calibre_plugins.fanfic_organizer.cover_ui import CoverStyleDialog

        dialog = CoverStyleDialog(self, self._cover_style)
        if not dialog.exec_():
            return
        self._cover_style.update(dialog.values())

    def _load_cover_settings(self) -> dict:
        from calibre_plugins.fanfic_organizer.cover_ui import load_cover_dict

        return load_cover_dict()

    def _save_cover_settings(self) -> None:
        from calibre_plugins.fanfic_organizer.cover_ui import save_cover_dict

        cover = dict(self._cover_style)
        cover['enabled'] = self.generate_covers.isChecked()
        cover['replace_existing'] = self.replace_covers.isChecked()
        cover['set_calibre_cover'] = self.set_calibre_cover.isChecked()
        try:
            code, stdout, stderr = save_cover_dict(cover)
        except Exception as exc:
            error_dialog(
                self,
                'Fanfic Organizer',
                'Could not save cover settings in ao3kit.',
                det_msg=str(exc),
                show=True,
            )
            return
        if code != 0:
            error_dialog(
                self,
                'Fanfic Organizer',
                'Could not save cover settings in ao3kit.',
                det_msg=(stderr or stdout or f'exit {code}').strip(),
                show=True,
            )
