# -*- coding: utf-8 -*-
"""Background import progress UI for the Wranglekit plugin."""

from __future__ import annotations

import re
import shutil
import tempfile
import traceback
from pathlib import Path
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

from calibre_plugins.wranglekit.cleaned import (
    canonical_work_id,
    collect_collection_lines,
    collect_remapping_lines,
    format_collection_summary,
    format_remapping_summary,
)
from calibre_plugins.wranglekit.columns import apply_layout_columns
from calibre_plugins.wranglekit.enrich import EnrichCancelled, enrich_records_via_ao3kit
from calibre_plugins.wranglekit.epub_plan import (
    merge_download_manifest,
    pending_epub_attachments,
    summarize_epub_download,
)
from calibre_plugins.wranglekit.importer import (
    attach_downloaded_epubs,
    import_records,
    refresh_library_ui,
)
from calibre_plugins.wranglekit.jsonl_loader import (
    load_import_source,
    load_jsonl_records,
    resolve_epub_path,
)
from calibre_plugins.wranglekit.selected import (
    apply_cleaned_records,
    apply_collections_records,
    apply_series_records,
    book_has_epub,
    load_selected_for_epub_download,
    load_selected_for_collections,
    load_selected_records,
)

_PROGRESS_RE = re.compile(r'\[(\d+)/(\d+)\]')
_UNIQUE_TAGS_RE = re.compile(
    r'(\d+) unique tags across batch \((\d+) already cached, (\d+) need AO3'
)


_SETUP_STATUS_RE = re.compile(
    r'^(Looking for a Python|Trying /\S|Using /\S|Running:)'
)
_WROTE_WORKS_RE = re.compile(r'^Wrote (\d+) works to ')
_DOWNLOAD_ARROW_RE = re.compile(r'^(Downloaded .+?)\s*→\s+.+')


def _book_noun(n: int) -> str:
    return 'book' if n == 1 else 'books'


def _user_status_line(message: str) -> str | None:
    """Drop interpreter/command plumbing; keep scrape/download progress."""
    text = (message or '').rstrip()
    if not text:
        return None
    first = text.splitlines()[0].strip()
    if _SETUP_STATUS_RE.match(first):
        return None
    if first.startswith('Tag remappings: none'):
        return None
    wrote = _WROTE_WORKS_RE.match(first)
    if wrote:
        n = int(wrote.group(1))
        noun = 'work' if n == 1 else 'works'
        return f'Found {n} matching {noun}.'
    download = _DOWNLOAD_ARROW_RE.match(first)
    if download:
        return download.group(1).rstrip('.') + '.'
    if first.startswith('Import zip:'):
        return None
    return text


def _progress_from_status(message: str) -> tuple[int, int] | None:
    unique = _UNIQUE_TAGS_RE.search(message)
    if unique:
        need = int(unique.group(3))
        return 0, need if need else int(unique.group(1))
    match = _PROGRESS_RE.search(message)
    if match:
        return int(match.group(1)), int(match.group(2))
    return None


def _finish_with_remaps(
    summary: str, records: list[dict[str, Any]]
) -> tuple[str, str]:
    remap_text = format_remapping_summary(records)
    n = len(collect_remapping_lines(records))
    if n:
        summary = (
            summary.rstrip('.')
            + f'. {n} unique tag remapping(s) — see details and the log below.'
        )
    else:
        summary = summary.rstrip('.') + '. No tag remappings.'
    return summary, remap_text


def _finish_with_collections(
    summary: str, records: list[dict[str, Any]]
) -> tuple[str, str]:
    remap_text = format_collection_summary(records)
    n = len(collect_collection_lines(records))
    if n:
        summary = (
            summary.rstrip('.')
            + f'. {n} unique collection assignment(s) — see details and the log below.'
        )
    else:
        summary = summary.rstrip('.') + '. No collection matches.'
    return summary, remap_text


def _keep_open_with_close(dialog) -> None:
    try:
        dialog.buttons.rejected.disconnect(dialog._on_cancel)
    except TypeError:
        pass
    dialog.buttons.setEnabled(True)
    dialog.buttons.clear()
    dialog.buttons.addButton(QDialogButtonBox.Close)
    dialog.buttons.rejected.connect(dialog.accept)
    dialog.buttons.accepted.connect(dialog.accept)


def _apply_progress_bar(bar: QProgressBar, current: int, total: int) -> None:
    if total <= 0:
        bar.setMaximum(0)
        bar.setFormat('Working…')
        return
    bar.setMaximum(total)
    bar.setValue(min(current, total))
    bar.setFormat('%v / %m  (%p%)')


