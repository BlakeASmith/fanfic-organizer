# -*- coding: utf-8 -*-
"""Check-for-updates dialog for the Fanfic Organizer Calibre plugin."""

from __future__ import annotations

from PyQt5.Qt import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QThread,
    QVBoxLayout,
    Qt,
)

from calibre.gui2 import error_dialog, info_dialog, question_dialog

from calibre_plugins.fanfic_organizer.prefs import prefs
from calibre_plugins.fanfic_organizer.updates import (
    ReleaseInfo,
    UpdateError,
    compare_to_installed,
    download_and_install,
    fetch_releases,
    filter_releases,
    format_published_at,
    installed_version_text,
    latest_release,
    latest_stable_release,
    spawn_calibre_restart,
    summarize_release_notes,
)


class _FetchReleasesWorker(QThread):
    finished_with_result = None
    failed = None

    def run(self):
        try:
            releases = fetch_releases()
        except UpdateError as exc:
            self.failed = str(exc)
            return
        self.finished_with_result = releases


class _InstallReleaseWorker(QThread):
    finished_ok = False
    failed = None

    def __init__(self, release: ReleaseInfo, parent=None):
        super().__init__(parent)
        self.release = release

    def run(self):
        try:
            download_and_install(self.release)
        except UpdateError as exc:
            self.failed = str(exc)
            return
        self.finished_ok = True


class UpdateCheckDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Check for updates")
        self.setMinimumWidth(560)
        self._all_releases: list[ReleaseInfo] = []
        self._releases: list[ReleaseInfo] = []
        self._fetch_worker: _FetchReleasesWorker | None = None
        self._install_worker: _InstallReleaseWorker | None = None

        layout = QVBoxLayout()
        self.setLayout(layout)

        intro = QLabel(
            "Compare the installed plugin with GitHub Releases. Choose a "
            "release to install a newer build or roll back to an older tag. "
            "Calibre must restart to load the new zip."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        form = QFormLayout()
        self.current_label = QLabel(installed_version_text())
        form.addRow("Installed", self.current_label)
        self.latest_label = QLabel("Checking GitHub…")
        form.addRow("Latest on GitHub", self.latest_label)
        layout.addLayout(form)

        self.include_prereleases = QCheckBox(
            "Include preview pre-releases (main-branch builds)"
        )
        self.include_prereleases.setChecked(
            bool(prefs.get("include_prereleases", False))
        )
        self.include_prereleases.setToolTip(
            "When checked, list automated GitHub pre-releases "
            "(X.Y.Z-preview.<run>+<sha>) alongside standard releases. "
            "Prefer standard releases for daily use."
        )
        self.include_prereleases.stateChanged.connect(self._on_prerelease_toggled)
        layout.addWidget(self.include_prereleases)

        self.status = QLabel("Contacting GitHub…")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

        version_row = QHBoxLayout()
        self.version_combo = QComboBox()
        self.version_combo.setEnabled(False)
        self.version_combo.currentIndexChanged.connect(self._refresh_selection)
        version_row.addWidget(self.version_combo, stretch=1)
        self.refresh_btn = QPushButton("Refresh")
        self.refresh_btn.clicked.connect(self.refresh_releases)
        version_row.addWidget(self.refresh_btn)
        layout.addLayout(version_row)

        self.notes = QPlainTextEdit()
        self.notes.setReadOnly(True)
        self.notes.setPlaceholderText("Release notes will appear here.")
        self.notes.setMinimumHeight(180)
        layout.addWidget(self.notes)

        self.buttons = QDialogButtonBox()
        self.install_btn = self.buttons.addButton(
            "Install and restart Calibre…", QDialogButtonBox.AcceptRole
        )
        self.install_btn.setEnabled(False)
        self.close_btn = self.buttons.addButton(
            QDialogButtonBox.Close
        )
        self.close_btn.clicked.connect(self.reject)
        self.install_btn.clicked.connect(self.install_selected)
        layout.addWidget(self.buttons)

        self.refresh_releases()

    def refresh_releases(self):
        if self._fetch_worker is not None and self._fetch_worker.isRunning():
            return
        self.version_combo.setEnabled(False)
        self.install_btn.setEnabled(False)
        self.refresh_btn.setEnabled(False)
        self.status.setText("Contacting GitHub…")
        self.latest_label.setText("Checking…")
        worker = _FetchReleasesWorker(self)
        self._fetch_worker = worker
        worker.finished.connect(self._on_releases_loaded)
        worker.start()

    def _on_releases_loaded(self):
        worker = self._fetch_worker
        self.refresh_btn.setEnabled(True)
        if worker is None:
            return
        if worker.failed:
            self.status.setText(worker.failed)
            self.latest_label.setText("Unavailable")
            error_dialog(
                self,
                "Fanfic Organizer",
                "Could not check GitHub for updates.",
                det_msg=worker.failed,
                show=True,
            )
            return
        self._all_releases = list(worker.finished_with_result or [])
        self._apply_release_list()

    def _on_prerelease_toggled(self, _state=None):
        prefs["include_prereleases"] = self.include_prereleases.isChecked()
        if self._all_releases:
            self._apply_release_list()

    def _apply_release_list(self):
        include = self.include_prereleases.isChecked()
        self._releases = filter_releases(
            self._all_releases, include_prereleases=include
        )
        self.version_combo.blockSignals(True)
        self.version_combo.clear()
        for release in self._releases:
            label = release.version_text
            if release.is_preview:
                label += " (preview)"
            if compare_to_installed(release) > 0:
                label += " (newer)"
            elif compare_to_installed(release) < 0:
                label += " (older)"
            else:
                label += " (installed)"
            published = format_published_at(release.published_at)
            if published:
                label += f" — {published}"
            self.version_combo.addItem(label, release)
        self.version_combo.blockSignals(False)
        self.version_combo.setEnabled(bool(self._releases))
        latest = latest_release(self._releases)
        latest_stable = latest_stable_release(self._all_releases)
        if latest is None:
            self.latest_label.setText("No releases found")
            if self._all_releases and not include:
                self.status.setText(
                    "No standard releases found. Enable preview pre-releases "
                    "to see automated main-branch builds."
                )
            else:
                self.status.setText(
                    "No published GitHub releases with a plugin zip were found."
                )
            self._refresh_selection()
            return
        if include and latest_stable is not None:
            latest_text = latest_stable.version_text
            if latest.is_preview and latest.version_text != latest_text:
                latest_text += f" (preview: {latest.version_text})"
            self.latest_label.setText(latest_text)
        elif include and latest.is_preview:
            self.latest_label.setText(f"{latest.version_text} (preview only)")
        else:
            self.latest_label.setText(latest.version_text)
        current = installed_version_text()
        if compare_to_installed(latest) > 0:
            kind = "preview build" if latest.is_preview else "release"
            self.status.setText(
                f"{kind.capitalize()} {latest.version_text} is available "
                f"(you have {current})."
            )
        elif compare_to_installed(latest) == 0:
            self.status.setText("You are on the latest listed build.")
        else:
            self.status.setText(
                "Your installed build is newer than the latest GitHub release."
            )
        if self._releases:
            self.version_combo.setCurrentIndex(0)
        self._refresh_selection()

    def _selected_release(self) -> ReleaseInfo | None:
        data = self.version_combo.currentData()
        return data if isinstance(data, ReleaseInfo) else None

    def _refresh_selection(self):
        release = self._selected_release()
        if release is None:
            self.notes.clear()
            self.install_btn.setEnabled(False)
            return
        self.notes.setPlainText(summarize_release_notes(release.body))
        same = compare_to_installed(release) == 0
        self.install_btn.setEnabled(not same)
        if same:
            self.install_btn.setText("Already installed")
        else:
            direction = "Upgrade" if compare_to_installed(release) > 0 else "Downgrade"
            self.install_btn.setText(f"{direction} to {release.version_text} and restart Calibre…")

    def install_selected(self):
        release = self._selected_release()
        if release is None:
            return
        current = installed_version_text()
        direction = "upgrade" if compare_to_installed(release) > 0 else "downgrade"
        if not question_dialog(
            self,
            "Fanfic Organizer",
            (
                f"{direction.capitalize()} from {current} to {release.version_text}?\n\n"
                "The plugin zip will be downloaded from GitHub, installed with "
                "calibre-customize, and Calibre will restart."
            ),
        ):
            return
        QApplication.setOverrideCursor(Qt.WaitCursor)
        self.install_btn.setEnabled(False)
        self.refresh_btn.setEnabled(False)
        self.version_combo.setEnabled(False)
        worker = _InstallReleaseWorker(release, self)
        self._install_worker = worker
        worker.finished.connect(self._on_install_finished)
        worker.start()

    def _on_install_finished(self):
        QApplication.restoreOverrideCursor()
        self.refresh_btn.setEnabled(True)
        self.version_combo.setEnabled(bool(self._releases))
        worker = self._install_worker
        if worker is None:
            return
        if worker.failed:
            self.install_btn.setEnabled(True)
            error_dialog(
                self,
                "Fanfic Organizer",
                "Could not install the selected release.",
                det_msg=worker.failed,
                show=True,
            )
            self._refresh_selection()
            return
        try:
            spawn_calibre_restart()
        except UpdateError as exc:
            info_dialog(
                self,
                "Fanfic Organizer",
                (
                    f"Installed {self._selected_release().version_text}, but Calibre "
                    "could not be restarted automatically."
                ),
                det_msg=str(exc),
                show=True,
            )
            self.accept()
            return
        info_dialog(
            self,
            "Fanfic Organizer",
            (
                f"Installed {self._selected_release().version_text}. "
                "Calibre will quit and reopen shortly."
            ),
            show=True,
        )
        self.accept()


def show_update_check(parent=None):
    dialog = UpdateCheckDialog(parent)
    dialog.exec_()
