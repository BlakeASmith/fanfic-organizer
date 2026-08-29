"""Manual deploy of fanfic-organizer collections to KOReader on Kobo."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from PyQt5.Qt import QTimer

from calibre.gui2 import error_dialog, info_dialog

from calibre_plugins.fanfic_organizer.prefs import prefs


class KoreaderSupport:
    """Explicit Deploy to KOReader action (no automatic USB hooks)."""

    def __init__(self, plugin) -> None:
        self.plugin = plugin
        self._deploy_pending = False

    def koreader_subdir(self) -> str:
        raw = str(prefs.get("koreader_path") or "").strip()
        return raw or ".adds/koreader"

    def connected_device(self):
        gui = self.plugin.gui
        device_manager = getattr(gui, "device_manager", None)
        if device_manager is None or not device_manager.is_device_connected:
            return None
        return device_manager.connected_device

    def deploy_ready(self) -> bool:
        device = self.connected_device()
        if device is None:
            return False
        from ao3kit.koreader.detect import koreader_deployable

        return koreader_deployable(device, koreader_subdir=self.koreader_subdir())

    def deploy(self, *, silent: bool = False) -> None:
        """Deploy when the user asks; wait out an in-progress device sync first."""
        if self._deploy_pending:
            return
        self._deploy_pending = True
        QTimer.singleShot(0, lambda: self._deploy_when_ready(silent=silent))

    def _deploy_when_ready(self, *, silent: bool) -> None:
        gui = self.plugin.gui
        job_manager = getattr(gui, "job_manager", None)
        if job_manager is not None and job_manager.has_device_jobs():
            QTimer.singleShot(1000, lambda: self._deploy_when_ready(silent=silent))
            return
        self._deploy_pending = False
        self._run_deploy(silent=silent)

    def _run_deploy(self, *, silent: bool) -> dict[str, Any] | None:
        from ao3kit.koreader.detect import KoreaderDetectionError

        device = self.connected_device()
        gui = self.plugin.gui
        if device is None:
            if not silent:
                error_dialog(
                    gui,
                    "Fanfic Organizer",
                    "Connect your Kobo or Android phone via USB first.",
                    show=True,
                )
            return None
        try:
            result = self._deploy_to_device(gui.current_db, device)
        except KoreaderDetectionError as exc:
            if not silent:
                detail = exc.message
                if exc.hint:
                    detail = f"{exc.message}\n\n{exc.hint}"
                error_dialog(
                    gui,
                    "Fanfic Organizer",
                    "This device is not ready for KOReader collections deploy.",
                    det_msg=detail,
                    show=True,
                )
            return None
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