def write_import_payload(
    gui, payload: dict[str, Any], *, update_existing: bool,
    skip_existing_epub: bool = False,
) -> tuple[str, str, list[int]]:
    db = apply_layout_columns(gui)
    records = payload['records']
    bundle_root = payload.get('bundle_root')
    outcomes = import_records(
        db,
        records,
        update_existing=update_existing,
        bundle_root=str(bundle_root) if bundle_root else None,
        skip_existing_epub=skip_existing_epub,
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
    summary, remap_text = _finish_with_remaps(summary, records)
    book_ids = [
        item['book_id'] for item in outcomes if item.get('book_id') is not None
    ]
    return summary, remap_text, book_ids


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
        include_series: bool = False,
        parent=None,
    ):
        super().__init__(parent)
        self.path = path
        self.simplify_tags = simplify_tags
        self.include_series = include_series
        self._cancel = False
        self._enrich_handle = None

    def request_cancel(self) -> None:
        self._cancel = True
        handle = self._enrich_handle
        if handle is not None:
            handle.cancel()

    def _on_status(self, message: str) -> None:
        visible = _user_status_line(message)
        if visible is None:
            return
        self.status.emit(visible)
        parsed = _progress_from_status(visible)
        if parsed is not None:
            self.progress.emit(*parsed)

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

            if self.include_series:
                from calibre_plugins.wranglekit.enrich import EnrichHandle, run_ao3kit
                from calibre_plugins.wranglekit.prefs import plugin_runtime_settings
                from calibre_plugins.wranglekit.scrape_run import (
                    merge_plugin_settings,
                    prepare_series_from_command,
                )

                self.progress.emit(0, 0)
                self.status.emit(
                    'Fetching other works in the same AO3 series…'
                )
                tmp = tempfile.mkdtemp(prefix='ao3-series-')
                options = merge_plugin_settings(
                    {'download_epubs': False},
                    plugin_runtime_settings(),
                )
                argv, jsonl, dest = prepare_series_from_command(
                    records, tmp, options
                )
                self._enrich_handle = EnrichHandle()
                code, stdout, stderr = run_ao3kit(
                    argv,
                    on_status=self._on_status,
                    handle=self._enrich_handle,
                    should_cancel=lambda: self._cancel,
                    cancel_message='Cancelled while fetching series.',
                )
                self._enrich_handle = None
                if self._cancel:
                    shutil.rmtree(tmp, ignore_errors=True)
                    raise EnrichCancelled('Cancelled while fetching series.')
                if code != 0:
                    shutil.rmtree(tmp, ignore_errors=True)
                    detail = (stderr or stdout or '').strip() or f'exit {code}'
                    raise ValueError(f'Series expand failed:\n{detail}')
                records = load_jsonl_records(jsonl)
                shutil.rmtree(tmp, ignore_errors=True)
                self.status.emit(
                    f'Series expand finished ({len(records)} work(s)).'
                )

            if self.simplify_tags:
                self.progress.emit(0, 0)
                self.status.emit(
                    'Starting simplification of tags, fandoms, and relationships '
                    '(AO3 lookups + user rules)…'
                )
                from calibre_plugins.wranglekit.enrich import EnrichHandle

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

    def __init__(
        self, gui, *, path: str, simplify_tags: bool, update_existing: bool,
        include_series: bool = False,
    ):
        super().__init__(gui)
        self.gui = gui
        self.update_existing = update_existing
        self.setWindowTitle('Wranglekit — Import')
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
        self.bar.setTextVisible(True)
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

        self._worker = ImportWorker(
            path,
            simplify_tags=simplify_tags,
            include_series=include_series,
            parent=self,
        )
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
        _apply_progress_bar(self.bar, current, total)

    def _on_cancel(self) -> None:
        if self._closing:
            self.accept()
            return
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
            'Wranglekit',
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

            summary, remap_text, book_ids = write_import_payload(
                self.gui,
                payload,
                update_existing=self.update_existing,
            )
            self.headline.setText('Import complete.')
            self._append(summary)
            self._append(remap_text)
            self.bar.setMaximum(1)
            self.bar.setValue(1)
            refresh_library_ui(self.gui, book_ids)
            info_dialog(self.gui, 'Wranglekit', summary, det_msg=remap_text, show=True)
            self._closing = True
            _keep_open_with_close(self)
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
                'Wranglekit',
                'Import failed while writing to Calibre.',
                det_msg=detail,
                show=True,
            )
        finally:
            if cleanup_dir:
                shutil.rmtree(cleanup_dir, ignore_errors=True)


class ScrapeImportWorker(QThread):
    """Scrape AO3, optionally download EPUBs, optionally enrich, then hand off records."""

    status = pyqtSignal(str)
    progress = pyqtSignal(int, int)
    finished_ok = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, options: dict[str, Any], *, parent=None):
        super().__init__(parent)
        self.options = dict(options)
        self._cancel = False
        self._handle = None

    def request_cancel(self) -> None:
        self._cancel = True
        handle = self._handle
        if handle is not None:
            handle.cancel()

    def _on_status(self, message: str) -> None:
        visible = _user_status_line(message)
        if visible is None:
            return
        self.status.emit(visible)
        parsed = _progress_from_status(visible)
        if parsed is not None:
            self.progress.emit(*parsed)

    def run(self) -> None:
        from calibre_plugins.wranglekit.enrich import EnrichHandle, run_ao3kit
        from calibre_plugins.wranglekit.scrape_run import (
            describe_scrape,
            prepare_scrape_command,
        )

        tmp = tempfile.mkdtemp(prefix='ao3-scrape-')
        cleanup_dir = tmp
        try:
            self.progress.emit(0, 0)
            self.status.emit(describe_scrape(self.options))
            argv, jsonl = prepare_scrape_command(self.options, tmp)
            self._handle = EnrichHandle()
            code, stdout, stderr = run_ao3kit(
                argv,
                on_status=self._on_status,
                handle=self._handle,
                should_cancel=lambda: self._cancel,
                cancel_message='Cancelled during search.',
            )
            self._handle = None
            if self._cancel:
                raise EnrichCancelled('Cancelled during search.')
            if code != 0:
                detail = (stderr or stdout or '').strip() or '(no output)'
                raise ValueError(f'Search failed (exit {code}):\n{detail}')
            if not jsonl.is_file():
                raise ValueError('Search produced no results file.')

            records = load_jsonl_records(jsonl)
            if not records:
                self.status.emit(
                    'No works matched that search and those filters.'
                )
                self.finished_ok.emit(
                    {
                        'records': [],
                        'bundle_root': jsonl.parent,
                        'cleanup_dir': cleanup_dir,
                        'enrich_note': '',
                        'no_matches': True,
                    }
                )
                return
            bundle_root: Path = jsonl.parent
            if self.options.get('download_epubs'):
                n_epub = sum(1 for record in records if record.get('epub_file'))
                self.status.emit(
                    f'Found {len(records)} work(s); {n_epub} EPUB(s) ready to import.'
                )
            else:
                self.status.emit(f'Scraped {len(records)} work(s).')

            enrich_note = ''
            if self.options.get('simplify_tags'):
                self.progress.emit(0, 0)
                self.status.emit(
                    'Starting simplification of tags, fandoms, and relationships '
                    '(AO3 lookups + user rules)…'
                )
                self._handle = EnrichHandle()
                records, enrich_error = enrich_records_via_ao3kit(
                    records,
                    on_status=self._on_status,
                    handle=self._handle,
                    should_cancel=lambda: self._cancel,
                )
                self._handle = None
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

            self.finished_ok.emit(
                {
                    'records': records,
                    'bundle_root': bundle_root,
                    'cleanup_dir': cleanup_dir,
                    'enrich_note': enrich_note,
                }
            )
        except EnrichCancelled as exc:
            shutil.rmtree(cleanup_dir, ignore_errors=True)
            self.failed.emit(str(exc))
        except Exception:
            shutil.rmtree(cleanup_dir, ignore_errors=True)
            self.failed.emit(traceback.format_exc())


