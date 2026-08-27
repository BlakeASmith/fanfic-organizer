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
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPixmap,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    Qt,
    QVBoxLayout,
    QWidget,
)

from calibre.gui2 import error_dialog

from calibre_plugins.fanfic_organizer.prefs import prefs

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

PREVIEW_SAMPLES = (
    (
        'short',
        'Short title',
        'Operation Cameo',
        'alexwlchan',
        'Star Wars',
        'Rey/Ben Solo',
        '12000',
        '72',
    ),
    (
        'long',
        'Long title',
        'The One Where They All Get Together in a Coffee Shop and Save the Galaxy (Again)',
        'Jane AUs-ten',
        'Star Wars',
        'Rey/Ben Solo',
        '125000',
        '72',
    ),
    (
        'very_long',
        'Very long title',
        'and they were roommates (oh my god they were roommates) or: a treatise on found family, time travel, and the inherent eroticism of sharing a tiny apartment',
        'AVeryLongPenNameWithoutSpaces',
        'Marvel Cinematic Universe',
        '',
        '344429',
        '62',
    ),
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
        'header_size': 28,
        'footer_size': 24,
        'min_title_size': 32,
        'min_author_size': 24,
        'title_max_lines': 8,
        'author_max_lines': 3,
        'title_leading': 1.08,
        'author_leading': 1.08,
        'auto_fit_title': True,
        'uppercase_title': False,
        'text_shadow': True,
        'text_stroke_px': 3,
        'text_stroke_color': '#000000',
        'title_color': '#ffffff',
        'author_color': '#ffffff',
        'header_color': '#f5f5f5',
        'footer_color': '#f5f5f5',
        'padding': 0.125,
        'title_y': 0.18,
        'author_y': 0.82,
        'header_y': 0.07,
        'footer_y': 0.93,
        'block_gap': 0.035,
        'scrim': 0.22,
        'auto_contrast': True,
        'contrast_min_ratio': 3.5,
        'lightness_top': 0.26,
        'lightness_bottom': 0.11,
    }


def load_cover_dict() -> dict[str, Any]:
    data = default_cover_dict()
    try:
        from calibre_plugins.fanfic_organizer.enrich import run_ao3kit

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
    from calibre_plugins.fanfic_organizer.enrich import run_ao3kit

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


def _int_spin(value: Any, lo: int, hi: int, default: int) -> QSpinBox:
    box = QSpinBox()
    box.setRange(lo, hi)
    try:
        box.setValue(int(value))
    except (TypeError, ValueError):
        box.setValue(default)
    return box


def _float_spin(
    value: Any,
    lo: float,
    hi: float,
    default: float,
    *,
    decimals: int = 2,
    step: float = 0.02,
    suffix: str = '',
) -> QDoubleSpinBox:
    box = QDoubleSpinBox()
    box.setRange(lo, hi)
    box.setDecimals(decimals)
    box.setSingleStep(step)
    if suffix:
        box.setSuffix(suffix)
    try:
        box.setValue(float(value))
    except (TypeError, ValueError):
        box.setValue(default)
    return box


def _pct_spin(value: Any, default_frac: float, *, lo: float = 0.0, hi: float = 100.0) -> QDoubleSpinBox:
    try:
        frac = float(value)
    except (TypeError, ValueError):
        frac = default_frac
    if frac <= 1.5:
        frac *= 100.0
    return _float_spin(frac, lo, hi, default_frac * 100.0, decimals=1, step=1.0, suffix=' %')


def _hex_edit(value: Any, default: str) -> QLineEdit:
    edit = QLineEdit(str(value or default))
    edit.setPlaceholderText(default)
    return edit


