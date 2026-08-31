# -*- coding: utf-8 -*-
"""Qt dialogs for combining / editing omnibus EPUBs."""

from __future__ import annotations

from typing import Any

try:
    from qt.core import (
        QCheckBox,
        QComboBox,
        QDialog,
        QDialogButtonBox,
        QHBoxLayout,
        QLabel,
        QLineEdit,
        QListWidget,
        QListWidgetItem,
        QMessageBox,
        QPushButton,
        QVBoxLayout,
    )
except ImportError:
    from PyQt5.QtWidgets import (
        QCheckBox,
        QComboBox,
        QDialog,
        QDialogButtonBox,
        QHBoxLayout,
        QLabel,
        QLineEdit,
        QListWidget,
        QListWidgetItem,
        QMessageBox,
        QPushButton,
        QVBoxLayout,
    )


class CombineSelectedDialog(QDialog):
    def __init__(self, parent, rows: list[dict[str, Any]], *, default_title: str = ''):
        super().__init__(parent)
        self.setWindowTitle('Combine selected EPUBs')
        self._rows = list(rows)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel('Order (top = first in the EPUB):'))
        self.list = QListWidget()
        for row in self._rows:
            title = str(row.get('title') or row.get('record', {}).get('title') or '?')
            item = QListWidgetItem(title)
            item.setData(256, row)  # Qt.UserRole
            self.list.addItem(item)
        layout.addWidget(self.list)
        btns = QHBoxLayout()
        up = QPushButton('Up')
        down = QPushButton('Down')
        up.clicked.connect(self._move_up)
        down.clicked.connect(self._move_down)
        btns.addWidget(up)
        btns.addWidget(down)
        btns.addStretch(1)
        layout.addLayout(btns)
        layout.addWidget(QLabel('Title:'))
        self.title_edit = QLineEdit(default_title)
        layout.addWidget(self.title_edit)
        self.include_prefaces = QCheckBox('Include each work’s preface pages')
        self.include_prefaces.setChecked(False)
        layout.addWidget(self.include_prefaces)
        self.remove_individuals = QCheckBox('Remove individual books after combine')
        self.remove_individuals.setChecked(False)
        layout.addWidget(self.remove_individuals)
        box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        box.accepted.connect(self._accept)
        box.rejected.connect(self.reject)
        layout.addWidget(box)

    def _move_up(self):
        row = self.list.currentRow()
        if row <= 0:
            return
        item = self.list.takeItem(row)
        self.list.insertItem(row - 1, item)
        self.list.setCurrentRow(row - 1)

    def _move_down(self):
        row = self.list.currentRow()
        if row < 0 or row >= self.list.count() - 1:
            return
        item = self.list.takeItem(row)
        self.list.insertItem(row + 1, item)
        self.list.setCurrentRow(row + 1)

    def _accept(self):
        if self.remove_individuals.isChecked():
            n = self.list.count()
            if (
                QMessageBox.question(
                    self,
                    'Remove individual books?',
                    f'Delete {n} individual library books after the omnibus is created?',
                )
                != QMessageBox.Yes
            ):
                return
        self.accept()

    def result_payload(self) -> dict[str, Any]:
        ordered = []
        for i in range(self.list.count()):
            ordered.append(self.list.item(i).data(256))
        return {
            'rows': ordered,
            'title': self.title_edit.text().strip(),
            'include_prefaces': self.include_prefaces.isChecked(),
            'remove_individuals': self.remove_individuals.isChecked(),
        }


class CombineSeriesDialog(QDialog):
    def __init__(
        self,
        parent,
        *,
        series_name: str,
        series_id: str,
        member_count: int,
        missing_count: int,
        updating: bool,
    ):
        super().__init__(parent)
        self.setWindowTitle('Combine series')
        layout = QVBoxLayout(self)
        action = 'Update existing omnibus' if updating else 'Create omnibus'
        layout.addWidget(
            QLabel(
                f'{action} for “{series_name or series_id}” '
                f'({member_count} with EPUB'
                + (f', {missing_count} missing EPUB' if missing_count else '')
                + ').'
            )
        )
        self.fetch_newer = QCheckBox('Check AO3 for newer series parts first')
        self.fetch_newer.setChecked(False)
        layout.addWidget(self.fetch_newer)
        self.include_prefaces = QCheckBox('Include each work’s preface pages')
        self.include_prefaces.setChecked(False)
        layout.addWidget(self.include_prefaces)
        self.remove_individuals = QCheckBox('Remove individual books after combine')
        self.remove_individuals.setChecked(False)
        layout.addWidget(self.remove_individuals)
        box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        box.accepted.connect(self._accept)
        box.rejected.connect(self.reject)
        layout.addWidget(box)

    def _accept(self):
        if self.remove_individuals.isChecked():
            if (
                QMessageBox.question(
                    self,
                    'Remove individual books?',
                    'Delete the individual series parts from the library after combining?',
                )
                != QMessageBox.Yes
            ):
                return
        self.accept()

    def result_payload(self) -> dict[str, Any]:
        return {
            'fetch_newer': self.fetch_newer.isChecked(),
            'include_prefaces': self.include_prefaces.isChecked(),
            'remove_individuals': self.remove_individuals.isChecked(),
        }