class ScrapeImportDialog(QDialog):
    """Progress window for Search AO3 (optional EPUBs in the same run) → import."""

    def __init__(self, gui, *, options: dict[str, Any]):
        super().__init__(gui)
        self.gui = gui
        self.update_existing = bool(options.get('update_existing', True))
        self.setWindowTitle('Wranglekit — Search and import')
        self.setMinimumSize(640, 420)
        self.setWindowModality(Qt.NonModal)
        self.setAttribute(Qt.WA_DeleteOnClose, True)

        layout = QVBoxLayout(self)

        self.headline = QLabel('Searching AO3…')
        self.headline.setWordWrap(True)
        layout.addWidget(self.headline)

        self.bar = QProgressBar()
        self.bar.setMinimum(0)
        self.bar.setMaximum(0)
        self.bar.setTextVisible(True)
        layout.addWidget(self.bar)

        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        try:
            self.log.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        except AttributeError:
            self.log.setLineWrapMode(QPlainTextEdit.NoWrap)
        layout.addWidget(self.log)

        self.buttons = QDialogButtonBox(QDialogButtonBox.Cancel)
        self.buttons.rejected.connect(self._on_cancel)
        layout.addWidget(self.buttons)

        self._worker = ScrapeImportWorker(options, parent=self)
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
        _apply_progress_bar(self.bar, current, total)

    def _on_cancel(self) -> None:
        if self._closing:
            self.accept()
            return
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
            self.headline.setText('Search cancelled.')
            self._append(detail)
            self.bar.setMaximum(1)
            self.bar.setValue(0)
            return
        self.headline.setText('Search/import failed.')
        self._append(detail)
        error_dialog(
            self.gui,
            'Wranglekit',
            'Search or download failed.',
            det_msg=detail,
            show=True,
        )

    def _on_worker_finished(self, payload: dict[str, Any]) -> None:
        cleanup_dir = payload.get('cleanup_dir')
        try:
            if payload.get('no_matches') or not payload.get('records'):
                summary = (
                    'No works matched that search and those filters. '
                    'Try lowering min score / kudos / words, or raising max results.'
                )
                self.headline.setText('No matching works.')
                self._append(summary)
                self.bar.setMaximum(1)
                self.bar.setValue(1)
                info_dialog(self.gui, 'Wranglekit', summary, show=True)
                self._closing = True
                _keep_open_with_close(self)
                return

            self.headline.setText('Writing books into Calibre library…')
            self._append('Writing books into Calibre library…')
            self.bar.setMaximum(0)
            self.buttons.setEnabled(False)

            summary, remap_text, book_ids = write_import_payload(
                self.gui,
                payload,
                update_existing=self.update_existing,
            )
            self.headline.setText('Import complete.')
            self._append(summary)
            self._append(remap_text)
            self.bar.setMaximum(1)
            self.bar.setValue(1)
            refresh_library_ui(self.gui, book_ids)
            info_dialog(self.gui, 'Wranglekit', summary, det_msg=remap_text, show=True)
            self._closing = True
            _keep_open_with_close(self)
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
                'Wranglekit',
                'Import failed while writing to Calibre.',
                det_msg=detail,
                show=True,
            )
        finally:
            if cleanup_dir:
                shutil.rmtree(cleanup_dir, ignore_errors=True)


class SimplifySelectedWorker(QThread):
    """Simplify tags, fandoms, and relationships for already-imported books."""

    status = pyqtSignal(str)
    progress = pyqtSignal(int, int)
    finished_ok = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, book_ids: list[int], *, collections_only: bool = False, parent=None):
        super().__init__(parent)
        self.book_ids = list(book_ids)
        self.collections_only = bool(collections_only)
        self._cancel = False
        self._enrich_handle = None
        self._db = None

    def set_db(self, db) -> None:
        # Calibre DB handle must be used carefully; we only read in the worker
        # and write back on the GUI thread.
        self._db = db

    def request_cancel(self) -> None:
        self._cancel = True
        handle = self._enrich_handle
        if handle is not None:
            handle.cancel()

    def _on_status(self, message: str) -> None:
        visible = _user_status_line(message)
        if visible is None:
            return
        self.status.emit(visible)
        parsed = _progress_from_status(visible)
        if parsed is not None:
            self.progress.emit(*parsed)

    def run(self) -> None:
        try:
            if self._db is None:
                raise ValueError('Library database was not provided.')
            self.progress.emit(0, 0)
            if self.collections_only:
                self.status.emit(
                    f'Loading {len(self.book_ids)} selected book(s) for '
                    'collection recompute…'
                )
                ready, skipped = load_selected_for_collections(
                    self._db, self.book_ids
                )
                empty_error = 'None of the selected books could be loaded.'
                working = (
                    f'Recomputing collections for {len(ready)} book(s)…'
                    if ready
                    else ''
                )
                done = 'Collection recompute finished'
                cancel_during = 'Cancelled while recomputing collections.'
            else:
                self.status.emit(
                    f'Loading AO3 metadata for {len(self.book_ids)} selected book(s)…'
                )
                ready, skipped = load_selected_records(self._db, self.book_ids)
                empty_error = (
                    'None of the selected books have an AO3 URL or work id.'
                )
                working = (
                    f'Simplifying tags, fandoms, and relationships for '
                    f'{len(ready)} book(s)…'
                    if ready
                    else ''
                )
                done = 'Simplification finished'
                cancel_during = 'Cancelled during simplification.'
            for item in skipped:
                self.status.emit(
                    f"Skipping {item.get('title') or item.get('book_id')}: "
                    f"{item.get('reason')}"
                )
            if self._cancel:
                raise EnrichCancelled('Cancelled while loading selection.')
            if not ready:
                raise ValueError(empty_error)

            records = [item['record'] for item in ready]
            self.status.emit(working)
            self.progress.emit(0, 0)

            from calibre_plugins.wranglekit.enrich import EnrichHandle

            self._enrich_handle = EnrichHandle()
            enriched, enrich_error = enrich_records_via_ao3kit(
                records,
                on_status=self._on_status,
                handle=self._enrich_handle,
                should_cancel=lambda: self._cancel,
                force=True,
                collections_recompute=self.collections_only,
            )
            self._enrich_handle = None
            if self._cancel:
                raise EnrichCancelled(cancel_during)
            if enrich_error:
                raise ValueError(enrich_error)

            for item, record in zip(ready, enriched, strict=True):
                item['record'] = record

            cleaned_count = sum(
                1
                for item in ready
                if isinstance(item['record'].get('cleaned'), dict)
                and item['record']['cleaned'].get('source') == 'rules'
            )
            self.status.emit(f'{done} ({cleaned_count}/{len(ready)}).')
            self.finished_ok.emit(
                {
                    'items': ready,
                    'skipped': skipped,
                    'cleaned_count': cleaned_count,
                    'collections_only': self.collections_only,
                }
            )
        except EnrichCancelled as exc:
            self.failed.emit(str(exc))
        except Exception:
            self.failed.emit(traceback.format_exc())


