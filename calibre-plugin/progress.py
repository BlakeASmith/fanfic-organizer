# -*- coding: utf-8 -*-
"""Background import progress UI for the AO3 Scraper plugin."""

from __future__ import annotations

import re
import shutil
import traceback
from typing import Any

from PyQt5.Qt import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QPlainTextEdit,
    QProgressBar,
    QThread,
    QVBoxLayout,
    Qt,
    pyqtSignal,
)

from calibre.gui2 import error_dialog, info_dialog

from calibre_plugins.ao3_scraper.columns import ensure_plugin_columns
from calibre_plugins.ao3_scraper.enrich import EnrichCancelled, enrich_records_via_ao3kit
from calibre_plugins.ao3_scraper.importer import import_records
from calibre_plugins.ao3_scraper.jsonl_loader import load_import_source

_PROGRESS_RE = re.compile(r'\[(\d+)/(\d+)\]')


class ImportWorker(QThread):
    """Load + optionally enrich records off the GUI thread."""

    status = pyqtSignal(str)
    progress = pyqtSignal(int, int)  # current, total (0,0 = busy/indeterminate)
    finished_ok = pyqtSignal(object)  # payload dict
    failed = pyqtSignal(str)

    def __init__(
        self,
        path: str,
        *,
        simplify_tags: bool,
        parent=None,
    ):
        super().__init__(parent)
        self.path = path
        self.simplify_tags = simplify_tags
        self._cancel = False
        self._enrich_handle = None

    def request_cancel(self) -> None:
        self._cancel = True
        handle = self._enrich_handle
        if handle is not None:
            handle.cancel()

    def _on_status(self, message: str) -> None:
        self.status.emit(message)
        match = _PROGRESS_RE.search(message)
        if match:
            self.progress.emit(int(match.group(1)), int(match.group(2)))

    def run(self) -> None:
        cleanup_dir = None
        try:
            self.progress.emit(0, 0)
            self.status.emit(f'Loading import file…\n{self.path}')
            records, bundle_root, cleanup_dir = load_import_source(self.path)
            if self._cancel:
                raise EnrichCancelled('Cancelled while loading.')
            if not records:
                raise ValueError('The import file contains no records.')

            self.status.emit(f'Loaded {len(records)} work(s).')
            enrich_note = ''

            if self.simplify_tags:
                self.progress.emit(0, len(records))
                self.status.emit(
                    'Starting tag simplification (AO3 lookups + user rules)…'
                )
                from calibre_plugins.ao3_scraper.enrich import EnrichHandle

                self._enrich_handle = EnrichHandle()
                records, enrich_error = enrich_records_via_ao3kit(
                    records,
                    on_status=self._on_status,
                    handle=self._enrich_handle,
                    should_cancel=lambda: self._cancel,
                )
                self._enrich_handle = None
                if self._cancel:
                    raise EnrichCancelled('Cancelled during tag simplification.')
                if enrich_error:
                    enrich_note = f'\n\nTag simplification skipped: {enrich_error}'
                    self.status.emit(f'Tag simplification skipped:\n{enrich_error}')
                else:
                    cleaned_count = sum(
                        1
                        for record in records
                        if isinstance(record.get('cleaned'), dict)
                        and record['cleaned'].get('source') == 'rules'
                    )
                    enrich_note = (
                        f'\n\nSimplified tags for {cleaned_count}/{len(records)} works.'
                    )
                    self.status.emit(
                        f'Tag simplification finished ({cleaned_count}/{len(records)}).'
                    )
            else:
                self.status.emit('Tag simplification disabled for this import.')

            self.finished_ok.emit(
                {
                    'records': records,
                    'bundle_root': bundle_root,
                    'cleanup_dir': cleanup_dir,
                    'enrich_note': enrich_note,
                }
            )
        except EnrichCancelled as exc:
            if cleanup_dir:
                shutil.rmtree(cleanup_dir, ignore_errors=True)
            self.failed.emit(str(exc))
        except Exception:
            if cleanup_dir:
                shutil.rmtree(cleanup_dir, ignore_errors=True)
            self.failed.emit(traceback.format_exc())