class CombineCollectionDialog(QDialog):
    def __init__(self, parent, collections: list[str], *, updating_name: str = ''):
        super().__init__(parent)
        self.setWindowTitle('Combine collection')
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel('Collection:'))
        self.combo = QComboBox()
        self.combo.setEditable(True)
        for name in collections:
            self.combo.addItem(name)
        if updating_name:
            idx = self.combo.findText(updating_name)
            if idx >= 0:
                self.combo.setCurrentIndex(idx)
            else:
                self.combo.setEditText(updating_name)
        layout.addWidget(self.combo)
        self.auto_update = QCheckBox('Keep this EPUB updated when the collection changes')
        self.auto_update.setChecked(True)
        layout.addWidget(self.auto_update)
        self.include_prefaces = QCheckBox('Include each work’s preface pages')
        self.include_prefaces.setChecked(False)
        layout.addWidget(self.include_prefaces)
        self.remove_individuals = QCheckBox('Remove individual books after combine')
        self.remove_individuals.setChecked(False)
        self.remove_individuals.setToolTip(
            'Dangerous if books also belong to other collections.'
        )
        layout.addWidget(self.remove_individuals)
        box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        box.accepted.connect(self._accept)
        box.rejected.connect(self.reject)
        layout.addWidget(box)

    def _accept(self):
        if not self.combo.currentText().strip():
            QMessageBox.warning(self, 'Combine collection', 'Pick a collection name.')
            return
        if self.remove_individuals.isChecked():
            if (
                QMessageBox.question(
                    self,
                    'Remove individual books?',
                    'Delete individual books in this collection after combining?\n'
                    'Books that also belong to other collections will be deleted too.',
                )
                != QMessageBox.Yes
            ):
                return
        self.accept()

    def result_payload(self) -> dict[str, Any]:
        return {
            'collection': self.combo.currentText().strip(),
            'auto_update': self.auto_update.isChecked(),
            'include_prefaces': self.include_prefaces.isChecked(),
            'remove_individuals': self.remove_individuals.isChecked(),
        }


class EditOmnibusDialog(QDialog):
    def __init__(self, parent, members: list[dict[str, Any]], *, auto_update: bool):
        super().__init__(parent)
        self.setWindowTitle('Edit omnibus')
        self._members = list(members)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel('Members (reorder = spine/ToC only; notes kept):'))
        self.list = QListWidget()
        for m in self._members:
            if not m.get('active', True):
                continue
            label = str(m.get('title') or m.get('member_id') or '?')
            item = QListWidgetItem(label)
            item.setData(256, m)
            self.list.addItem(item)
        layout.addWidget(self.list)
        btns = QHBoxLayout()
        up = QPushButton('Up')
        down = QPushButton('Down')
        up.clicked.connect(self._move_up)
        down.clicked.connect(self._move_down)
        btns.addWidget(up)
        btns.addWidget(down)
        btns.addStretch(1)
        layout.addLayout(btns)
        self.auto_update = QCheckBox('Auto-update when collection changes')
        self.auto_update.setChecked(bool(auto_update))
        layout.addWidget(self.auto_update)
        self.rebuild = QCheckBox('Rebuild EPUB (rewrites paths — breaks reader notes)')
        self.rebuild.setChecked(False)
        layout.addWidget(self.rebuild)
        self.explode = QCheckBox('Explode into separate library books instead')
        self.explode.setChecked(False)
        layout.addWidget(self.explode)
        self.delete_after_explode = QCheckBox('Delete omnibus after explode')
        self.delete_after_explode.setChecked(False)
        layout.addWidget(self.delete_after_explode)
        box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        box.accepted.connect(self.accept)
        box.rejected.connect(self.reject)
        layout.addWidget(box)

    def _move_up(self):
        row = self.list.currentRow()
        if row <= 0:
            return
        item = self.list.takeItem(row)
        self.list.insertItem(row - 1, item)
        self.list.setCurrentRow(row - 1)

    def _move_down(self):
        row = self.list.currentRow()
        if row < 0 or row >= self.list.count() - 1:
            return
        item = self.list.takeItem(row)
        self.list.insertItem(row + 1, item)
        self.list.setCurrentRow(row + 1)

    def result_payload(self) -> dict[str, Any]:
        order = []
        for i in range(self.list.count()):
            m = self.list.item(i).data(256) or {}
            order.append(str(m.get('member_id') or ''))
        return {
            'order': [m for m in order if m],
            'auto_update': self.auto_update.isChecked(),
            'rebuild': self.rebuild.isChecked(),
            'explode': self.explode.isChecked(),
            'delete_after_explode': self.delete_after_explode.isChecked(),
        }