class SimplifySelectedDialog(QDialog):
    """Progress UI for simplifying tags on the current selection."""

    def __init__(self, gui, book_ids: list[int], *, collections_only: bool = False):
        super().__init__(gui)
        self.gui = gui
        self.collections_only = bool(collections_only)
        if self.collections_only:
            title = 'Wranglekit — Recompute collections'
            headline = (
                f'Recomputing collections for {len(book_ids)} selected '
                f'{_book_noun(len(book_ids))}…'
            )
        else:
            title = 'Wranglekit — Simplify selected'
            headline = (
                f'Simplifying tags, fandoms, and relationships for '
                f'{len(book_ids)} selected book(s)…'
            )
        self.setWindowTitle(title)
        self.setMinimumSize(640, 420)
        self.setWindowModality(Qt.NonModal)
        self.setAttribute(Qt.WA_DeleteOnClose, True)

        layout = QVBoxLayout(self)

        self.headline = QLabel(headline)
        self.headline.setWordWrap(True)
        layout.addWidget(self.headline)

        self.bar = QProgressBar()
        self.bar.setMinimum(0)
        self.bar.setMaximum(0)
        self.bar.setTextVisible(True)
        layout.addWidget(self.bar)

        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        try:
            self.log.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        except AttributeError:
            self.log.setLineWrapMode(QPlainTextEdit.NoWrap)
        layout.addWidget(self.log)

        self.buttons = QDialogButtonBox(QDialogButtonBox.Cancel)
        self.buttons.rejected.connect(self._on_cancel)
        layout.addWidget(self.buttons)

        self._worker = SimplifySelectedWorker(
            book_ids, collections_only=self.collections_only, parent=self
        )
        self._worker.set_db(gui.current_db)
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
        _apply_progress_bar(self.bar, current, total)

    def _on_cancel(self) -> None:
        if self._closing:
            self.accept()
            return
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
            self.headline.setText(
                'Collection recompute cancelled.'
                if self.collections_only
                else 'Simplification cancelled.'
            )
            self._append(detail)
            self.bar.setMaximum(1)
            self.bar.setValue(0)
            return
        self.headline.setText(
            'Collection recompute failed.'
            if self.collections_only
            else 'Simplification failed.'
        )
        self._append(detail)
        error_dialog(
            self.gui,
            'Wranglekit',
            (
                'Recomputing collections failed for the selection.'
                if self.collections_only
                else 'Tag simplification failed for the selection.'
            ),
            det_msg=detail,
            show=True,
        )

    def _on_worker_finished(self, payload: dict[str, Any]) -> None:
        try:
            self.bar.setMaximum(0)
            self.buttons.setEnabled(False)

            db = apply_layout_columns(self.gui)
            skipped = payload.get('skipped') or []
            records = [item['record'] for item in payload['items']]
            if self.collections_only:
                self.headline.setText('Writing collections into Calibre…')
                self._append('Writing collections into Calibre…')
                outcomes = apply_collections_records(db, payload['items'])
                updated = sum(1 for item in outcomes if item.get('action') == 'updated')
                summary = (
                    f'Recomputed collections on {updated} of {len(outcomes)} '
                    f'{_book_noun(len(outcomes))}'
                    + (
                        f'; skipped {len(skipped)}.'
                        if skipped
                        else '.'
                    )
                )
                summary, remap_text = _finish_with_collections(summary, records)
            else:
                self.headline.setText('Writing cleaned metadata into Calibre…')
                self._append('Writing cleaned metadata into Calibre…')
                outcomes = apply_cleaned_records(db, payload['items'])
                summary = (
                    f'Simplified tags for {len(outcomes)} book(s)'
                    f' ({payload.get("cleaned_count", len(outcomes))} with rules source)'
                    + (
                        f'; skipped {len(skipped)} without an AO3 URL / work id.'
                        if skipped
                        else '.'
                    )
                )
                summary, remap_text = _finish_with_remaps(summary, records)
            self.headline.setText('Done.')
            self._append(summary)
            self._append(remap_text)
            self.bar.setMaximum(1)
            self.bar.setValue(1)
            refresh_library_ui(
                self.gui,
                [item['book_id'] for item in outcomes if item.get('book_id') is not None],
            )
            info_dialog(self.gui, 'Wranglekit', summary, det_msg=remap_text, show=True)
            self._closing = True
            _keep_open_with_close(self)
        except Exception:
            detail = traceback.format_exc()
            self.headline.setText('Failed while writing cleaned metadata.')
            self._append(detail)
            self.buttons.setEnabled(True)
            self.buttons.clear()
            self.buttons.addButton(QDialogButtonBox.Close)
            self.buttons.rejected.connect(self.reject)
            error_dialog(
                self.gui,
                'Wranglekit',
                'Failed while writing cleaned metadata.',
                det_msg=detail,
                show=True,
            )


class ApplyCollectionsDialog(SimplifySelectedDialog):
    """Progress UI for recomputing collections without rewriting tags."""

    def __init__(self, gui, book_ids: list[int]):
        super().__init__(gui, book_ids, collections_only=True)


