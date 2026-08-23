# -*- coding: utf-8 -*-
"""Attached log window and running-jobs list for background ao3kit jobs."""

from __future__ import annotations

from pathlib import Path

from PyQt5.Qt import (
    QAbstractItemView,
    QDialog,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTimer,
    QVBoxLayout,
    Qt,
)

from calibre.gui2 import error_dialog, info_dialog, question_dialog

from calibre_plugins.wranglekit.jobs import (
    first_line,
    format_job_header,
    job_clear_bucket,
    job_is_deletable,
    job_is_retryable,
    progress_from_message,
    read_json,
    read_log_tail,
)
from calibre_plugins.wranglekit.progress import _apply_progress_bar, _user_status_line

_RETRY_TIP = (
    'Run this job again from the start. Already-downloaded EPUBs and '
    'cached tags are skipped.'
)


class JobLogDialog(QDialog):
    """Live tail of a detached job. Close / Background detaches; Cancel stops."""

    def __init__(
        self,
        gui,
        *,
        job_id: str,
        title: str,
        log_path: Path,
        status_path: Path,
        supervisor,
    ):
        super().__init__(gui)
        self.gui = gui
        self.job_id = job_id
        self._log_path = Path(log_path)
        self._status_path = Path(status_path)
        self._supervisor = supervisor
        self._closing = False
        self._finished = False
        self.setWindowTitle(title or 'Wranglekit — Job')
        self.setMinimumSize(640, 420)
        self.resize(760, 520)
        self.setWindowModality(Qt.NonModal)
        self.setAttribute(Qt.WA_DeleteOnClose, True)

        layout = QVBoxLayout(self)
        self.headline = QLabel('Starting…')
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

        # Explicit QPushButtons: QDialogButtonBox ActionRole is often invisible
        # on macOS / Calibre's Qt style.
        row = QHBoxLayout()
        self.background_btn = QPushButton('Move to background')
        self.background_btn.setToolTip(
            'Hide this window. The job keeps running; reopen the log from '
            'Running jobs…'
        )
        self.background_btn.clicked.connect(self._on_background)
        self.cancel_btn = QPushButton('Cancel')
        self.cancel_btn.clicked.connect(self._on_cancel)
        self.retry_btn = QPushButton('Retry')
        self.retry_btn.setToolTip(_RETRY_TIP)
        self.retry_btn.clicked.connect(self._on_retry)
        self.retry_btn.setVisible(False)
        self.remove_btn = QPushButton('Remove')
        self.remove_btn.setToolTip(
            'Delete this job from the list. Books already in Calibre stay.'
        )
        self.remove_btn.clicked.connect(self._on_remove)
        self.remove_btn.setVisible(False)
        self.close_btn = QPushButton('Close')
        self.close_btn.clicked.connect(self.accept)
        self.close_btn.setVisible(False)
        row.addWidget(self.background_btn)
        row.addStretch(1)
        row.addWidget(self.retry_btn)
        row.addWidget(self.remove_btn)
        row.addWidget(self.cancel_btn)
        row.addWidget(self.close_btn)
        layout.addLayout(row)

        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self.reload)
        self._timer.start()
        self.reload()

    def reload(self) -> None:
        status = read_json(self._status_path) or {}
        self.headline.setText(format_job_header(status, self._log_path))
        parsed = progress_from_message(str(status.get('message') or ''))
        if parsed is not None:
            _apply_progress_bar(self.bar, parsed[0], parsed[1])
        elif status.get('running'):
            self.bar.setMaximum(0)
            self.bar.setFormat('Working…')
        text = read_log_tail(self._log_path)
        if not text:
            text = f'Waiting for log…\n{self._log_path}'
        bar = self.log.verticalScrollBar()
        follow = bar.value() >= bar.maximum() - 8
        self.log.setPlainText(text)
        if follow:
            bar.setValue(bar.maximum())

        running = bool(status.get('running'))
        ingest = str(status.get('ingest') or 'none')
        if self._finished:
            return
        if running:
            return
        if ingest == 'pending':
            self.bar.setMaximum(0)
            self.bar.setFormat('Writing into Calibre…')
            return
        if self.job_id == 'warm' and status:
            message = str(status.get('message') or 'Background tag cache stopped.')
            self.mark_finished(message, ok=True)

    def mark_finished(self, summary: str, *, ok: bool = True, detail: str = '') -> None:
        self._finished = True
        self._timer.stop()
        self.reload()
        first = first_line(summary, 200) if summary else 'Done.'
        self.headline.setText(first[:200])
        if summary:
            self._append(summary)
        if detail:
            self._append(detail)
        self.bar.setMaximum(1)
        self.bar.setValue(1 if ok else 0)
        self.background_btn.setVisible(False)
        self.cancel_btn.setVisible(False)
        self.close_btn.setVisible(True)
        self.close_btn.setEnabled(True)
        status = read_json(self._status_path) or {}
        status.setdefault('id', self.job_id)
        self.retry_btn.setVisible(job_is_retryable(status))
        self.retry_btn.setEnabled(True)
        self.remove_btn.setVisible(job_is_deletable(status))
        self.remove_btn.setEnabled(True)

    def mark_retrying(self) -> None:
        self._finished = False
        self._closing = False
        self.retry_btn.setVisible(False)
        self.remove_btn.setVisible(False)
        self.close_btn.setVisible(False)
        self.background_btn.setVisible(True)
        self.background_btn.setEnabled(True)
        self.cancel_btn.setVisible(True)
        self.cancel_btn.setEnabled(True)
        self.bar.setMaximum(0)
        self.bar.setFormat('Working…')
        self.headline.setText('Retrying…')
        self._append('Retrying from the start (existing EPUBs and cached tags are skipped)…')
        self._timer.start()
        self.reload()

    def mark_working(self, message: str) -> None:
        visible = _user_status_line(message) or message
        self.headline.setText(visible[:200])
        self._append(visible)
        self.bar.setMaximum(0)
        self.cancel_btn.setEnabled(False)

    def _append(self, message: str) -> None:
        text = (message or '').rstrip()
        if not text:
            return
        self.log.appendPlainText(text)
        self.log.verticalScrollBar().setValue(self.log.verticalScrollBar().maximum())

    def _on_background(self) -> None:
        self._closing = True
        self._timer.stop()
        self._supervisor.detach(self.job_id)
        self.accept()

    def _on_cancel(self) -> None:
        if self._finished or self._closing:
            self.accept()
            return
        self.headline.setText('Stopping…')
        self._append('Stop requested…')
        self.cancel_btn.setEnabled(False)
        self.background_btn.setEnabled(False)
        self._supervisor.cancel(self.job_id)

    def _on_retry(self) -> None:
        if self.job_id == 'warm':
            return
        self.retry_btn.setEnabled(False)
        if self._supervisor.retry(self.job_id, attach=True) is None:
            self.retry_btn.setEnabled(True)

    def _on_remove(self) -> None:
        if not job_is_deletable(read_json(self._status_path) or {'id': self.job_id}):
            return
        if not question_dialog(
            self.gui,
            'Wranglekit',
            'Remove this job from the list? Books already in Calibre stay.',
        ):
            return
        if self._supervisor.delete_jobs([self.job_id]):
            self._closing = True
            self.accept()

    def closeEvent(self, event) -> None:
        if self._finished or self._closing:
            super().closeEvent(event)
            return
        self._on_background()
        event.accept()


