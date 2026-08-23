# -*- coding: utf-8 -*-
"""Cover style dialog for the Calibre plugin (reads/writes ao3kit cover config)."""

from __future__ import annotations

import json
from typing import Any

from PyQt5.Qt import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPixmap,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    Qt,
    QVBoxLayout,
    QWidget,
)

from calibre.gui2 import error_dialog

from calibre_plugins.wranglekit.prefs import prefs

COVER_FIELD_LABELS = (
    ('title', 'Title'),
    ('author', 'Author'),
    ('fandom', 'Fandom'),
    ('relationship', 'Relationship'),
    ('series', 'Series'),
    ('rating', 'Rating'),
    ('wordcount', 'Word count'),
    ('score', 'Quality score'),
    ('complete', 'Complete / WIP'),
)

COLOR_SEEDS = (
    ('fandom', 'Fandom (same colour for a fandom)'),
    ('relationship', 'Relationship'),
    ('author', 'Author'),
    ('title', 'Title'),
    ('work_id', 'AO3 work id'),
)

COLOR_MODES = (
    ('hash', 'Hash (random-looking, stable per seed)'),
    ('palette', 'Palette (pick from the list below)'),
    ('solid', 'Solid colour'),
)


def default_cover_dict() -> dict[str, Any]:
    return {
        'enabled': True,
        'replace_existing': True,
        'set_calibre_cover': True,
        'fields': ['title', 'author', 'wordcount', 'score'],
        'color_seed': 'fandom',
        'color_mode': 'hash',
        'gradient': True,
        'solid_color': '#2c3e6b',
        'palette': [],
        'fandom_colors': {},
        'width': 600,
        'height': 900,
        'font': 'Georgia',
        'title_size': 88,
        'author_size': 62,
        'uppercase_title': False,
        'text_shadow': False,
    }


def load_cover_dict() -> dict[str, Any]:
    data = default_cover_dict()
    try:
        from calibre_plugins.wranglekit.enrich import run_ao3kit

        code, stdout, _stderr = run_ao3kit(['config', 'show'])
        if code == 0 and (stdout or '').strip():
            payload = json.loads(stdout)
            cover = payload.get('cover') if isinstance(payload, dict) else None
            if isinstance(cover, dict):
                data.update(cover)
                return data
    except Exception:
        pass
    data['enabled'] = bool(prefs.get('generate_covers', True))
    data['set_calibre_cover'] = bool(prefs.get('set_calibre_cover', True))
    return data


def save_cover_dict(cover: dict[str, Any]) -> tuple[int, str, str]:
    from calibre_plugins.wranglekit.enrich import run_ao3kit

    prefs['generate_covers'] = bool(cover.get('enabled', True))
    prefs['set_calibre_cover'] = bool(cover.get('set_calibre_cover', True))
    return run_ao3kit(['config', 'merge', json.dumps({'cover': cover})])


def format_fandom_colors(mapping: Any) -> str:
    if not isinstance(mapping, dict) or not mapping:
        return ''
    return '\n'.join(f'{name} = {color}' for name, color in mapping.items())


def parse_fandom_colors(text: str) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for raw in (text or '').splitlines():
        line = raw.strip()
        if not line or line.startswith('#'):
            continue
        if '=' in line:
            name, color = line.split('=', 1)
        elif ':' in line:
            name, color = line.split(':', 1)
        else:
            continue
        name = name.strip()
        color = color.strip()
        if name and color:
            mapping[name] = color
    return mapping


def _combo(options: tuple[tuple[str, str], ...], current: str) -> QComboBox:
    combo = QComboBox()
    for value, label in options:
        combo.addItem(label, value)
    idx = combo.findData(current)
    if idx >= 0:
        combo.setCurrentIndex(idx)
    return combo


