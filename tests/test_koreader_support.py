"""KOReader plugin support must not crash Calibre when ao3kit is missing."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace

PLUGIN = Path(__file__).resolve().parents[1] / "calibre-plugin"


def _load_koreader_support(monkeypatch):
    """Load koreader_support with Calibre/Qt stubs."""
    import sys

    qt = SimpleNamespace(QTimer=SimpleNamespace(singleShot=lambda *_a, **_k: None))
    monkeypatch.setitem(sys.modules, "PyQt5", SimpleNamespace(Qt=qt))
    monkeypatch.setitem(sys.modules, "PyQt5.Qt", qt)
    monkeypatch.setitem(
        sys.modules,
        "calibre",
        SimpleNamespace(gui2=SimpleNamespace(error_dialog=None, info_dialog=None)),
    )
    monkeypatch.setitem(
        sys.modules,
        "calibre.gui2",
        SimpleNamespace(error_dialog=lambda *a, **k: None, info_dialog=lambda *a, **k: None),
    )

    prefs_mod = SimpleNamespace(prefs={"koreader_path": ".adds/koreader"})
    monkeypatch.setitem(sys.modules, "calibre_plugins", SimpleNamespace())
    monkeypatch.setitem(
        sys.modules,
        "calibre_plugins.fanfic_organizer",
        SimpleNamespace(prefs=prefs_mod, runtime=None),
    )
    monkeypatch.setitem(sys.modules, "calibre_plugins.fanfic_organizer.prefs", prefs_mod)

    runtime_spec = importlib.util.spec_from_file_location(
        "calibre_plugins.fanfic_organizer.runtime", PLUGIN / "runtime.py"
    )
    assert runtime_spec is not None and runtime_spec.loader is not None
    runtime = importlib.util.module_from_spec(runtime_spec)
    runtime_spec.loader.exec_module(runtime)
    monkeypatch.setitem(sys.modules, "calibre_plugins.fanfic_organizer.runtime", runtime)

    spec = importlib.util.spec_from_file_location(
        "calibre_plugins.fanfic_organizer.koreader_support",
        PLUGIN / "koreader_support.py",
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module, runtime


def test_deploy_ready_false_when_ao3kit_missing(monkeypatch):
    module, runtime = _load_koreader_support(monkeypatch)
    monkeypatch.setattr(runtime, "ensure_ao3kit_importable", lambda **_k: False)

    class _Device:
        DEVICE_PLUGBOARD_NAME = "KOBOTOUCH"

    plugin = SimpleNamespace(
        gui=SimpleNamespace(
            device_manager=SimpleNamespace(
                is_device_connected=True,
                connected_device=_Device(),
            )
        )
    )
    support = module.KoreaderSupport(plugin)
    assert support.deploy_ready() is False


def test_deploy_ready_false_without_device(monkeypatch):
    module, _runtime = _load_koreader_support(monkeypatch)
    plugin = SimpleNamespace(
        gui=SimpleNamespace(
            device_manager=SimpleNamespace(is_device_connected=False, connected_device=None)
        )
    )
    support = module.KoreaderSupport(plugin)
    assert support.deploy_ready() is False


def test_deploy_ready_uses_bootstrapped_detect(monkeypatch):
    module, runtime = _load_koreader_support(monkeypatch)
    calls = {"deployable": 0}

    class _Detect:
        @staticmethod
        def koreader_deployable(device, *, koreader_subdir=".adds/koreader"):
            calls["deployable"] += 1
            assert device is not None
            assert koreader_subdir == ".adds/koreader"
            return True

    monkeypatch.setattr(runtime, "ensure_ao3kit_importable", lambda **_k: True)

    def fake_import(self):
        return _Detect, SimpleNamespace()

    monkeypatch.setattr(module.KoreaderSupport, "_import_koreader", fake_import)

    plugin = SimpleNamespace(
        gui=SimpleNamespace(
            device_manager=SimpleNamespace(
                is_device_connected=True,
                connected_device=SimpleNamespace(DEVICE_PLUGBOARD_NAME="KOBOTOUCH"),
            )
        )
    )
    support = module.KoreaderSupport(plugin)
    assert support.deploy_ready() is True
    assert calls["deployable"] == 1