class JobsListDialog(QDialog):
    """List running and recent ao3kit jobs; attach or stop."""

    def __init__(self, gui, supervisor):
        super().__init__(gui)
        self.gui = gui
        self._supervisor = supervisor
        self.setWindowTitle('Wranglekit — Running jobs')
        self.setMinimumSize(720, 360)
        self.setWindowModality(Qt.NonModal)

        layout = QVBoxLayout(self)
        layout.addWidget(
            QLabel(
                'Jobs keep running if you close their log window. '
                'Background on a log window detaches; Cancel / Stop ends the process. '
                'Retry re-runs a failed or stopped job from the start; existing '
                'EPUBs and cached tags are skipped. Delete / Clear remove jobs '
                'from this list only — the Calibre library is unchanged. '
                'Shift-click or ⌘-click to select several jobs, then Delete.'
            )
        )
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(['Job', 'State', 'Result', 'Message', 'Id'])
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.doubleClicked.connect(self._attach_selected)
        self.table.itemSelectionChanged.connect(self._update_buttons)
        layout.addWidget(self.table)
        self._jobs = []

        row = QHBoxLayout()
        self.attach_btn = QPushButton('Show log')
        self.attach_btn.clicked.connect(self._attach_selected)
        self.stop_btn = QPushButton('Stop')
        self.stop_btn.clicked.connect(self._stop_selected)
        self.retry_btn = QPushButton('Retry')
        self.retry_btn.setToolTip(_RETRY_TIP)
        self.retry_btn.clicked.connect(self._retry_selected)
        self.delete_btn = QPushButton('Delete')
        self.delete_btn.setToolTip(
            'Remove the selected jobs from this list. Books already in Calibre stay. '
            'Shift-click or ⌘-click to select more than one.'
        )
        self.delete_btn.clicked.connect(self._delete_selected)
        self.clear_finished_btn = QPushButton('Clear finished')
        self.clear_finished_btn.setToolTip(
            'Remove all successful completed jobs from this list.'
        )
        self.clear_finished_btn.clicked.connect(self._clear_finished)
        self.clear_failed_btn = QPushButton('Clear failed')
        self.clear_failed_btn.setToolTip(
            'Remove all failed and stopped jobs from this list.'
        )
        self.clear_failed_btn.clicked.connect(self._clear_failed)
        row.addWidget(self.attach_btn)
        row.addWidget(self.stop_btn)
        row.addWidget(self.retry_btn)
        row.addWidget(self.delete_btn)
        row.addStretch(1)
        row.addWidget(self.clear_finished_btn)
        row.addWidget(self.clear_failed_btn)
        close_btn = QPushButton('Close')
        close_btn.clicked.connect(self.accept)
        row.addWidget(close_btn)
        layout.addLayout(row)

        self._timer = QTimer(self)
        self._timer.setInterval(2000)
        self._timer.timeout.connect(self.reload)
        self._timer.start()
        self.reload()

    def reload(self) -> None:
        selected = set(self._selected_ids())
        jobs = self._supervisor.list_jobs()
        self._jobs = jobs
        self.table.blockSignals(True)
        try:
            self.table.setRowCount(len(jobs))
            for row, job in enumerate(jobs):
                title = str(job.get('title') or job.get('id') or '')
                if job.get('running'):
                    state = 'Running'
                elif str(job.get('ingest') or '') == 'pending':
                    state = 'Writing to library'
                elif str(job.get('ingest') or '') == 'cancelled':
                    state = 'Stopped'
                elif str(job.get('ingest') or '') == 'failed' or job.get(
                    'exit_code'
                ) not in (None, 0):
                    state = 'Failed'
                else:
                    state = 'Finished'
                message = first_line(job.get('message'), 120)
                result = first_line(job.get('result'), 120)
                job_id = str(job.get('id') or '')
                for col, text in enumerate((title, state, result, message, job_id)):
                    self.table.setItem(row, col, QTableWidgetItem(text))
            self.table.clearSelection()
            for row, job in enumerate(jobs):
                job_id = str(job.get('id') or '')
                on = bool(job_id) and job_id in selected
                for col in range(self.table.columnCount()):
                    cell = self.table.item(row, col)
                    if cell is not None:
                        cell.setSelected(on)
            self.table.resizeColumnsToContents()
        finally:
            self.table.blockSignals(False)
        self._update_buttons()

    def _selected_ids(self) -> list[str]:
        rows = sorted({index.row() for index in self.table.selectedIndexes()})
        ids: list[str] = []
        seen: set[str] = set()
        for row in rows:
            item = self.table.item(row, 4)
            if item is None:
                continue
            job_id = item.text()
            if job_id and job_id not in seen:
                seen.add(job_id)
                ids.append(job_id)
        return ids

    def _selected_jobs(self) -> list[dict]:
        by_id = {str(job.get('id') or ''): job for job in getattr(self, '_jobs', [])}
        jobs = []
        for job_id in self._selected_ids():
            jobs.append(by_id.get(job_id) or {'id': job_id})
        return jobs

    def _selected_job(self) -> dict:
        job_id = self._selected_id()
        if not job_id:
            return {}
        for job in getattr(self, '_jobs', []):
            if str(job.get('id') or '') == job_id:
                return job
        return {'id': job_id}

    def _selected_id(self) -> str:
        ids = self._selected_ids()
        row = self.table.currentRow()
        item = self.table.item(row, 4) if row >= 0 else None
        current = item.text() if item is not None else ''
        if current and current in ids:
            return current
        if ids:
            return ids[0]
        return current

    def _update_buttons(self) -> None:
        jobs = self._selected_jobs()
        current = self._selected_job()
        self.attach_btn.setEnabled(bool(self._selected_id()))
        self.stop_btn.setEnabled(any(job.get('running') for job in jobs))
        self.retry_btn.setEnabled(job_is_retryable(current))
        self.delete_btn.setEnabled(any(job_is_deletable(job) for job in jobs))

    def _attach_selected(self) -> None:
        job_id = self._selected_id()
        if not job_id:
            error_dialog(self.gui, 'Wranglekit', 'Select a job first.', show=True)
            return
        self._supervisor.attach(job_id)

    def _stop_selected(self) -> None:
        running = [
            job
            for job in self._selected_jobs()
            if job.get('running') and job.get('id')
        ]
        if not running:
            error_dialog(self.gui, 'Wranglekit', 'Select a running job first.', show=True)
            return
        n = len(running)
        noun = 'job' if n == 1 else 'jobs'
        if not question_dialog(self.gui, 'Wranglekit', f'Stop {n} {noun}?'):
            return
        for job in running:
            self._supervisor.cancel(str(job.get('id')))
        self.reload()

    def _retry_selected(self) -> None:
        job_id = self._selected_id()
        if not job_id:
            error_dialog(self.gui, 'Wranglekit', 'Select a job first.', show=True)
            return
        if not job_is_retryable(self._selected_job()):
            error_dialog(
                self.gui,
                'Wranglekit',
                'That job is not waiting to be retried. Retry is for failed or '
                'stopped jobs after they finish writing into Calibre.',
                show=True,
            )
            return
        self._supervisor.retry(job_id)
        self.reload()

    def _delete_selected(self) -> None:
        selected = self._selected_jobs()
        deletable = [
            job for job in selected if job_is_deletable(job) and job.get('id')
        ]
        if not deletable:
            error_dialog(
                self.gui,
                'Wranglekit',
                'Select one or more finished jobs to delete. '
                'Running jobs and jobs still writing into Calibre cannot be deleted.',
                show=True,
            )
            return
        n = len(deletable)
        skipped = len(selected) - n
        noun = 'job' if n == 1 else 'jobs'
        extra = ''
        if skipped:
            extra = f' {skipped} still running or writing will be left.'
        if not question_dialog(
            self.gui,
            'Wranglekit',
            f'Remove {n} {noun} from the list? Books already in Calibre stay.{extra}',
        ):
            return
        self._supervisor.delete_jobs([str(job.get('id')) for job in deletable])
        self.reload()

    def _clear_finished(self) -> None:
        self._clear_buckets(
            ('finished',),
            'Remove all finished jobs from the list? Books already in Calibre stay.',
            finished=True,
        )

    def _clear_failed(self) -> None:
        self._clear_buckets(
            ('failed', 'stopped'),
            'Remove all failed and stopped jobs from the list? '
            'Books already in Calibre stay.',
            failed=True,
            stopped=True,
        )

    def _clear_buckets(self, buckets: tuple[str, ...], prompt: str, **flags: bool) -> None:
        ids = [
            str(job.get('id') or '')
            for job in getattr(self, '_jobs', [])
            if job_clear_bucket(job) in buckets and job.get('id')
        ]
        if not ids:
            info_dialog(self.gui, 'Wranglekit', 'Nothing to clear.', show=True)
            return
        noun = 'job' if len(ids) == 1 else 'jobs'
        if not question_dialog(
            self.gui,
            'Wranglekit',
            f'{prompt}\n\n{len(ids)} {noun}.',
        ):
            return
        self._supervisor.clear_jobs(**flags)
        self.reload()