class CoverStyleDialog(QDialog):
    def __init__(self, parent=None, cover: dict[str, Any] | None = None):
        super().__init__(parent)
        self.setWindowTitle('Cover style')
        self.setMinimumWidth(520)
        self._cover = dict(default_cover_dict())
        if cover:
            self._cover.update(cover)

        layout = QVBoxLayout(self)
        intro = QLabel(
            'Covers match the AO3 cover tool: title and author on a dark '
            'gradient whose colour is stable for a fandom. Extra lines, '
            'fonts, sizes, and colour overrides are optional.'
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        fields_box = QGroupBox('Show on cover')
        fields_layout = QVBoxLayout(fields_box)
        current_fields = {
            str(name).lower() for name in (self._cover.get('fields') or ['title', 'author'])
        }
        self.field_boxes: dict[str, QCheckBox] = {}
        for key, label in COVER_FIELD_LABELS:
            box = QCheckBox(label)
            box.setChecked(key in current_fields)
            self.field_boxes[key] = box
            fields_layout.addWidget(box)
        layout.addWidget(fields_box)

        colour = QGroupBox('Colour')
        colour_form = QFormLayout(colour)
        self.color_seed = _combo(COLOR_SEEDS, str(self._cover.get('color_seed') or 'fandom'))
        self.color_mode = _combo(COLOR_MODES, str(self._cover.get('color_mode') or 'hash'))
        self.gradient = QCheckBox('Vertical gradient')
        self.gradient.setChecked(bool(self._cover.get('gradient', True)))
        self.solid_color = QLineEdit(str(self._cover.get('solid_color') or '#2c3e6b'))
        self.solid_color.setPlaceholderText('#2c3e6b')
        palette = self._cover.get('palette') or []
        if isinstance(palette, list):
            palette_text = ', '.join(str(item) for item in palette)
        else:
            palette_text = str(palette)
        self.palette = QLineEdit(palette_text)
        self.palette.setPlaceholderText('#7a1f1f, #1f4b7a, #2d6a4f')
        self.fandom_colors = QPlainTextEdit()
        self.fandom_colors.setPlaceholderText(
            'Star Wars = #c41e3a\nHarry Potter = #740001'
        )
        self.fandom_colors.setPlainText(
            format_fandom_colors(self._cover.get('fandom_colors'))
        )
        self.fandom_colors.setMaximumHeight(90)
        colour_form.addRow('Colour from', self.color_seed)
        colour_form.addRow('Mode', self.color_mode)
        colour_form.addRow(self.gradient)
        colour_form.addRow('Solid colour', self.solid_color)
        colour_form.addRow('Palette', self.palette)
        colour_form.addRow('Fandom colours', self.fandom_colors)
        layout.addWidget(colour)

        type_box = QGroupBox('Type and size')
        type_form = QFormLayout(type_box)
        self.font = QLineEdit(str(self._cover.get('font') or 'Georgia'))
        self.width = QSpinBox()
        self.width.setRange(200, 2400)
        self.width.setValue(int(self._cover.get('width') or 600))
        self.height = QSpinBox()
        self.height.setRange(300, 3600)
        self.height.setValue(int(self._cover.get('height') or 900))
        self.title_size = QSpinBox()
        self.title_size.setRange(16, 200)
        self.title_size.setValue(int(self._cover.get('title_size') or 88))
        self.author_size = QSpinBox()
        self.author_size.setRange(12, 160)
        self.author_size.setValue(int(self._cover.get('author_size') or 62))
        self.uppercase_title = QCheckBox('Uppercase title')
        self.uppercase_title.setChecked(bool(self._cover.get('uppercase_title')))
        self.text_shadow = QCheckBox('Text shadow')
        self.text_shadow.setChecked(bool(self._cover.get('text_shadow')))
        size_row = QWidget()
        size_layout = QHBoxLayout(size_row)
        size_layout.setContentsMargins(0, 0, 0, 0)
        size_layout.addWidget(QLabel('W'))
        size_layout.addWidget(self.width)
        size_layout.addWidget(QLabel('H'))
        size_layout.addWidget(self.height)
        size_layout.addStretch(1)
        type_form.addRow('Font', self.font)
        type_form.addRow('Size (px)', size_row)
        type_form.addRow('Title size', self.title_size)
        type_form.addRow('Author size', self.author_size)
        type_form.addRow(self.uppercase_title)
        type_form.addRow(self.text_shadow)
        layout.addWidget(type_box)

        preview_row = QHBoxLayout()
        self.preview_btn = QPushButton('Preview sample…')
        self.preview_btn.clicked.connect(self.preview)
        preview_row.addWidget(self.preview_btn)
        preview_row.addStretch(1)
        layout.addLayout(preview_row)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def values(self) -> dict[str, Any]:
        fields = [key for key, box in self.field_boxes.items() if box.isChecked()]
        if 'title' not in fields:
            fields.insert(0, 'title')
        palette = [
            part.strip()
            for part in self.palette.text().split(',')
            if part.strip()
        ]
        return {
            'fields': fields,
            'color_seed': self.color_seed.currentData(),
            'color_mode': self.color_mode.currentData(),
            'gradient': self.gradient.isChecked(),
            'solid_color': self.solid_color.text().strip() or '#2c3e6b',
            'palette': palette,
            'fandom_colors': parse_fandom_colors(self.fandom_colors.toPlainText()),
            'font': self.font.text().strip() or 'Georgia',
            'width': int(self.width.value()),
            'height': int(self.height.value()),
            'title_size': int(self.title_size.value()),
            'author_size': int(self.author_size.value()),
            'uppercase_title': self.uppercase_title.isChecked(),
            'text_shadow': self.text_shadow.isChecked(),
        }

    def preview(self) -> None:
        import tempfile
        from pathlib import Path

        from calibre_plugins.wranglekit.enrich import EnrichCancelled, run_ao3kit

        values = self.values()
        tmp = Path(tempfile.mkdtemp(prefix='ao3-cover-')) / 'preview.png'
        argv = [
            'cover',
            '--preview',
            '--title',
            'Yet Another Coffee Shop AU',
            '--author',
            'Jane AUs-ten',
            '--fandom',
            'Star Wars',
            '--relationship',
            'Rey/Ben Solo',
            '--wordcount',
            '125000',
            '--score',
            '72',
            '--fields',
            ','.join(values['fields']),
            '--color-seed',
            str(values['color_seed']),
            '--color-mode',
            str(values['color_mode']),
            '--color',
            str(values['solid_color']),
            '--font',
            str(values['font']),
            '--width',
            str(values['width']),
            '--height',
            str(values['height']),
            '--gradient' if values['gradient'] else '--no-gradient',
            '--uppercase-title' if values['uppercase_title'] else '--no-uppercase-title',
            '--text-shadow' if values['text_shadow'] else '--no-text-shadow',
            '-o',
            str(tmp),
        ]
        if values['palette']:
            argv.extend(['--palette', ','.join(values['palette'])])
        try:
            code, stdout, stderr = run_ao3kit(argv)
        except EnrichCancelled:
            return
        if code != 0 or not tmp.is_file():
            error_dialog(
                self,
                'Wranglekit',
                'Could not render a sample cover.',
                det_msg=(stderr or stdout or f'exit {code}').strip(),
                show=True,
            )
            return
        pix = QPixmap(str(tmp))
        if pix.isNull():
            error_dialog(
                self,
                'Wranglekit',
                'Wrote a sample cover but could not display it.',
                show=True,
            )
            return
        dlg = QDialog(self)
        dlg.setWindowTitle('Cover preview')
        box = QVBoxLayout(dlg)
        label = QLabel()
        label.setPixmap(pix.scaledToHeight(360))
        label.setAlignment(Qt.AlignCenter)
        box.addWidget(label)
        close = QDialogButtonBox(QDialogButtonBox.Ok)
        close.accepted.connect(dlg.accept)
        box.addWidget(close)
        dlg.exec_()