class ImportProgressDialog(QDialog):
    """Non-blocking progress window with a live log."""

    def __init__(self, gui, *, path: str, simplify_tags: bool, update_existing: bool):
        super().__init__(gui)
        self.gui = gui
        self.update_existing = update_existing
        self.setWindowTitle('AO3 Scraper — Import')
        self.setMinimumSize(640, 420)
        self.setWindowModality(Qt.NonModal)
        self.setAttribute(Qt.WA_DeleteOnClose, True)

        layout = QVBoxLayout(self)

        self.headline = QLabel('Preparing import…')
        self.headline.setWordWrap(True)
        layout.addWidget(self.headline)

        self.bar = QProgressBar()
        self.bar.setMinimum(0)
        self.bar.setMaximum(0)  # busy
        layout.addWidget(self.bar)

        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        try:
            # Qt6 / Calibre 6+
            self.log.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        except AttributeError:
            # Qt5
            self.log.setLineWrapMode(QPlainTextEdit.NoWrap)
        layout.addWidget(self.log)

        self.buttons = QDialogButtonBox(QDialogButtonBox.Cancel)
        self.buttons.rejected.connect(self._on_cancel)
        layout.addWidget(self.buttons)

        self._worker = ImportWorker(path, simplify_tags=simplify_tags, parent=self)
        self._worker.status.connect(self._on_status)
        self._worker.progress.connect(self._on_progress)
        self._worker.finished_ok.connect(self._on_worker_finished)
        self._worker.failed.connect(self._on_worker_failed)
        self._closing = False

        self._worker.start()

    def _append(self, message: str) -> None:
        text = (message or '').rstrip()
        if not text:
            return
        self.log.appendPlainText(text)
        self.log.verticalScrollBar().setValue(self.log.verticalScrollBar().maximum())

    def _on_status(self, message: str) -> None:
        self._append(message)
        first = message.strip().splitlines()[0] if message.strip() else ''
        if first:
            self.headline.setText(first[:200])

    def _on_progress(self, current: int, total: int) -> None:
        if total <= 0:
            self.bar.setMaximum(0)
            return
        self.bar.setMaximum(total)
        self.bar.setValue(min(current, total))

    def _on_cancel(self) -> None:
        if self._worker.isRunning():
            self.headline.setText('Cancelling…')
            self._append('Cancel requested…')
            self.buttons.setEnabled(False)
            self._worker.request_cancel()
            return
        self.reject()

    def closeEvent(self, event) -> None:
        if self._worker.isRunning() and not self._closing:
            self._on_cancel()
            event.ignore()
            return
        super().closeEvent(event)

    def _on_worker_failed(self, detail: str) -> None:
        self.buttons.setEnabled(True)
        self.buttons.clear()
        self.buttons.addButton(QDialogButtonBox.Close)
        self.buttons.rejected.connect(self.reject)
        self.buttons.accepted.connect(self.reject)
        if detail.lower().startswith('cancelled'):
            self.headline.setText('Import cancelled.')
            self._append(detail)
            self.bar.setMaximum(1)
            self.bar.setValue(0)
            return
        self.headline.setText('Import failed.')
        self._append(detail)
        error_dialog(
            self.gui,
            'AO3 Scraper',
            'Import failed during load/tag simplification.',
            det_msg=detail,
            show=True,
        )

    def _on_worker_finished(self, payload: dict[str, Any]) -> None:
        cleanup_dir = payload.get('cleanup_dir')
        try:
            self.headline.setText('Writing books into Calibre library…')
            self._append('Writing books into Calibre library…')
            self.bar.setMaximum(0)
            self.buttons.setEnabled(False)

            db = self.gui.current_db
            ensure_plugin_columns(db)
            records = payload['records']
            bundle_root = payload.get('bundle_root')
            outcomes = import_records(
                db,
                records,
                update_existing=self.update_existing,
                bundle_root=str(bundle_root) if bundle_root else None,
            )

            added = sum(1 for x in outcomes if x['action'] == 'added')
            updated = sum(1 for x in outcomes if x['action'] == 'updated')
            skipped = sum(1 for x in outcomes if x['action'] == 'skipped')
            epubs = sum(1 for x in outcomes if x.get('epub'))
            summary = (
                f'Imported {len(outcomes)} works '
                f'({added} added, {updated} updated, {skipped} skipped'
                f'{f", {epubs} with EPUB" if epubs else ""}).'
                f'{payload.get("enrich_note") or ""}'
            )
            self.headline.setText('Import complete.')
            self._append(summary)
            self.bar.setMaximum(1)
            self.bar.setValue(1)
            self.gui.library_view.model().refresh()
            info_dialog(self.gui, 'AO3 Scraper', summary, show=True)
            self._closing = True
            self.accept()
        except Exception:
            detail = traceback.format_exc()
            self.headline.setText('Import failed while writing to Calibre.')
            self._append(detail)
            self.buttons.setEnabled(True)
            self.buttons.clear()
            self.buttons.addButton(QDialogButtonBox.Close)
            self.buttons.rejected.connect(self.reject)
            error_dialog(
                self.gui,
                'AO3 Scraper',
                'Import failed while writing to Calibre.',
                det_msg=detail,
                show=True,
            )
        finally:
            if cleanup_dir:
                shutil.rmtree(cleanup_dir, ignore_errors=True)