class CoverStyleDialog(QDialog):
    def __init__(self, parent=None, cover: dict[str, Any] | None = None):
        super().__init__(parent)
        self.setWindowTitle('Cover style')
        self.setMinimumWidth(560)
        self.resize(580, 720)
        self._cover = dict(default_cover_dict())
        if cover:
            self._cover.update(cover)

        layout = QVBoxLayout(self)
        intro = QLabel(
            'Covers match the AO3 cover tool: title and author on a dark '
            'gradient whose colour is stable for a fandom. Long titles shrink '
            'and tighten so they stay readable. Extra lines, fonts, layout, '
            'and contrast are optional.'
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        inner = QWidget()
        inner_layout = QVBoxLayout(inner)
        inner_layout.setContentsMargins(4, 4, 12, 4)

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
        inner_layout.addWidget(fields_box)

        colour = QGroupBox('Colour')
        colour_form = QFormLayout(colour)
        self.color_seed = _combo(COLOR_SEEDS, str(self._cover.get('color_seed') or 'fandom'))
        self.color_mode = _combo(COLOR_MODES, str(self._cover.get('color_mode') or 'hash'))
        self.gradient = QCheckBox('Vertical gradient')
        self.gradient.setChecked(bool(self._cover.get('gradient', True)))
        self.auto_contrast = QCheckBox('Auto-darken bright colours for readable white text')
        self.auto_contrast.setChecked(bool(self._cover.get('auto_contrast', True)))
        self.solid_color = _hex_edit(self._cover.get('solid_color'), '#2c3e6b')
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
        self.lightness_top = _pct_spin(self._cover.get('lightness_top'), 0.26, lo=4.0, hi=70.0)
        self.lightness_bottom = _pct_spin(
            self._cover.get('lightness_bottom'), 0.11, lo=2.0, hi=70.0
        )
        colour_form.addRow('Colour from', self.color_seed)
        colour_form.addRow('Mode', self.color_mode)
        colour_form.addRow(self.gradient)
        colour_form.addRow(self.auto_contrast)
        colour_form.addRow('Solid colour', self.solid_color)
        colour_form.addRow('Palette', self.palette)
        colour_form.addRow('Fandom colours', self.fandom_colors)
        colour_form.addRow('Top brightness', self.lightness_top)
        colour_form.addRow('Bottom brightness', self.lightness_bottom)
        inner_layout.addWidget(colour)

        type_box = QGroupBox('Type and size')
        type_form = QFormLayout(type_box)
        self.font = QLineEdit(str(self._cover.get('font') or 'Georgia'))
        self.width = _int_spin(self._cover.get('width'), 200, 2400, 600)
        self.height = _int_spin(self._cover.get('height'), 300, 3600, 900)
        self.title_size = _int_spin(self._cover.get('title_size'), 16, 200, 88)
        self.author_size = _int_spin(self._cover.get('author_size'), 12, 160, 62)
        self.header_size = _int_spin(self._cover.get('header_size'), 10, 80, 28)
        self.footer_size = _int_spin(self._cover.get('footer_size'), 10, 80, 24)
        self.min_title_size = _int_spin(self._cover.get('min_title_size'), 12, 120, 32)
        self.uppercase_title = QCheckBox('Uppercase title')
        self.uppercase_title.setChecked(bool(self._cover.get('uppercase_title')))
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
        type_form.addRow('Header size', self.header_size)
        type_form.addRow('Footer size', self.footer_size)
        type_form.addRow('Smallest title', self.min_title_size)
        type_form.addRow(self.uppercase_title)
        inner_layout.addWidget(type_box)

        layout_box = QGroupBox('Layout')
        layout_form = QFormLayout(layout_box)
        self.auto_fit_title = QCheckBox('Shrink long titles to fit (recommended)')
        self.auto_fit_title.setChecked(bool(self._cover.get('auto_fit_title', True)))
        self.padding = _pct_spin(self._cover.get('padding'), 0.125, lo=4.0, hi=30.0)
        self.title_y = _pct_spin(self._cover.get('title_y'), 0.18)
        self.author_y = _pct_spin(self._cover.get('author_y'), 0.82)
        self.header_y = _pct_spin(self._cover.get('header_y'), 0.07)
        self.footer_y = _pct_spin(self._cover.get('footer_y'), 0.93)
        self.title_leading = _float_spin(
            self._cover.get('title_leading'), 0.80, 1.80, 1.08, decimals=2, step=0.02
        )
        self.author_leading = _float_spin(
            self._cover.get('author_leading'), 0.80, 1.80, 1.08, decimals=2, step=0.02
        )
        self.title_max_lines = _int_spin(self._cover.get('title_max_lines'), 1, 16, 8)
        self.author_max_lines = _int_spin(self._cover.get('author_max_lines'), 1, 8, 3)
        layout_form.addRow(self.auto_fit_title)
        layout_form.addRow('Side padding', self.padding)
        layout_form.addRow('Title position', self.title_y)
        layout_form.addRow('Author position', self.author_y)
        layout_form.addRow('Header position', self.header_y)
        layout_form.addRow('Footer position', self.footer_y)
        layout_form.addRow('Title line spacing', self.title_leading)
        layout_form.addRow('Author line spacing', self.author_leading)
        layout_form.addRow('Max title lines', self.title_max_lines)
        layout_form.addRow('Max author lines', self.author_max_lines)
        inner_layout.addWidget(layout_box)

        text_box = QGroupBox('Text contrast')
        text_form = QFormLayout(text_box)
        self.title_color = _hex_edit(self._cover.get('title_color'), '#ffffff')
        self.author_color = _hex_edit(self._cover.get('author_color'), '#ffffff')
        self.header_color = _hex_edit(self._cover.get('header_color'), '#f5f5f5')
        self.footer_color = _hex_edit(self._cover.get('footer_color'), '#f5f5f5')
        self.text_shadow = QCheckBox('Text shadow')
        self.text_shadow.setChecked(bool(self._cover.get('text_shadow', True)))
        self.text_stroke_px = _int_spin(self._cover.get('text_stroke_px'), 0, 12, 3)
        self.text_stroke_color = _hex_edit(self._cover.get('text_stroke_color'), '#000000')
        self.scrim = _pct_spin(self._cover.get('scrim'), 0.22, lo=0.0, hi=80.0)
        text_form.addRow('Title colour', self.title_color)
        text_form.addRow('Author colour', self.author_color)
        text_form.addRow('Header colour', self.header_color)
        text_form.addRow('Footer colour', self.footer_color)
        text_form.addRow(self.text_shadow)
        text_form.addRow('Outline (px)', self.text_stroke_px)
        text_form.addRow('Outline colour', self.text_stroke_color)
        text_form.addRow('Dark overlay', self.scrim)
        inner_layout.addWidget(text_box)

        scroll.setWidget(inner)
        layout.addWidget(scroll)

        preview_row = QHBoxLayout()
        self.preview_sample = QComboBox()
        for key, label, *_rest in PREVIEW_SAMPLES:
            self.preview_sample.addItem(label, key)
        self.preview_sample.setCurrentIndex(1)
        self.preview_btn = QPushButton('Preview sample…')
        self.preview_btn.clicked.connect(self.preview)
        preview_row.addWidget(QLabel('Sample'))
        preview_row.addWidget(self.preview_sample)
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
            'auto_contrast': self.auto_contrast.isChecked(),
            'solid_color': self.solid_color.text().strip() or '#2c3e6b',
            'palette': palette,
            'fandom_colors': parse_fandom_colors(self.fandom_colors.toPlainText()),
            'lightness_top': self.lightness_top.value() / 100.0,
            'lightness_bottom': self.lightness_bottom.value() / 100.0,
            'font': self.font.text().strip() or 'Georgia',
            'width': int(self.width.value()),
            'height': int(self.height.value()),
            'title_size': int(self.title_size.value()),
            'author_size': int(self.author_size.value()),
            'header_size': int(self.header_size.value()),
            'footer_size': int(self.footer_size.value()),
            'min_title_size': int(self.min_title_size.value()),
            'uppercase_title': self.uppercase_title.isChecked(),
            'auto_fit_title': self.auto_fit_title.isChecked(),
            'padding': self.padding.value() / 100.0,
            'title_y': self.title_y.value() / 100.0,
            'author_y': self.author_y.value() / 100.0,
            'header_y': self.header_y.value() / 100.0,
            'footer_y': self.footer_y.value() / 100.0,
            'title_leading': float(self.title_leading.value()),
            'author_leading': float(self.author_leading.value()),
            'title_max_lines': int(self.title_max_lines.value()),
            'author_max_lines': int(self.author_max_lines.value()),
            'title_color': self.title_color.text().strip() or '#ffffff',
            'author_color': self.author_color.text().strip() or '#ffffff',
            'header_color': self.header_color.text().strip() or '#f5f5f5',
            'footer_color': self.footer_color.text().strip() or '#f5f5f5',
            'text_shadow': self.text_shadow.isChecked(),
            'text_stroke_px': int(self.text_stroke_px.value()),
            'text_stroke_color': self.text_stroke_color.text().strip() or '#000000',
            'scrim': self.scrim.value() / 100.0,
        }

    def preview(self) -> None:
        import tempfile
        from pathlib import Path

        from calibre_plugins.fanfic_organizer.enrich import EnrichCancelled, run_ao3kit

        values = self.values()
        sample = self.preview_sample.currentData()
        chosen = PREVIEW_SAMPLES[1]
        for row in PREVIEW_SAMPLES:
            if row[0] == sample:
                chosen = row
                break
        _key, _label, title, author, fandom, relationship, words, score = chosen
        tmp = Path(tempfile.mkdtemp(prefix='ao3-cover-')) / 'preview.png'
        argv = [
            'cover',
            '--preview',
            '--title',
            title,
            '--author',
            author,
            '--fandom',
            fandom,
            '--relationship',
            relationship,
            '--wordcount',
            words,
            '--score',
            score,
            '--settings-json',
            json.dumps(values),
            '-o',
            str(tmp),
        ]
        try:
            code, stdout, stderr = run_ao3kit(argv)
        except EnrichCancelled:
            return
        if code != 0 or not tmp.is_file():
            error_dialog(
                self,
                'Fanfic Organizer',
                'Could not render a sample cover.',
                det_msg=(stderr or stdout or f'exit {code}').strip(),
                show=True,
            )
            return
        pix = QPixmap(str(tmp))
        if pix.isNull():
            error_dialog(
                self,
                'Fanfic Organizer',
                'Wrote a sample cover but could not display it.',
                show=True,
            )
            return
        dlg = QDialog(self)
        dlg.setWindowTitle('Cover preview')
        box = QVBoxLayout(dlg)
        label = QLabel()
        label.setPixmap(pix.scaledToHeight(420))
        label.setAlignment(Qt.AlignCenter)
        box.addWidget(label)
        close = QDialogButtonBox(QDialogButtonBox.Ok)
        close.accepted.connect(dlg.accept)
        box.addWidget(close)
        dlg.exec_()