class DownloadSelectedWorker(QThread):
    """Download native EPUBs for selected books that do not already have one."""

    status = pyqtSignal(str)
    progress = pyqtSignal(int, int)
    epub_ready = pyqtSignal(object)
    finished_ok = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, book_ids: list[int], *, tmp: str, parent=None):
        super().__init__(parent)
        self.book_ids = list(book_ids)
        self._tmp = tmp
        self._cancel = False
        self._handle = None
        self._db = None
        self._ready: list[dict[str, Any]] = []
        self._jsonl: Path | None = None
        self._dest: Path | None = None
        self._seen_book_ids: set[Any] = set()

    def set_db(self, db) -> None:
        self._db = db

    def request_cancel(self) -> None:
        self._cancel = True
        handle = self._handle
        if handle is not None:
            handle.cancel()

    def _on_status(self, message: str) -> None:
        visible = _user_status_line(message)
        if visible is None:
            return
        self.status.emit(visible)
        parsed = _progress_from_status(visible)
        if parsed is not None:
            self.progress.emit(*parsed)

    def _poll_ready_epubs(self) -> None:
        jsonl = self._jsonl
        dest = self._dest
        if not jsonl or not dest or not self._ready or not jsonl.is_file():
            return
        try:
            downloaded = load_jsonl_records(jsonl)
        except (OSError, ValueError):
            return
        for item in pending_epub_attachments(
            self._ready, downloaded, self._seen_book_ids
        ):
            if resolve_epub_path(item.get('record') or {}, dest) is None:
                continue
            self._seen_book_ids.add(item['book_id'])
            self.epub_ready.emit({'item': item, 'bundle_root': str(dest)})

    def _finish_payload(
        self,
        skipped: list[dict[str, Any]],
        *,
        cancelled: bool = False,
    ) -> dict[str, Any]:
        downloaded: list[dict[str, Any]] = []
        if self._jsonl is not None and self._jsonl.is_file():
            try:
                downloaded = load_jsonl_records(self._jsonl)
            except (OSError, ValueError):
                downloaded = []
        items = (
            merge_download_manifest(self._ready, downloaded) if self._ready else []
        )
        return {
            'items': items,
            'skipped': skipped,
            'bundle_root': str(self._dest) if self._dest else None,
            'cleanup_dir': self._tmp,
            'cancelled': cancelled,
        }

    def run(self) -> None:
        from calibre_plugins.wranglekit.enrich import EnrichHandle, run_ao3kit
        from calibre_plugins.wranglekit.prefs import plugin_runtime_settings
        from calibre_plugins.wranglekit.scrape_run import (
            merge_plugin_settings,
            prepare_download_command,
        )

        skipped: list[dict[str, Any]] = []
        try:
            if self._db is None:
                raise ValueError('Library database was not provided.')
            self.progress.emit(0, 0)
            ready, skipped = load_selected_for_epub_download(self._db, self.book_ids)
            self._ready = ready
            for item in skipped:
                self.status.emit(
                    f"Skipping {item.get('title') or item.get('book_id')}: "
                    f"{item.get('reason')}"
                )
            if self._cancel:
                raise EnrichCancelled('Cancelled while loading selection.')
            if not ready:
                self.finished_ok.emit(self._finish_payload(skipped))
                return

            noun = _book_noun(len(ready))
            self.status.emit(f'Downloading native EPUBs for {len(ready)} {noun}…')
            self.progress.emit(0, len(ready))
            options = merge_plugin_settings({}, plugin_runtime_settings())
            records = [item['record'] for item in ready]
            argv, jsonl, dest = prepare_download_command(records, self._tmp, options)
            self._jsonl = jsonl
            self._dest = dest
            self._handle = EnrichHandle()
            code, stdout, stderr = run_ao3kit(
                argv,
                on_status=self._on_status,
                on_poll=self._poll_ready_epubs,
                handle=self._handle,
                should_cancel=lambda: self._cancel,
                cancel_message='Cancelled during EPUB download.',
            )
            self._handle = None
            if self._cancel:
                raise EnrichCancelled('Cancelled during EPUB download.')
            downloaded: list[dict[str, Any]] = []
            if jsonl.is_file():
                downloaded = load_jsonl_records(jsonl)
            if code != 0 and not any(item.get('epub_file') for item in downloaded):
                detail = (stderr or stdout or '').strip() or '(no output)'
                raise ValueError(f'EPUB download failed (exit {code}):\n{detail}')

            self._poll_ready_epubs()
            self.finished_ok.emit(self._finish_payload(skipped))
        except EnrichCancelled:
            self._poll_ready_epubs()
            self.finished_ok.emit(self._finish_payload(skipped, cancelled=True))
        except Exception:
            self.failed.emit(traceback.format_exc())


