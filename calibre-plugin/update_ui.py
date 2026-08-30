# -*- coding: utf-8 -*-
"""Check-for-updates dialog for the Fanfic Organizer Calibre plugin."""

from __future__ import annotations

from PyQt5.Qt import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDesktopServices,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QThread,
    QUrl,
    QVBoxLayout,
    Qt,
)

from calibre.gui2 import error_dialog, info_dialog, question_dialog

from calibre_plugins.fanfic_organizer.prefs import prefs
from calibre_plugins.fanfic_organizer.updates import (
    ReleaseInfo,
    UpdateError,
    changelog_for_selection,
    compare_to_installed,
    download_and_install_from_url,
    download_and_install_selection,
    fetch_releases,
    filter_releases,
    format_published_at,
    github_auth_token,
    installed_version_text,
    is_actions_artifact_url,
    latest_release,
    latest_stable_release,
    select_pr_builds,
    spawn_calibre_restart,
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


class _InstallSelectionWorker(QThread):
    finished_ok = False
    failed = None
    installed_label = ""

    def __init__(self, item: ReleaseInfo, parent=None):
        super().__init__(parent)
        self.item = item
        self.installed_label = item.version_text

    def run(self):
        try:
            download_and_install_selection(self.item)
        except UpdateError as exc:
            self.failed = str(exc)
            return
        self.finished_ok = True


class _InstallUrlWorker(QThread):
    finished_ok = False
    failed = None
    installed_label = "plugin zip"

    def __init__(self, url: str, token: str | None = None, parent=None):
        super().__init__(parent)
        self.url = url
        self.token = token

    def run(self):
        try:
            download_and_install_from_url(self.url, token=self.token)
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
        self._pr_builds: list[ReleaseInfo] = []
        self._releases: list[ReleaseInfo] = []
        self._fetch_worker: _FetchReleasesWorker | None = None
        self._install_worker: QThread | None = None

        layout = QVBoxLayout()
        self.setLayout(layout)

        intro = QLabel(
            "Compare the installed plugin with GitHub Releases. Choose a "
            "release to read its changelog, install a newer build, or roll "
            "back to an older tag. Calibre must restart to load the new zip."
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

        self.include_pr_builds = QCheckBox(
            "Include PR builds (list by pull-request number)"
        )
        self.include_pr_builds.setChecked(
            bool(prefs.get("include_pr_builds", False))
        )
        self.include_pr_builds.setToolTip(
            "When checked, list the latest public PR pre-release for each "
            "pull-request number (X.Y.Z-pr.<n>+<sha>). No GitHub login required."
        )
        self.include_pr_builds.stateChanged.connect(self._on_pr_builds_toggled)
        layout.addWidget(self.include_pr_builds)

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

        notes_header = QHBoxLayout()
        notes_label = QLabel("Changelog")
        notes_label.setToolTip(
            "Release notes from GitHub. When upgrading, every listed release "
            "between your installed build and the selection is shown."
        )
        notes_header.addWidget(notes_label)
        notes_header.addStretch(1)
        self.open_github_btn = QPushButton("Open on GitHub…")
        self.open_github_btn.setEnabled(False)
        self.open_github_btn.setToolTip(
            "Open this release or pull-request build page in your browser."
        )
        self.open_github_btn.clicked.connect(self.open_selected_on_github)
        notes_header.addWidget(self.open_github_btn)
        layout.addLayout(notes_header)

        self.notes = QPlainTextEdit()
        self.notes.setReadOnly(True)
        self.notes.setPlaceholderText(
            "Changelog for the selected release will appear here."
        )
        self.notes.setMinimumHeight(180)
        layout.addWidget(self.notes)

        url_label = QLabel("Or paste a zip / release URL")
        url_label.setToolTip(
            "Public release asset URLs install without login. "
            "GitHub Actions artifact links still need a token."
        )
        layout.addWidget(url_label)

        url_row = QHBoxLayout()
        self.artifact_url = QLineEdit()
        self.artifact_url.setPlaceholderText(
            "https://github.com/…/releases/download/…/FanFicOrganizer-….zip"
        )
        self.artifact_url.setClearButtonEnabled(True)
        self.artifact_url.textChanged.connect(self._refresh_url_install)
        url_row.addWidget(self.artifact_url, stretch=1)
        self.install_url_btn = QPushButton("Install from URL…")
        self.install_url_btn.setEnabled(False)
        self.install_url_btn.clicked.connect(self.install_from_url)
        url_row.addWidget(self.install_url_btn)
        layout.addLayout(url_row)

        self.buttons = QDialogButtonBox()
        self.install_btn = self.buttons.addButton(
            "Install and restart Calibre…", QDialogButtonBox.AcceptRole
        )
        self.install_btn.setEnabled(False)
        self.close_btn = self.buttons.addButton(QDialogButtonBox.Close)
        self.close_btn.clicked.connect(self.reject)
        self.install_btn.clicked.connect(self.install_selected)
        layout.addWidget(self.buttons)

        self._refresh_url_install()
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

    def _on_pr_builds_toggled(self, _state=None):
        prefs["include_pr_builds"] = self.include_pr_builds.isChecked()
        if self._all_releases:
            self._apply_release_list()

    def _apply_release_list(self):
        include = self.include_prereleases.isChecked()
        include_prs = self.include_pr_builds.isChecked()
        self._releases = filter_releases(
            self._all_releases, include_prereleases=include
        )
        self._pr_builds = select_pr_builds(self._all_releases) if include_prs else []
        pr_builds = self._pr_builds
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
        for build in pr_builds:
            number = build.pr_number
            label = (
                f"PR #{number} · {build.version_text}"
                if number is not None
                else build.version_text
            )
            published = format_published_at(build.published_at)
            if published:
                label += f" — {published}"
            self.version_combo.addItem(label, build)
        self.version_combo.blockSignals(False)
        self.version_combo.setEnabled(bool(self._releases) or bool(pr_builds))
        latest = latest_release(self._releases)
        latest_stable = latest_stable_release(self._all_releases)
        if latest is None and not pr_builds:
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
        if latest is not None:
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
        else:
            self.latest_label.setText("No standard releases")
            self.status.setText(
                f"{len(pr_builds)} PR build(s) listed. Select a PR number to install."
            )
        if include_prs and pr_builds and latest is not None:
            self.status.setText(
                self.status.text()
                + f" {len(pr_builds)} PR build(s) also listed."
            )
        if include_prs and not pr_builds:
            self.status.setText(
                self.status.text()
                + " No PR pre-releases found yet."
            )
        if self.version_combo.count():
            self.version_combo.setCurrentIndex(0)
        self._refresh_selection()

    def _selected_item(self) -> ReleaseInfo | None:
        data = self.version_combo.currentData()
        return data if isinstance(data, ReleaseInfo) else None

    def _refresh_selection(self):
        item = self._selected_item()
        if item is None:
            self.notes.clear()
            self.install_btn.setEnabled(False)
            self.open_github_btn.setEnabled(False)
            return
        self.notes.setPlainText(changelog_for_selection(self._releases, item))
        self.notes.verticalScrollBar().setValue(0)
        self.open_github_btn.setEnabled(bool(item.html_url))
        if item.is_pr_build:
            self.artifact_url.setText(item.download_url)
            self.install_btn.setEnabled(True)
            number = item.pr_number
            label = f"PR #{number}" if number is not None else item.version_text
            self.install_btn.setText(f"Install {label} and restart Calibre…")
            return
        same = compare_to_installed(item) == 0
        self.install_btn.setEnabled(not same)
        if same:
            self.install_btn.setText("Already installed")
        else:
            direction = "Upgrade" if compare_to_installed(item) > 0 else "Downgrade"
            self.install_btn.setText(
                f"{direction} to {item.version_text} and restart Calibre…"
            )

    def _refresh_url_install(self, _text: str = ""):
        url = self.artifact_url.text().strip()
        busy = self._install_worker is not None and self._install_worker.isRunning()
        self.install_url_btn.setEnabled(bool(url) and not busy)

    def open_selected_on_github(self):
        item = self._selected_item()
        if item is None or not item.html_url:
            return
        QDesktopServices.openUrl(QUrl(item.html_url))

    def _set_busy(self, busy: bool):
        self.refresh_btn.setEnabled(not busy)
        self.version_combo.setEnabled(
            not busy and (bool(self._releases) or bool(self._pr_builds))
        )
        self.artifact_url.setEnabled(not busy)
        self.include_prereleases.setEnabled(not busy)
        self.include_pr_builds.setEnabled(not busy)
        if busy:
            self.install_btn.setEnabled(False)
            self.install_url_btn.setEnabled(False)
        else:
            self._refresh_selection()
            self._refresh_url_install()

    def install_selected(self):
        item = self._selected_item()
        if item is None:
            return
        if item.is_pr_build:
            number = item.pr_number
            label = f"PR #{number}" if number is not None else item.version_text
            prompt = (
                f"Install {label} ({item.version_text}) and restart Calibre?\n\n"
                "Downloaded from the public GitHub release asset."
            )
        else:
            current = installed_version_text()
            direction = "upgrade" if compare_to_installed(item) > 0 else "downgrade"
            prompt = (
                f"{direction.capitalize()} from {current} to {item.version_text}?\n\n"
                "The plugin zip will be downloaded from GitHub, installed with "
                "calibre-customize, and Calibre will restart."
            )
        if not question_dialog(self, "Fanfic Organizer", prompt):
            return
        QApplication.setOverrideCursor(Qt.WaitCursor)
        self._set_busy(True)
        worker = _InstallSelectionWorker(item, self)
        self._install_worker = worker
        worker.finished.connect(self._on_install_finished)
        worker.start()

    def install_from_url(self):
        url = self.artifact_url.text().strip()
        if not url:
            return
        token = None
        if is_actions_artifact_url(url):
            token = github_auth_token()
            if not token:
                error_dialog(
                    self,
                    "Fanfic Organizer",
                    "GitHub Actions artifact URLs require authentication.",
                    det_msg=(
                        "Prefer a PR pre-release from the list (Include PR builds), "
                        "or a public releases/download URL. "
                        "Otherwise set GITHUB_TOKEN / GH_TOKEN or run `gh auth login`."
                    ),
                    show=True,
                )
                return
        kind = (
            "Actions artifact"
            if is_actions_artifact_url(url)
            else "plugin zip URL"
        )
        if not question_dialog(
            self,
            "Fanfic Organizer",
            f"Install this {kind} and restart Calibre?\n\n{url}",
        ):
            return
        QApplication.setOverrideCursor(Qt.WaitCursor)
        self._set_busy(True)
        worker = _InstallUrlWorker(url, token=token, parent=self)
        self._install_worker = worker
        worker.finished.connect(self._on_install_finished)
        worker.start()

    def _on_install_finished(self):
        QApplication.restoreOverrideCursor()
        worker = self._install_worker
        self._install_worker = None
        self._set_busy(False)
        if worker is None:
            return
        label = getattr(worker, "installed_label", "plugin") or "plugin"
        if worker.failed:
            error_dialog(
                self,
                "Fanfic Organizer",
                "Could not install the selected build.",
                det_msg=worker.failed,
                show=True,
            )
            return
        try:
            spawn_calibre_restart()
        except UpdateError as exc:
            info_dialog(
                self,
                "Fanfic Organizer",
                f"Installed {label}, but Calibre could not be restarted automatically.",
                det_msg=str(exc),
                show=True,
            )
            self.accept()
            return
        info_dialog(
            self,
            "Fanfic Organizer",
            f"Installed {label}. Calibre will quit and reopen shortly.",
            show=True,
        )
        self.accept()


def show_update_check(parent=None):
    dialog = UpdateCheckDialog(parent)
    dialog.exec_()
