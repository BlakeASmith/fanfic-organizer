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

from calibre.gui2 import error_dialog, question_dialog

from calibre_plugins.ao3_scraper.jobs import (
    format_job_header,
    progress_from_message,
    read_json,
    read_log_tail,
)
from calibre_plugins.ao3_scraper.progress import _apply_progress_bar, _user_status_line


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
        self.setWindowTitle(title or 'AO3 Scraper — Job')
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
        self.close_btn = QPushButton('Close')
        self.close_btn.clicked.connect(self.accept)
        self.close_btn.setVisible(False)
        row.addWidget(self.background_btn)
        row.addStretch(1)
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
        first = (summary or '').strip().splitlines()[0] if summary else 'Done.'
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
        self.setWindowTitle('AO3 Scraper — Running jobs')
        self.setMinimumSize(720, 360)
        self.setWindowModality(Qt.NonModal)

        layout = QVBoxLayout(self)
        layout.addWidget(
            QLabel(
                'Jobs keep running if you close their log window. '
                'Background on a log window detaches; Cancel / Stop ends the process.'
            )
        )
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(['Job', 'State', 'Message', 'Id'])
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.doubleClicked.connect(self._attach_selected)
        layout.addWidget(self.table)

        row = QHBoxLayout()
        self.attach_btn = QPushButton('Show log')
        self.attach_btn.clicked.connect(self._attach_selected)
        self.stop_btn = QPushButton('Stop')
        self.stop_btn.clicked.connect(self._stop_selected)
        row.addWidget(self.attach_btn)
        row.addWidget(self.stop_btn)
        row.addStretch(1)
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
        jobs = self._supervisor.list_jobs()
        self.table.setRowCount(len(jobs))
        for row, job in enumerate(jobs):
            title = str(job.get('title') or job.get('id') or '')
            if job.get('running'):
                state = 'Running'
            elif str(job.get('ingest') or '') == 'pending':
                state = 'Writing to library'
            elif str(job.get('ingest') or '') == 'cancelled':
                state = 'Stopped'
            elif job.get('exit_code') not in (None, 0):
                state = 'Failed'
            else:
                state = 'Finished'
            message = str(job.get('message') or '').splitlines()[0][:120]
            job_id = str(job.get('id') or '')
            for col, text in enumerate((title, state, message, job_id)):
                self.table.setItem(row, col, QTableWidgetItem(text))
        self.table.resizeColumnsToContents()

    def _selected_id(self) -> str:
        row = self.table.currentRow()
        if row < 0:
            return ''
        item = self.table.item(row, 3)
        return item.text() if item is not None else ''

    def _attach_selected(self) -> None:
        job_id = self._selected_id()
        if not job_id:
            error_dialog(self.gui, 'AO3 Scraper', 'Select a job first.', show=True)
            return
        self._supervisor.attach(job_id)

    def _stop_selected(self) -> None:
        job_id = self._selected_id()
        if not job_id:
            error_dialog(self.gui, 'AO3 Scraper', 'Select a job first.', show=True)
            return
        if not question_dialog(
            self.gui,
            'AO3 Scraper',
            f'Stop job {job_id}?',
        ):
            return
        self._supervisor.cancel(job_id)
        self.reload()