class DownloadSelectedDialog(QDialog):
    """Progress UI for downloading missing EPUBs onto the current selection."""

    def __init__(self, gui, book_ids: list[int]):
        super().__init__(gui)
        self.gui = gui
        self.setWindowTitle('Wranglekit — Download EPUBs')
        self.setMinimumSize(640, 420)
        self.setWindowModality(Qt.NonModal)
        self.setAttribute(Qt.WA_DeleteOnClose, True)

        self._cleanup_dir = tempfile.mkdtemp(prefix='ao3-epub-')
        self._outcomes: list[dict[str, Any]] = []
        self._attached_ids: set[int] = set()
        self._bundle_root: str | None = None
        self._total = 0
        self._closing = False

        layout = QVBoxLayout(self)

        n = len(book_ids)
        self.headline = QLabel(
            f'Downloading missing EPUBs for {n} selected {_book_noun(n)}…'
        )
        self.headline.setWordWrap(True)
        layout.addWidget(self.headline)

        self.bar = QProgressBar()
        self.bar.setMinimum(0)
        self.bar.setMaximum(0)
        self.bar.setTextVisible(True)
        layout.addWidget(self.bar)

        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        try:
            self.log.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        except AttributeError:
            self.log.setLineWrapMode(QPlainTextEdit.NoWrap)
        layout.addWidget(self.log)

        self.buttons = QDialogButtonBox(QDialogButtonBox.Cancel)
        self.buttons.rejected.connect(self._on_cancel)
        layout.addWidget(self.buttons)

        self._worker = DownloadSelectedWorker(
            book_ids, tmp=self._cleanup_dir, parent=self
        )
        self._worker.set_db(gui.current_db)
        self._worker.status.connect(self._on_status)
        self._worker.progress.connect(self._on_progress)
        self._worker.epub_ready.connect(self._on_epub_ready)
        self._worker.finished_ok.connect(self._on_worker_finished)
        self._worker.failed.connect(self._on_worker_failed)
        self._worker.start()

    def _append(self, message: str) -> None:
        text = (message or '').rstrip()
        if not text:
            return
        self.log.appendPlainText(text)
        self.log.verticalScrollBar().setValue(self.log.verticalScrollBar().maximum())

    def _on_status(self, message: str) -> None:
        self._append(message)

    def _on_progress(self, current: int, total: int) -> None:
        if total > 0:
            self._total = total
        _apply_progress_bar(self.bar, current, total)
        self._refresh_headline()

    def _refresh_headline(self) -> None:
        if self._closing:
            return
        added = len(self._attached_ids)
        total = self._total
        if not self._worker.isRunning():
            return
        if total <= 0:
            return
        if added and added < total:
            self.headline.setText(
                f'Added EPUB to {added} of {total}. Still downloading…'
            )
        elif added == 0:
            self.headline.setText(
                f'Downloading native EPUBs for {total} {_book_noun(total)}…'
            )
        else:
            self.headline.setText(f'Added EPUB to {added} of {total}.')

    def _on_epub_ready(self, payload: dict[str, Any]) -> None:
        item = payload.get('item') or {}
        bundle_root = payload.get('bundle_root') or self._bundle_root
        if bundle_root:
            self._bundle_root = str(bundle_root)
        book_id = item.get('book_id')
        if book_id is None or book_id in self._attached_ids:
            return
        title = item.get('title') or (item.get('record') or {}).get('title') or book_id
        db = self.gui.current_db
        if book_has_epub(db, book_id):
            self._append(f'Skipping {title}: already has an EPUB')
            self._attached_ids.add(int(book_id))
            return
        outcomes = attach_downloaded_epubs(
            db, [item], bundle_root=self._bundle_root
        )
        for outcome in outcomes:
            self._outcomes.append(outcome)
            name = outcome.get('title') or title
            if outcome.get('epub'):
                self._attached_ids.add(int(outcome['book_id']))
                self._append(f'Added EPUB to {name}.')
                refresh_library_ui(self.gui, [outcome['book_id']])
            elif outcome.get('action') == 'failed':
                self._append(
                    f'Could not add EPUB to {name}: {outcome.get("reason")}'
                )
        self._refresh_headline()

    def _attach_remaining(
        self,
        items: list[dict[str, Any]],
        bundle_root: str | None,
        *,
        cancelled: bool,
    ) -> None:
        for item in items:
            book_id = item.get('book_id')
            if book_id in self._attached_ids:
                continue
            record = item.get('record') or {}
            if record.get('epub_file') and bundle_root:
                self._on_epub_ready({'item': item, 'bundle_root': bundle_root})
                continue
            if record.get('epub_error'):
                self._outcomes.append(
                    {
                        'book_id': book_id,
                        'title': item.get('title') or record.get('title'),
                        'action': 'failed',
                        'reason': record.get('epub_error'),
                        'epub': False,
                    }
                )
                continue
            if cancelled:
                continue
            self._outcomes.append(
                {
                    'book_id': book_id,
                    'title': item.get('title') or record.get('title'),
                    'action': 'failed',
                    'reason': 'no EPUB file',
                    'epub': False,
                }
            )

    def _cleanup_temp(self) -> None:
        if self._cleanup_dir:
            shutil.rmtree(self._cleanup_dir, ignore_errors=True)
            self._cleanup_dir = None

    def _show_close_button(self) -> None:
        self.buttons.setEnabled(True)
        self.buttons.clear()
        self.buttons.addButton(QDialogButtonBox.Close)
        self.buttons.rejected.connect(self.reject)
        self.buttons.accepted.connect(self.reject)

    def _on_cancel(self) -> None:
        if self._closing:
            self.accept()
            return
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
        self._cleanup_temp()
        super().closeEvent(event)

    def _on_worker_failed(self, detail: str) -> None:
        self._show_close_button()
        if self._outcomes:
            summary = summarize_epub_download(self._outcomes, [])
            self._append(summary)
            self.headline.setText('EPUB download failed after adding some files.')
        else:
            self.headline.setText('EPUB download failed.')
        self._append(detail)
        self.bar.setMaximum(1)
        self.bar.setValue(0)
        error_dialog(
            self.gui,
            'Wranglekit',
            'EPUB download failed for the selection.',
            det_msg=detail,
            show=True,
        )

    def _on_worker_finished(self, payload: dict[str, Any]) -> None:
        try:
            items = payload.get('items') or []
            skipped = payload.get('skipped') or []
            cancelled = bool(payload.get('cancelled'))
            bundle_root = payload.get('bundle_root') or self._bundle_root
            self._attach_remaining(items, bundle_root, cancelled=cancelled)

            if not items and not self._outcomes:
                summary = summarize_epub_download(
                    [], skipped, cancelled=cancelled
                )
                if not skipped and not cancelled:
                    summary = 'None of the selected books need an EPUB download.'
                self.headline.setText(
                    'Download cancelled.' if cancelled else 'Nothing to download.'
                )
                self._append(summary)
                self.bar.setMaximum(1)
                self.bar.setValue(1)
                info_dialog(self.gui, 'Wranglekit', summary, show=True)
                self._closing = True
                _keep_open_with_close(self)
                return

            summary = summarize_epub_download(
                self._outcomes, skipped, cancelled=cancelled
            )
            failed = [
                item for item in self._outcomes if item.get('action') == 'failed'
            ]
            details = '\n'.join(
                f"{item.get('title') or item.get('book_id')}: {item.get('reason')}"
                for item in failed
            )
            self.headline.setText('Download cancelled.' if cancelled else 'Done.')
            self._append(summary)
            if details:
                self._append(details)
            self.bar.setMaximum(max(self._total, 1))
            self.bar.setValue(self.bar.maximum())
            info_dialog(self.gui, 'Wranglekit', summary, det_msg=details, show=True)
            self._closing = True
            _keep_open_with_close(self)
        except Exception:
            detail = traceback.format_exc()
            self.headline.setText('Failed while adding EPUBs to Calibre.')
            self._append(detail)
            self._show_close_button()
            error_dialog(
                self.gui,
                'Wranglekit',
                'Failed while adding EPUBs to Calibre.',
                det_msg=detail,
                show=True,
            )


class ImportSeriesWorker(QThread):
    """Expand selected books into full AO3 series and import the rest."""

    status = pyqtSignal(str)
    progress = pyqtSignal(int, int)
    finished_ok = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, book_ids: list[int], *, parent=None):
        super().__init__(parent)
        self.book_ids = list(book_ids)
        self._cancel = False
        self._handle = None
        self._db = None

    def set_db(self, db) -> None:
        self._db = db

    def request_cancel(self) -> None:
        self._cancel = True
        handle = self._handle
        if handle is not None:
            handle.cancel()

    def _on_status(self, message: str) -> None:
        visible = _user_status_line(message)
        if visible is None:
            return
        self.status.emit(visible)
        parsed = _progress_from_status(visible)
        if parsed is not None:
            self.progress.emit(*parsed)

    def run(self) -> None:
        from calibre_plugins.wranglekit.enrich import EnrichHandle, run_ao3kit
        from calibre_plugins.wranglekit.prefs import plugin_runtime_settings, prefs
        from calibre_plugins.wranglekit.scrape_run import (
            merge_plugin_settings,
            prepare_series_from_command,
        )

        tmp = tempfile.mkdtemp(prefix='ao3-series-')
        cleanup_dir = tmp
        try:
            if self._db is None:
                raise ValueError('Library database was not provided.')
            self.progress.emit(0, 0)
            self.status.emit(
                f'Loading AO3 metadata for {len(self.book_ids)} selected book(s)…'
            )
            ready, skipped = load_selected_records(self._db, self.book_ids)
            for item in skipped:
                self.status.emit(
                    f"Skipping {item.get('title') or item.get('book_id')}: "
                    f"{item.get('reason')}"
                )
            if self._cancel:
                raise EnrichCancelled('Cancelled while loading selection.')
            if not ready:
                raise ValueError(
                    'None of the selected books have an AO3 URL or work id.'
                )

            records = [item['record'] for item in ready]
            options = merge_plugin_settings(
                {
                    'download_epubs': bool(prefs.get('download_epubs', True)),
                    'simplify_tags': bool(prefs.get('simplify_tags', False)),
                    'update_existing': bool(prefs.get('update_existing', True)),
                },
                plugin_runtime_settings(),
            )
            self.status.emit(
                f'Looking up series for {len(records)} selected book(s)…'
            )
            argv, jsonl, dest = prepare_series_from_command(records, tmp, options)
            self._handle = EnrichHandle()
            code, stdout, stderr = run_ao3kit(
                argv,
                on_status=self._on_status,
                handle=self._handle,
                should_cancel=lambda: self._cancel,
                cancel_message='Cancelled while fetching series.',
            )
            self._handle = None
            if self._cancel:
                raise EnrichCancelled('Cancelled while fetching series.')
            if code != 0:
                detail = (stderr or stdout or '').strip() or '(no output)'
                raise ValueError(f'Series lookup failed (exit {code}):\n{detail}')
            if not jsonl.is_file():
                raise ValueError('Series lookup produced no results file.')

            expanded = load_jsonl_records(jsonl)
            if not expanded:
                raise ValueError('No works found in those series.')

            enrich_note = ''
            if options.get('simplify_tags'):
                self.progress.emit(0, 0)
                self.status.emit(
                    'Starting simplification of tags, fandoms, and relationships '
                    '(AO3 lookups + user rules)…'
                )
                self._handle = EnrichHandle()
                expanded, enrich_error = enrich_records_via_ao3kit(
                    expanded,
                    on_status=self._on_status,
                    handle=self._handle,
                    should_cancel=lambda: self._cancel,
                )
                self._handle = None
                if self._cancel:
                    raise EnrichCancelled('Cancelled during tag simplification.')
                if enrich_error:
                    enrich_note = f'\n\nTag simplification skipped: {enrich_error}'
                    self.status.emit(f'Tag simplification skipped:\n{enrich_error}')
                else:
                    cleaned_count = sum(
                        1
                        for record in expanded
                        if isinstance(record.get('cleaned'), dict)
                        and record['cleaned'].get('source') == 'rules'
                    )
                    enrich_note = (
                        f'\n\nSimplified tags for {cleaned_count}/{len(expanded)} works.'
                    )
                    self.status.emit(
                        f'Tag simplification finished ({cleaned_count}/{len(expanded)}).'
                    )

            self.status.emit(f'Ready to import {len(expanded)} work(s) from series.')
            self.finished_ok.emit(
                {
                    'records': expanded,
                    'bundle_root': dest if options.get('download_epubs') else None,
                    'cleanup_dir': cleanup_dir,
                    'enrich_note': enrich_note,
                    'skipped': skipped,
                    'update_existing': options.get('update_existing', True),
                }
            )
        except EnrichCancelled as exc:
            shutil.rmtree(cleanup_dir, ignore_errors=True)
            self.failed.emit(str(exc))
        except Exception:
            shutil.rmtree(cleanup_dir, ignore_errors=True)
            self.failed.emit(traceback.format_exc())