class JobNotifyDialog(QDialog):
    """Completion popup when the log window is not attached. Offers Retry."""

    def __init__(
        self,
        gui,
        *,
        summary: str,
        detail: str = '',
        ok: bool = True,
        retryable: bool = False,
    ):
        super().__init__(gui)
        self.should_retry = False
        self.setWindowTitle('Wranglekit')
        self.setMinimumWidth(420)
        layout = QVBoxLayout(self)
        label = QLabel(summary or ('Done.' if ok else 'Job failed.'))
        label.setWordWrap(True)
        layout.addWidget(label)
        if detail and detail.strip() != (summary or '').strip():
            extra = QPlainTextEdit()
            extra.setReadOnly(True)
            extra.setPlainText(detail)
            extra.setMaximumHeight(160)
            layout.addWidget(extra)
        row = QHBoxLayout()
        row.addStretch(1)
        if retryable:
            retry_btn = QPushButton('Retry')
            retry_btn.setToolTip(_RETRY_TIP)
            retry_btn.clicked.connect(self._on_retry)
            row.addWidget(retry_btn)
        close_btn = QPushButton('Close')
        close_btn.clicked.connect(self.accept)
        close_btn.setDefault(True)
        row.addWidget(close_btn)
        layout.addLayout(row)

    def _on_retry(self) -> None:
        self.should_retry = True
        self.accept()
