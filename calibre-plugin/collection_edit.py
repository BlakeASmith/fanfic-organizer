# -*- coding: utf-8 -*-
"""Edit collection membership for the Calibre selection."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from PyQt5.Qt import (
    QAbstractItemView,
    QApplication,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    Qt,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from calibre.gui2 import error_dialog, question_dialog

from calibre_plugins.wranglekit.collection_rules import (
    MATCH_CHOICES,
    MODE_CHOICES,
    build_collections_list_argv,
    build_collections_pin_argv,
    build_collections_remove_argv,
    build_collections_set_argv,
    build_collections_toggle_argv,
    build_collections_unpin_argv,
    collection_names_from_explain,
    collection_names_from_rules,
    flatten_explain_rows,
    format_membership_status,
    format_membership_why,
    format_when,
    merge_collection_names,
    parse_rules_list,
)


def _run_ao3kit(parent, argv: list[str], *, quiet: bool = False):
    from calibre_plugins.wranglekit.enrich import EnrichCancelled, run_ao3kit

    QApplication.setOverrideCursor(Qt.WaitCursor)
    try:
        code, stdout, stderr = run_ao3kit(argv)
    except EnrichCancelled:
        return None, None, None
    finally:
        QApplication.restoreOverrideCursor()
    if code != 0:
        if not quiet:
            error_dialog(
                parent,
                'Wranglekit',
                'Could not update collections.',
                det_msg=(stderr or stdout or f'exit {code}').strip(),
                show=True,
            )
        return None, stdout, stderr
    return code, stdout, stderr


def prompt_collection_name(
    parent,
    names: list[str],
    *,
    prompt: str,
    current: str = '',
) -> str | None:
    """Pick an existing collection or type a new name. None if cancelled."""
    try:
        from PyQt5.Qt import QInputDialog
    except ImportError:
        from PyQt5.QtWidgets import QInputDialog

    wanted = str(current or '').strip()
    items = list(names)
    if wanted and wanted.casefold() not in {item.casefold() for item in items}:
        items = [wanted, *items]
    start = 0
    if wanted:
        for index, item in enumerate(items):
            if item.casefold() == wanted.casefold():
                start = index
                break
    if items:
        name, ok = QInputDialog.getItem(
            parent, 'Wranglekit', prompt, items, start, True
        )
    else:
        name, ok = QInputDialog.getText(parent, 'Wranglekit', prompt)
    if not ok:
        return None
    name = str(name or '').strip()
    if not name:
        error_dialog(parent, 'Wranglekit', 'Type a collection name first.', show=True)
        return None
    return name


def load_known_collection_names(parent, db, extra: list[str] | None = None) -> list[str]:
    from calibre_plugins.wranglekit.selected import library_collection_names

    rows: list[dict] = []
    result = _run_ao3kit(parent, build_collections_list_argv(), quiet=True)
    if result[0] is not None:
        try:
            rows = parse_rules_list(result[1] or '[]')
        except ValueError:
            rows = []
    return merge_collection_names(
        collection_names_from_rules(rows),
        library_collection_names(db),
        extra or [],
    )


class CollectionRuleEditDialog(QDialog):
    """Add or edit one collection membership rule."""

    def __init__(self, parent=None, row: dict | None = None):
        super().__init__(parent)
        self._row = dict(row or {})
        self.setWindowTitle(
            'Edit collection rule' if self._row.get('id') else 'New collection rule'
        )
        self.setMinimumWidth(480)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.collections = QLineEdit()
        self.collections.setText(_join(self._row.get('collections')))
        form.addRow('Collection', self.collections)

        if_row = QWidget()
        if_layout = QHBoxLayout(if_row)
        if_layout.setContentsMargins(0, 0, 0, 0)
        self.match = QComboBox()
        for value, label in MATCH_CHOICES:
            self.match.addItem(label, value)
        idx = self.match.findData(str(self._row.get('match') or 'mentions'))
        if idx >= 0:
            self.match.setCurrentIndex(idx)
        self.values = QLineEdit()
        self.values.setText(_join(self._row.get('values')))
        if_layout.addWidget(self.match)
        if_layout.addWidget(self.values, 1)
        form.addRow('When', if_row)

        self.mode = QComboBox()
        for value, label in MODE_CHOICES:
            self.mode.addItem(label, value)
        idx = self.mode.findData(str(self._row.get('mode') or 'include'))
        if idx >= 0:
            self.mode.setCurrentIndex(idx)
        form.addRow('Then', self.mode)
        layout.addLayout(form)

        hint = QLabel(
            'Shared rules apply to every matching book, not only the selection.'
        )
        hint.setWordWrap(True)
        layout.addWidget(hint)

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def argv(self) -> list[str] | None:
        collection = self.collections.text().strip()
        values = self.values.text().strip()
        match = str(self.match.currentData() or 'mentions')
        if not values:
            error_dialog(self, 'Wranglekit', 'Type something to match first.', show=True)
            return None
        if match in {'work_id', 'calibre_uuid'} and not collection:
            error_dialog(self, 'Wranglekit', 'Type a collection name first.', show=True)
            return None
        pin = match in {'work_id', 'calibre_uuid'} or bool(self._row.get('pin'))
        fields = {
            'match': match,
            'values': values,
            'collections': collection,
            'mode': str(self.mode.currentData() or 'include'),
            'pin': pin,
            'enabled': bool(self._row.get('enabled', True)),
            'description': str(self._row.get('description') or ''),
        }
        rule_id = str(self._row.get('id') or '')
        if rule_id:
            return build_collections_set_argv(rule_id, **fields)
        return None


class EditSelectedCollectionsDialog(QDialog):
    """Show why selected books are in collections, and edit pins / rules."""

    def __init__(self, gui, book_ids: list[int]):
        super().__init__(gui)
        self.gui = gui
        self.book_ids = list(book_ids)
        self._books: list[dict] = []
        self._rows: list[dict] = []
        self._known_names: list[str] = []
        self._focus_collection = ''
        self.setWindowTitle('Edit collections of selected books')
        self.setMinimumSize(780, 560)
        self.resize(900, 640)

        layout = QVBoxLayout(self)
        intro = QLabel(
            'Each row is one book in one collection, and <b>Why</b> lists the '
            'rules that put it there (or keep it out). Use <b>Add this book '
            'to…</b> or <b>Add selected books to…</b> to pin a book into an '
            'existing collection or a new name — even if no rule matches, or '
            'if it already belongs to a different collection. Always / Never '
            'are per-work rules so recompute keeps that decision. Editing a '
            'shared rule changes every matching book. <b>Write collections to '
            'Calibre</b> replaces the column from the current rules. This does '
            'not fetch AO3 or change tags.'
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        filter_row = QHBoxLayout()
        filter_row.addWidget(QLabel('Show'))
        self.collection_filter = QComboBox()
        self.collection_filter.addItem('All collections', '')
        self.collection_filter.currentIndexChanged.connect(self._rebuild_table)
        filter_row.addWidget(self.collection_filter, 1)
        self.add_btn = QPushButton('Add selected books to…')
        self.add_btn.setToolTip(
            'Pin every selected book into an existing collection or a new name.'
        )
        self.add_btn.clicked.connect(self._add_selected)
        filter_row.addWidget(self.add_btn)
        layout.addLayout(filter_row)

        self.empty = QLabel('')
        self.empty.setWordWrap(True)
        layout.addWidget(self.empty)

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(['Book', 'Collection', 'Status', 'Why'])
        header = self.table.horizontalHeader()
        header.setStretchLastSection(True)
        try:
            header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
            header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
            header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        except Exception:
            pass
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        self.table.itemSelectionChanged.connect(self._sync_details)
        layout.addWidget(self.table, 1)

        self.details = QLabel('Select a row to see the rules.')
        self.details.setWordWrap(True)
        layout.addWidget(self.details)

        self.rules = QListWidget()
        self.rules.setMaximumHeight(110)
        self.rules.itemSelectionChanged.connect(self._sync_buttons)
        layout.addWidget(self.rules)

        book_btns = QHBoxLayout()
        self.always_btn = QPushButton('Always this book')
        self.never_btn = QPushButton('Never this book')
        self.keep_pin_btn = QPushButton('Keep as a pin')
        self.unpin_btn = QPushButton("Remove this book's pin")
        self.add_this_btn = QPushButton('Add this book to…')
        self.always_btn.setToolTip('Per-work rule: always put this book in the collection.')
        self.never_btn.setToolTip('Per-work rule: never put this book in the collection.')
        self.keep_pin_btn.setToolTip(
            'Save the current membership as a per-work rule so recompute keeps it.'
        )
        self.unpin_btn.setToolTip(
            'Remove Always / Never pins for this book so only shared rules apply.'
        )
        self.add_this_btn.setToolTip(
            'Pin this book into an existing collection or a new name, even if '
            'it already belongs to a different collection.'
        )
        self.always_btn.clicked.connect(self._always_book)
        self.never_btn.clicked.connect(self._never_book)
        self.keep_pin_btn.clicked.connect(self._keep_pin)
        self.unpin_btn.clicked.connect(self._unpin_book)
        self.add_this_btn.clicked.connect(self._add_this_book)
        for btn in (
            self.always_btn,
            self.never_btn,
            self.keep_pin_btn,
            self.unpin_btn,
            self.add_this_btn,
        ):
            book_btns.addWidget(btn)
        book_btns.addStretch(1)
        layout.addLayout(book_btns)

        rule_btns = QHBoxLayout()
        self.edit_rule_btn = QPushButton('Edit rule…')
        self.toggle_rule_btn = QPushButton('Turn rule off')
        self.delete_rule_btn = QPushButton('Delete rule…')
        self.edit_rule_btn.clicked.connect(self._edit_rule)
        self.toggle_rule_btn.clicked.connect(self._toggle_rule)
        self.delete_rule_btn.clicked.connect(self._delete_rule)
        for btn in (self.edit_rule_btn, self.toggle_rule_btn, self.delete_rule_btn):
            rule_btns.addWidget(btn)
        rule_btns.addStretch(1)
        layout.addLayout(rule_btns)

        buttons = QDialogButtonBox()
        self.write_btn = buttons.addButton(
            'Write collections to Calibre', QDialogButtonBox.ActionRole
        )
        self.write_btn.setToolTip(
            'Replace the Collections column on the selected books from the '
            'current rules. Does not fetch AO3 or change tags.'
        )
        self.write_btn.clicked.connect(self._write_calibre)
        buttons.addButton(QDialogButtonBox.Close).clicked.connect(self.accept)
        layout.addWidget(buttons)

        self._reload()

    def _selected_row(self) -> dict | None:
        index = self.table.currentRow()
        if index < 0 or index >= len(self._rows):
            return None
        return self._rows[index]

    def _selected_rule(self) -> dict | None:
        item = self.rules.currentItem()
        if item is None:
            return None
        data = item.data(Qt.UserRole)
        return data if isinstance(data, dict) else None

    def _pin_args(self, row: dict) -> tuple[str, str]:
        return str(row.get('work_id') or ''), str(row.get('calibre_uuid') or '')

    def _reload(self) -> None:
        from calibre_plugins.wranglekit.scrape_run import (
            build_collections_explain_argv,
        )
        from calibre_plugins.wranglekit.selected import load_selected_for_collections

        ready, skipped = load_selected_for_collections(
            self.gui.current_db, self.book_ids
        )
        if skipped:
            self.empty.setText(
                'Skipped: '
                + '; '.join(
                    f'{item.get("title")}: {item.get("reason")}' for item in skipped[:6]
                )
            )
            self.empty.setVisible(True)
        else:
            self.empty.setVisible(False)
        if not ready:
            error_dialog(
                self,
                'Wranglekit',
                'None of the selected books could be loaded.',
                show=True,
            )
            return

        tmp = tempfile.mkdtemp(prefix='ao3-coll-explain-')
        tmp_path = Path(tmp)
        try:
            inp = tmp_path / 'in.jsonl'
            out = tmp_path / 'explain.json'
            with inp.open('w', encoding='utf-8') as handle:
                for item in ready:
                    record = dict(item['record'])
                    record['book_id'] = item['book_id']
                    if not record.get('title'):
                        record['title'] = item.get('title')
                    handle.write(json.dumps(record, ensure_ascii=False) + '\n')
            result = _run_ao3kit(
                self, build_collections_explain_argv(str(inp), str(out))
            )
            if result[0] is None:
                return
            if not out.is_file():
                error_dialog(
                    self, 'Wranglekit', 'Could not explain collection membership.', show=True
                )
                return
            from calibre_plugins.wranglekit.collection_rules import parse_explain

            self._books = parse_explain(out.read_text(encoding='utf-8'))
        finally:
            import shutil

            shutil.rmtree(tmp, ignore_errors=True)

        self._known_names = load_known_collection_names(
            self,
            self.gui.current_db,
            extra=collection_names_from_explain(self._books),
        )
        current = self._focus_collection or str(self.collection_filter.currentData() or '')
        self._focus_collection = ''
        self.collection_filter.blockSignals(True)
        try:
            self.collection_filter.clear()
            self.collection_filter.addItem('All collections', '')
            for name in self._known_names:
                self.collection_filter.addItem(name, name)
            idx = self.collection_filter.findData(current)
            self.collection_filter.setCurrentIndex(idx if idx >= 0 else 0)
        finally:
            self.collection_filter.blockSignals(False)
        self._rebuild_table()

    def _rebuild_table(self) -> None:
        wanted = str(self.collection_filter.currentData() or '')
        selected = self._selected_row()
        selected_key = None
        if selected is not None:
            selected_key = (
                selected.get('book_id'),
                str(selected.get('name') or '').casefold(),
            )
        self._rows = flatten_explain_rows(self._books, wanted)
        self.table.setRowCount(len(self._rows))
        restore = 0
        for index, row in enumerate(self._rows):
            self.table.setItem(index, 0, QTableWidgetItem(str(row.get('title') or '')))
            self.table.setItem(index, 1, QTableWidgetItem(str(row.get('name') or '—')))
            self.table.setItem(
                index, 2, QTableWidgetItem(format_membership_status(str(row.get('status') or '')))
            )
            self.table.setItem(index, 3, QTableWidgetItem(format_membership_why(row)))
            key = (row.get('book_id'), str(row.get('name') or '').casefold())
            if selected_key is not None and key == selected_key:
                restore = index
        if self._rows:
            self.table.selectRow(restore)
        self._sync_details()

    def _sync_details(self) -> None:
        row = self._selected_row()
        self.rules.clear()
        if row is None:
            self.details.setText('Select a row to see the rules.')
            self._sync_buttons()
            return
        name = str(row.get('name') or '').strip()
        title = str(row.get('title') or 'this book')
        if not name:
            self.details.setText(
                f'<b>{title}</b> is not in any collection yet. Use '
                '<b>Add this book to…</b> to pick an existing collection or '
                'type a new name.'
            )
            self._sync_buttons()
            return
        status = format_membership_status(str(row.get('status') or ''))
        self.details.setText(
            f'<b>{title}</b> · {name}: {status}<br>{format_membership_why(row)}'
        )
        for kind, rules in (
            ('Put in', row.get('includes') or []),
            ('Keep out', row.get('excludes') or []),
        ):
            for rule in rules:
                label = f'{kind}: {format_when(rule)}'
                item = QListWidgetItem(label)
                item.setData(Qt.UserRole, rule)
                self.rules.addItem(item)
        if self.rules.count() and self.rules.currentRow() < 0:
            self.rules.setCurrentRow(0)
        self._sync_buttons()

    def _sync_buttons(self) -> None:
        row = self._selected_row()
        has_row = row is not None and bool(str((row or {}).get('name') or '').strip())
        work_id, uuid = self._pin_args(row or {})
        can_pin = has_row and bool(work_id or uuid)
        status = str((row or {}).get('status') or '')
        has_include_pin = bool((row or {}).get('include_pins'))
        has_exclude_pin = bool((row or {}).get('exclude_pins'))
        self.always_btn.setEnabled(can_pin and not has_include_pin)
        self.never_btn.setEnabled(can_pin and not has_exclude_pin)
        self.keep_pin_btn.setEnabled(can_pin and status == 'unexplained')
        self.unpin_btn.setEnabled(can_pin and (has_include_pin or has_exclude_pin))
        identifiable = row is not None and bool(work_id or uuid)
        self.add_this_btn.setEnabled(identifiable)
        self.add_btn.setEnabled(True)
        rule = self._selected_rule()
        has_shared = bool(rule) and not bool((rule or {}).get('pin'))
        self.edit_rule_btn.setEnabled(has_shared)
        self.toggle_rule_btn.setEnabled(has_shared)
        self.delete_rule_btn.setEnabled(has_shared)
        if has_shared and rule is not None:
            on = bool(rule.get('enabled', True))
            self.toggle_rule_btn.setText('Turn rule off' if on else 'Turn rule on')

    def _require_pin_target(self, row: dict) -> tuple[str, str] | None:
        work_id, uuid = self._pin_args(row)
        if work_id or uuid:
            return work_id, uuid
        error_dialog(
            self,
            'Wranglekit',
            'This book has no AO3 work id or Calibre UUID to pin.',
            show=True,
        )
        return None

    def _suggested_collection(self, row: dict | None = None) -> str:
        name = str((row or {}).get('name') or '').strip()
        if name:
            return name
        return str(self.collection_filter.currentData() or '').strip()

    def _pin_target_to(self, *, work_id: str, uuid: str, collection: str, title: str) -> bool:
        if _run_ao3kit(
            self,
            build_collections_unpin_argv(
                collection=collection, work_id=work_id, uuid=uuid, all_modes=True
            ),
        )[0] is None:
            return False
        if _run_ao3kit(
            self,
            build_collections_pin_argv(
                collection=collection,
                work_id=work_id,
                uuid='' if work_id else uuid,
                description=title,
            ),
        )[0] is None:
            return False
        return True

    def _always_book(self) -> None:
        row = self._selected_row()
        if row is None:
            return
        target = self._require_pin_target(row)
        if target is None:
            return
        work_id, uuid = target
        name = str(row.get('name') or '').strip()
        if not name:
            return
        if not self._pin_target_to(
            work_id=work_id,
            uuid=uuid,
            collection=name,
            title=str(row.get('title') or ''),
        ):
            return
        self._reload()

    def _never_book(self) -> None:
        row = self._selected_row()
        if row is None:
            return
        target = self._require_pin_target(row)
        if target is None:
            return
        work_id, uuid = target
        name = str(row.get('name') or '')
        if not question_dialog(
            self,
            'Wranglekit',
            f'Never put “{row.get("title")}” in {name}?\n\n'
            'This is a per-work rule. Shared tag/fandom rules will not put it back.',
        ):
            return
        if _run_ao3kit(
            self,
            build_collections_unpin_argv(
                collection=name, work_id=work_id, uuid=uuid, all_modes=True
            ),
        )[0] is None:
            return
        if _run_ao3kit(
            self,
            build_collections_pin_argv(
                collection=name,
                work_id=work_id,
                uuid='' if work_id else uuid,
                description=str(row.get('title') or ''),
                exclude=True,
            ),
        )[0] is None:
            return
        self._reload()

    def _keep_pin(self) -> None:
        row = self._selected_row()
        if row is None:
            return
        target = self._require_pin_target(row)
        if target is None:
            return
        work_id, uuid = target
        if _run_ao3kit(
            self,
            build_collections_pin_argv(
                collection=str(row.get('name') or ''),
                work_id=work_id,
                uuid='' if work_id else uuid,
                description=str(row.get('title') or ''),
            ),
        )[0] is None:
            return
        self._reload()

    def _unpin_book(self) -> None:
        row = self._selected_row()
        if row is None:
            return
        target = self._require_pin_target(row)
        if target is None:
            return
        work_id, uuid = target
        if _run_ao3kit(
            self,
            build_collections_unpin_argv(
                collection=str(row.get('name') or ''),
                work_id=work_id,
                uuid=uuid,
                all_modes=True,
            ),
        )[0] is None:
            return
        self._reload()

    def _edit_rule(self) -> None:
        rule = self._selected_rule()
        if rule is None or rule.get('pin'):
            return
        dialog = CollectionRuleEditDialog(self, rule)
        if not dialog.exec_():
            return
        argv = dialog.argv()
        if not argv:
            return
        if _run_ao3kit(self, argv)[0] is None:
            return
        self._reload()

    def _toggle_rule(self) -> None:
        rule = self._selected_rule()
        if rule is None or not rule.get('id'):
            return
        if _run_ao3kit(
            self, build_collections_toggle_argv(str(rule.get('id')))
        )[0] is None:
            return
        self._reload()

    def _delete_rule(self) -> None:
        rule = self._selected_rule()
        if rule is None or not rule.get('id'):
            return
        summary = format_when(rule)
        if not question_dialog(
            self,
            'Wranglekit',
            f'Delete this collection rule for every book?\n\n{summary}',
        ):
            return
        if _run_ao3kit(
            self, build_collections_remove_argv(str(rule.get('id')))
        )[0] is None:
            return
        self._reload()

    def _ask_collection(self, *, prompt: str, current: str = '') -> str | None:
        return prompt_collection_name(
            self,
            self._known_names,
            prompt=prompt,
            current=current,
        )

    def _add_this_book(self) -> None:
        row = self._selected_row()
        if row is None:
            return
        target = self._require_pin_target(row)
        if target is None:
            return
        work_id, uuid = target
        name = self._ask_collection(
            prompt=(
                'Add this book to this collection (pick an existing name or '
                'type a new one):'
            ),
            current=self._suggested_collection(row),
        )
        if not name:
            return
        if not self._pin_target_to(
            work_id=work_id,
            uuid=uuid,
            collection=name,
            title=str(row.get('title') or ''),
        ):
            return
        self._focus_collection = name
        self._reload()

    def _add_selected(self) -> None:
        from calibre_plugins.wranglekit.selected import pin_targets_from_selected

        name = self._ask_collection(
            prompt=(
                'Add the selected books to this collection (pick an existing '
                'name or type a new one):'
            ),
            current=self._suggested_collection(self._selected_row()),
        )
        if not name:
            return
        targets, skipped = pin_targets_from_selected(self.gui.current_db, self.book_ids)
        if not targets:
            extra = ''
            if skipped:
                extra = '\n\n' + '\n'.join(
                    f'{item.get("title")}: {item.get("reason")}' for item in skipped[:8]
                )
            error_dialog(
                self,
                'Wranglekit',
                'None of the selected books have an AO3 work id or Calibre UUID.'
                + extra,
                show=True,
            )
            return
        for item in targets:
            if not self._pin_target_to(
                work_id=item['work_id'],
                uuid=item['uuid'],
                collection=name,
                title=item['title'],
            ):
                return
        self._focus_collection = name
        self._reload()

    def _write_calibre(self) -> None:
        from calibre_plugins.wranglekit.columns import apply_layout_columns
        from calibre_plugins.wranglekit.importer import refresh_library_ui
        from calibre_plugins.wranglekit.scrape_run import build_collections_argv
        from calibre_plugins.wranglekit.selected import (
            apply_collections_records,
            load_selected_for_collections,
        )

        ready, skipped = load_selected_for_collections(
            self.gui.current_db, self.book_ids
        )
        if not ready:
            extra = ''
            if skipped:
                extra = '\n\n' + '\n'.join(
                    f'{item.get("title")}: {item.get("reason")}' for item in skipped[:8]
                )
            error_dialog(
                self,
                'Wranglekit',
                'None of the selected books could be loaded.' + extra,
                show=True,
            )
            return
        tmp = tempfile.mkdtemp(prefix='ao3-coll-write-')
        tmp_path = Path(tmp)
        try:
            inp = tmp_path / 'in.jsonl'
            out = tmp_path / 'out.jsonl'
            with inp.open('w', encoding='utf-8') as handle:
                for item in ready:
                    handle.write(json.dumps(item['record'], ensure_ascii=False) + '\n')
            result = _run_ao3kit(
                self, build_collections_argv(str(inp), str(out), {})
            )
            if result[0] is None:
                return
            records = []
            for line in out.read_text(encoding='utf-8').splitlines():
                if line.strip():
                    records.append(json.loads(line))
            if len(records) != len(ready):
                error_dialog(
                    self,
                    'Wranglekit',
                    'Collection recompute returned a different number of books.',
                    show=True,
                )
                return
            db = apply_layout_columns(self.gui)
            items = [
                {'book_id': item['book_id'], 'record': record}
                for item, record in zip(ready, records, strict=True)
            ]
            apply_collections_records(db, items)
            refresh_library_ui(
                self.gui, [item['book_id'] for item in items]
            )
        finally:
            import shutil

            shutil.rmtree(tmp, ignore_errors=True)
        self._reload()


def _join(value) -> str:
    if value is None:
        return ''
    if isinstance(value, str):
        return value
    return ', '.join(str(item) for item in value if str(item).strip())