class ImportSeriesDialog(QDialog):
    """Progress window for importing the rest of a series from the selection."""

    def __init__(self, gui, book_ids: list[int]):
        super().__init__(gui)
        self.gui = gui
        self.setWindowTitle('Wranglekit — Import series')
        self.setMinimumSize(640, 420)
        self.setWindowModality(Qt.NonModal)
        self.setAttribute(Qt.WA_DeleteOnClose, True)

        layout = QVBoxLayout(self)

        self.headline = QLabel('Looking up AO3 series…')
        self.headline.setWordWrap(True)
        layout.addWidget(self.headline)

        self.bar = QProgressBar()
        self.bar.setMinimum(0)
        self.bar.setMaximum(0)
        self.bar.setTextVisible(True)
        layout.addWidget(self.bar)

        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        try:
            self.log.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        except AttributeError:
            self.log.setLineWrapMode(QPlainTextEdit.NoWrap)
        layout.addWidget(self.log)

        self.buttons = QDialogButtonBox(QDialogButtonBox.Cancel)
        self.buttons.rejected.connect(self._on_cancel)
        layout.addWidget(self.buttons)

        self._worker = ImportSeriesWorker(book_ids, parent=self)
        self._worker.set_db(gui.current_db)
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
        _apply_progress_bar(self.bar, current, total)

    def _on_cancel(self) -> None:
        if self._closing:
            self.accept()
            return
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
            self.headline.setText('Series import cancelled.')
            self._append(detail)
            self.bar.setMaximum(1)
            self.bar.setValue(0)
            return
        self.headline.setText('Series import failed.')
        self._append(detail)
        error_dialog(
            self.gui,
            'Wranglekit',
            'Could not import the rest of the series.',
            det_msg=detail,
            show=True,
        )

    def _on_worker_finished(self, payload: dict[str, Any]) -> None:
        cleanup_dir = payload.get('cleanup_dir')
        try:
            records = payload.get('records') or []
            if not records:
                summary = 'No other works found in those series.'
                self.headline.setText('Nothing to import.')
                self._append(summary)
                self.bar.setMaximum(1)
                self.bar.setValue(1)
                info_dialog(self.gui, 'Wranglekit', summary, show=True)
                self._closing = True
                _keep_open_with_close(self)
                return

            self.headline.setText('Writing books into Calibre library…')
            self._append('Writing books into Calibre library…')
            self.bar.setMaximum(0)
            self.buttons.setEnabled(False)

            summary, remap_text, book_ids = write_import_payload(
                self.gui,
                payload,
                update_existing=bool(payload.get('update_existing', True)),
                skip_existing_epub=True,
            )
            skipped = payload.get('skipped') or []
            if skipped:
                summary = (
                    summary.rstrip('.')
                    + f'; skipped {len(skipped)} selected book(s) without an AO3 id.'
                )
            self.headline.setText('Import complete.')
            self._append(summary)
            self._append(remap_text)
            self.bar.setMaximum(1)
            self.bar.setValue(1)
            refresh_library_ui(self.gui, book_ids)
            info_dialog(self.gui, 'Wranglekit', summary, det_msg=remap_text, show=True)
            self._closing = True
            _keep_open_with_close(self)
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
                'Wranglekit',
                'Import failed while writing to Calibre.',
                det_msg=detail,
                show=True,
            )
        finally:
            if cleanup_dir:
                shutil.rmtree(cleanup_dir, ignore_errors=True)


