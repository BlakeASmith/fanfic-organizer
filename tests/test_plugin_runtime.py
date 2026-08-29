"""Bundled plugin zip extract (Calibre-free)."""

from __future__ import annotations

import importlib.util
import zipfile
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[1] / "calibre-plugin"


def load_runtime():
    spec = importlib.util.spec_from_file_location("ao3_plugin_runtime", PLUGIN / "runtime.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_bundle_zip(path: Path) -> None:
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("ao3kit/__init__.py", '__version__ = "0.1.0"\n')
        zf.writestr("ao3kit/cli.py", "def main(argv=None):\n    return 0\n")
        zf.writestr("vendor/requests/__init__.py", "")
        zf.writestr("run_ao3kit.py", "print('ok')\n")
        zf.writestr("__init__.py", "from calibre.customize import InterfaceActionBase\n")


def test_zip_has_bundled_ao3kit(tmp_path: Path):
    runtime = load_runtime()
    zip_path = tmp_path / "fanfic-organizer.zip"
    _write_bundle_zip(zip_path)
    assert runtime.zip_has_bundled_ao3kit(zip_path)
    thin = tmp_path / "thin.zip"
    with zipfile.ZipFile(thin, "w") as zf:
        zf.writestr("__init__.py", "")
        zf.writestr("run_ao3kit.py", "")
    assert not runtime.zip_has_bundled_ao3kit(thin)


def test_extract_leaves_unrelated_files(tmp_path: Path):
    runtime = load_runtime()
    zip_path = tmp_path / "fanfic-organizer.zip"
    _write_bundle_zip(zip_path)
    dest = tmp_path / "runtime"
    dest.mkdir()
    leftover = dest / "keep.txt"
    leftover.write_text("yes\n", encoding="utf-8")
    runtime.extract_bundled_runtime(zip_path, dest, version="0.25.0")
    assert (dest / "ao3kit" / "__init__.py").is_file()
    assert (dest / "vendor" / "requests" / "__init__.py").is_file()
    assert (dest / "run_ao3kit.py").is_file()
    assert leftover.read_text(encoding="utf-8") == "yes\n"


def test_load_user_dirs_from_checkout():
    runtime = load_runtime()
    dirs = runtime.load_user_dirs()
    assert dirs.jobs_dir().name == "jobs"
    assert dirs.APP_NAME == "fanfic-organizer"


def test_extract_skips_when_stamp_matches(tmp_path: Path):
    runtime = load_runtime()
    zip_path = tmp_path / "fanfic-organizer.zip"
    _write_bundle_zip(zip_path)
    dest = tmp_path / "runtime"
    runtime.extract_bundled_runtime(zip_path, dest, version="0.25.0")
    marker = dest / "ao3kit" / "__init__.py"
    original = marker.read_text(encoding="utf-8")
    marker.write_text("mutated\n", encoding="utf-8")
    runtime.extract_bundled_runtime(zip_path, dest, version="0.25.0")
    assert marker.read_text(encoding="utf-8") == "mutated\n"
    runtime.extract_bundled_runtime(zip_path, dest, version="0.25.1")
    assert marker.read_text(encoding="utf-8") == original


def test_plugin_ao3kit_command_calibre_debug():
    runtime = load_runtime()
    argv = runtime.plugin_ao3kit_command(
        "/opt/calibre-debug",
        ["tags", "enrich"],
        launcher="/rt/run_ao3kit.py",
    )
    assert argv == [
        "/opt/calibre-debug",
        "-e",
        "/rt/run_ao3kit.py",
        "--",
        "tags",
        "enrich",
    ]


def test_find_calibre_debug_sibling(tmp_path: Path):
    runtime = load_runtime()
    bindir = tmp_path / "MacOS"
    bindir.mkdir()
    gui = bindir / "calibre"
    debug = bindir / "calibre-debug"
    gui.write_text("", encoding="utf-8")
    debug.write_text("", encoding="utf-8")
    gui.chmod(0o755)
    debug.chmod(0o755)
    assert runtime.find_calibre_debug(executable=str(gui)) == str(debug)


def test_is_bundled_project(tmp_path: Path):
    runtime = load_runtime()
    project = tmp_path / "rt"
    (project / "ao3kit").mkdir(parents=True)
    (project / "ao3kit" / "__init__.py").write_text("", encoding="utf-8")
    assert not runtime.is_bundled_project(project)
    (project / "run_ao3kit.py").write_text("", encoding="utf-8")
    assert runtime.is_bundled_project(project)


def test_ensure_ao3kit_importable_with_project(tmp_path: Path, monkeypatch):
    import builtins
    import sys

    runtime = load_runtime()
    project = tmp_path / "checkout"
    (project / "ao3kit").mkdir(parents=True)
    (project / "ao3kit" / "__init__.py").write_text('__version__ = "0.0.0"\n', encoding="utf-8")
    vendor = project / "vendor"
    vendor.mkdir()

    for key in list(sys.modules):
        if key == "ao3kit" or key.startswith("ao3kit."):
            monkeypatch.delitem(sys.modules, key, raising=False)

    real_import = builtins.__import__

    def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "ao3kit" or name.startswith("ao3kit."):
            if str(project) not in sys.path:
                raise ImportError(name)
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    monkeypatch.setattr(
        sys,
        "path",
        [p for p in sys.path if Path(p).resolve() not in {project.resolve(), vendor.resolve()}],
    )

    assert runtime.ensure_ao3kit_importable(project=project)
    assert str(project) in sys.path
    assert str(vendor) in sys.path
    import ao3kit

    assert Path(ao3kit.__file__).resolve().parent == (project / "ao3kit").resolve()


def test_ensure_ao3kit_importable_false_without_roots(monkeypatch):
    import builtins
    import sys

    runtime = load_runtime()
    for key in list(sys.modules):
        if key == "ao3kit" or key.startswith("ao3kit."):
            monkeypatch.delitem(sys.modules, key, raising=False)

    real_import = builtins.__import__

    def blocked_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "ao3kit" or name.startswith("ao3kit."):
            raise ImportError(name)
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", blocked_import)
    monkeypatch.setattr(runtime, "ensure_bundled_runtime", lambda **_k: None)
    assert runtime.ensure_ao3kit_importable(project=None) is False
