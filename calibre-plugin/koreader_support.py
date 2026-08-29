# -*- coding: utf-8 -*-
"""Deploy fanfic-organizer collections to KOReader on USB sync."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from PyQt5.Qt import QTimer

from calibre.gui2 import error_dialog, info_dialog

from calibre_plugins.fanfic_organizer.prefs import prefs


class KoreaderSupport:
    """USB hooks and manual deploy for the KOReader collections add-on."""

    def __init__(self, plugin) -> None:
        self.plugin = plugin
        self._deploy_pending = False
        self._connected = False

    def connect(self) -> None:
        try:
            from calibre.gui2.device import device_signals
        except ImportError:
            return
        device_signals.device_connection_changed.connect(self._on_device_connection_changed)
        device_signals.device_metadata_available.connect(self._on_device_metadata_available)

    def enabled(self) -> bool:
        return bool(prefs.get("koreader_enabled", False))

    def koreader_subdir(self) -> str:
        raw = str(prefs.get("koreader_path") or "").strip()
        return raw or ".adds/koreader"

    def _on_device_connection_changed(self, connected: bool) -> None:
        self._connected = bool(connected)

    def _on_device_metadata_available(self) -> None:
        if not self.enabled():
            return
        self.schedule_deploy()

    def schedule_deploy(self) -> None:
        if self._deploy_pending:
            return
        self._deploy_pending = True
        QTimer.singleShot(500, self._try_deploy)

    def schedule_deploy_if_connected(self) -> None:
        if not self.enabled():
            return
        gui = self.plugin.gui
        device_manager = getattr(gui, "device_manager", None)
        if device_manager is None or not device_manager.is_device_connected:
            return
        self.schedule_deploy()

    def _try_deploy(self) -> None:
        gui = self.plugin.gui
        job_manager = getattr(gui, "job_manager", None)
        if job_manager is not None and job_manager.has_device_jobs():
            QTimer.singleShot(1000, self._try_deploy)
            return
        self._deploy_pending = False
        self.deploy(silent=True)

    def deploy(self, *, silent: bool = False) -> dict[str, Any] | None:
        if not self.enabled():
            if not silent:
                error_dialog(
                    self.plugin.gui,
                    "Fanfic Organizer",
                    "Enable KOReader support in Plugin settings first.",
                    show=True,
                )
            return None
        gui = self.plugin.gui
        device_manager = getattr(gui, "device_manager", None)
        if device_manager is None or not device_manager.is_device_connected:
            if not silent:
                error_dialog(
                    gui,
                    "Fanfic Organizer",
                    "Connect your Kobo (or other device) via USB first.",
                    show=True,
                )
            return None
        device = device_manager.connected_device
        if device is None:
            if not silent:
                error_dialog(
                    gui,
                    "Fanfic Organizer",
                    "No device is connected.",
                    show=True,
                )
            return None
        try:
            result = self._deploy_to_device(gui.current_db, device)
        except Exception as exc:
            if not silent:
                error_dialog(
                    gui,
                    "Fanfic Organizer",
                    "Could not deploy collections to KOReader.",
                    det_msg=str(exc),
                    show=True,
                )
            return None
        if not silent:
            books = int(result.get("books") or 0)
            info_dialog(
                gui,
                "Fanfic Organizer",
                f"Deployed collections for {books} book(s) on the device.\n\n"
                "In KOReader: Search → Fanfic collections.",
                show=True,
            )
        return result

    def _deploy_to_device(self, db, device) -> dict[str, Any]:
        from ao3kit.koreader.deploy import deploy_to_device, resolve_bundled_plugin_source

        from calibre_plugins.fanfic_organizer.enrich import read_dev_project_stamp
        from calibre_plugins.fanfic_organizer.runtime import installed_plugin_zip

        checkout = Path(read_dev_project_stamp() or "")
        plugin_zip = installed_plugin_zip()
        plugin_source = resolve_bundled_plugin_source(
            plugin_zip=plugin_zip,
            checkout_root=checkout if checkout.is_dir() else None,
        )
        return deploy_to_device(
            db,
            device,
            plugin_source=plugin_source,
            install_koplugin=plugin_source is not None,
            koreader_subdir=self.koreader_subdir(),
        )