class FillSeriesWorker(QThread):
    """Look up AO3 series for selected books and write Series on those books only."""

    status = pyqtSignal(str)
    progress = pyqtSignal(int, int)
    finished_ok = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, book_ids: list[int], *, parent=None):
        super().__init__(parent)
        self.book_ids = list(book_ids)
        self._cancel = False
        self._handle = None
        self._db = None

    def set_db(self, db) -> None:
        self._db = db

    def request_cancel(self) -> None:
        self._cancel = True
        handle = self._handle
        if handle is not None:
            handle.cancel()

    def _on_status(self, message: str) -> None:
        visible = _user_status_line(message)
        if visible is None:
            return
        self.status.emit(visible)
        parsed = _progress_from_status(visible)
        if parsed is not None:
            self.progress.emit(*parsed)

    def run(self) -> None:
        from calibre_plugins.wranglekit.enrich import EnrichHandle, run_ao3kit
        from calibre_plugins.wranglekit.prefs import plugin_runtime_settings
        from calibre_plugins.wranglekit.scrape_run import (
            merge_plugin_settings,
            prepare_fill_series_command,
        )

        tmp = tempfile.mkdtemp(prefix='ao3-fill-series-')
        try:
            if self._db is None:
                raise ValueError('Library database was not provided.')
            self.progress.emit(0, 0)
            self.status.emit(
                f'Loading AO3 metadata for {len(self.book_ids)} selected book(s)…'
            )
            ready, skipped = load_selected_records(self._db, self.book_ids)
            for item in skipped:
                self.status.emit(
                    f"Skipping {item.get('title') or item.get('book_id')}: "
                    f"{item.get('reason')}"
                )
            if self._cancel:
                raise EnrichCancelled('Cancelled while loading selection.')
            if not ready:
                raise ValueError(
                    'None of the selected books have an AO3 URL or work id.'
                )

            records = [item['record'] for item in ready]
            options = merge_plugin_settings({}, plugin_runtime_settings())
            self.status.emit(
                f'Looking up series for {len(records)} selected book(s)…'
            )
            argv, jsonl = prepare_fill_series_command(records, tmp, options)
            self._handle = EnrichHandle()
            code, stdout, stderr = run_ao3kit(
                argv,
                on_status=self._on_status,
                handle=self._handle,
                should_cancel=lambda: self._cancel,
                cancel_message='Cancelled while looking up series.',
            )
            self._handle = None
            if self._cancel:
                raise EnrichCancelled('Cancelled while looking up series.')
            if code != 0:
                detail = (stderr or stdout or '').strip() or '(no output)'
                raise ValueError(f'Series lookup failed (exit {code}):\n{detail}')
            if not jsonl.is_file():
                raise ValueError('Series lookup produced no results file.')

            filled = load_jsonl_records(jsonl)
            by_id: dict[str, dict[str, Any]] = {}
            for record in filled:
                work_id = canonical_work_id(record)
                if work_id:
                    by_id[work_id] = record
            for item in ready:
                work_id = canonical_work_id(item['record'])
                if work_id and work_id in by_id:
                    item['record'] = by_id[work_id]

            in_series = sum(
                1
                for item in ready
                if (item['record'].get('series') or [])
            )
            self.status.emit(
                f'Series lookup finished ({in_series}/{len(ready)} in a series).'
            )
            self.finished_ok.emit(
                {
                    'items': ready,
                    'skipped': skipped,
                    'in_series': in_series,
                }
            )
        except EnrichCancelled as exc:
            self.failed.emit(str(exc))
        except Exception:
            self.failed.emit(traceback.format_exc())
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class FillSeriesDialog(QDialog):
    """Progress window for filling Series on the current selection."""

    def __init__(self, gui, book_ids: list[int]):
        super().__init__(gui)
        self.gui = gui
        self.setWindowTitle('Wranglekit — Fill series')
        self.setMinimumSize(640, 420)
        self.setWindowModality(Qt.NonModal)
        self.setAttribute(Qt.WA_DeleteOnClose, True)

        layout = QVBoxLayout(self)

        self.headline = QLabel(
            f'Looking up AO3 series for {len(book_ids)} selected book(s)…'
        )
        self.headline.setWordWrap(True)
        layout.addWidget(self.headline)

        self.bar = QProgressBar()
        self.bar.setMinimum(0)
        self.bar.setMaximum(0)
        self.bar.setTextVisible(True)
        layout.addWidget(self.bar)

        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        try:
            self.log.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        except AttributeError:
            self.log.setLineWrapMode(QPlainTextEdit.NoWrap)
        layout.addWidget(self.log)

        self.buttons = QDialogButtonBox(QDialogButtonBox.Cancel)
        self.buttons.rejected.connect(self._on_cancel)
        layout.addWidget(self.buttons)

        self._worker = FillSeriesWorker(book_ids, parent=self)
        self._worker.set_db(gui.current_db)
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
        _apply_progress_bar(self.bar, current, total)

    def _on_cancel(self) -> None:
        if self._closing:
            self.accept()
            return
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
            self.headline.setText('Series fill cancelled.')
            self._append(detail)
            self.bar.setMaximum(1)
            self.bar.setValue(0)
            return
        self.headline.setText('Series fill failed.')
        self._append(detail)
        error_dialog(
            self.gui,
            'Wranglekit',
            'Could not fill Series for the selection.',
            det_msg=detail,
            show=True,
        )

    def _on_worker_finished(self, payload: dict[str, Any]) -> None:
        try:
            self.headline.setText('Writing Series into Calibre…')
            self._append('Writing Series into Calibre…')
            self.bar.setMaximum(0)
            self.buttons.setEnabled(False)

            items = payload.get('items') or []
            outcomes = apply_series_records(self.gui.current_db, items)
            filled = [item for item in outcomes if item.get('in_series')]
            skipped = payload.get('skipped') or []
            not_in = len(outcomes) - len(filled)
            summary = f'Filled Series on {len(filled)} book(s)'
            extras = []
            if not_in:
                extras.append(f'{not_in} not in an AO3 series')
            if skipped:
                extras.append(
                    f'skipped {len(skipped)} without an AO3 URL / work id'
                )
            if extras:
                summary += ' (' + '; '.join(extras) + ')'
            summary += '.'
            detail_lines = []
            for item in filled:
                name = item.get('series') or ''
                index = item.get('series_index')
                part = f' part {int(index)}' if index is not None else ''
                detail_lines.append(
                    f"{item.get('title') or item.get('book_id')}: {name}{part}"
                )
            remap_text = '\n'.join(detail_lines)
            self.headline.setText('Done.')
            self._append(summary)
            if remap_text:
                self._append(remap_text)
            self.bar.setMaximum(1)
            self.bar.setValue(1)
            refresh_library_ui(
                self.gui,
                [item['book_id'] for item in outcomes if item.get('book_id') is not None],
            )
            info_dialog(self.gui, 'Wranglekit', summary, det_msg=remap_text, show=True)
            self._closing = True
            _keep_open_with_close(self)
        except Exception:
            detail = traceback.format_exc()
            self.headline.setText('Failed while writing Series.')
            self._append(detail)
            self.buttons.setEnabled(True)
            self.buttons.clear()
            self.buttons.addButton(QDialogButtonBox.Close)
            self.buttons.rejected.connect(self.reject)
            error_dialog(
                self.gui,
                'Wranglekit',
                'Failed while writing Series.',
                det_msg=detail,
                show=True,
            )

