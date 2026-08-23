"""Plugin checkout discovery (no machine-specific default paths)."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[1] / "calibre-plugin"


def load_enrich():
    spec = importlib.util.spec_from_file_location("ao3_plugin_enrich", PLUGIN / "enrich.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _fake_project(root: Path) -> Path:
    (root / "ao3kit").mkdir(parents=True)
    (root / "ao3kit" / "__init__.py").write_text("", encoding="utf-8")
    return root


def test_candidate_projects_order_and_no_hardcoded_home(tmp_path: Path):
    enrich = load_enrich()
    configured = _fake_project(tmp_path / "configured")
    env_proj = _fake_project(tmp_path / "from-env")
    stamped = _fake_project(tmp_path / "stamped")
    bundled = _fake_project(tmp_path / "bundled")
    paths = enrich._candidate_projects(
        configured=str(configured),
        env={"AO3KIT_PROJECT": str(env_proj)},
        stamp_project=str(stamped),
        bundled=bundled,
    )
    assert paths[0] == configured.resolve()
    assert paths[1] == env_proj.resolve()
    assert paths[2] == stamped.resolve()
    assert bundled.resolve() in paths
    joined = " ".join(str(p) for p in paths)
    assert "emily/ao3" not in joined
    assert "/Users/blake/" not in joined


def test_candidate_projects_empty_without_hints():
    enrich = load_enrich()
    assert enrich._candidate_projects(env={}) == []


def test_read_dev_project_stamp(tmp_path: Path):
    enrich = load_enrich()
    plugin_dir = tmp_path / "plugin"
    plugin_dir.mkdir()
    assert enrich.read_dev_project_stamp(plugin_dir) == ""
    (plugin_dir / enrich.DEV_PROJECT_STAMP).write_text(
        json.dumps({"project": "/tmp/checkout"}) + "\n",
        encoding="utf-8",
    )
    assert enrich.read_dev_project_stamp(plugin_dir) == "/tmp/checkout"


def test_read_dev_project_stamp_from_plugin_zip(tmp_path: Path):
    import zipfile

    enrich = load_enrich()
    zip_path = tmp_path / "Wranglekit.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr(
            enrich.DEV_PROJECT_STAMP,
            json.dumps({"project": "/tmp/from-zip"}) + "\n",
        )
        zf.writestr("enrich.py", "# placeholder\n")
    assert enrich.read_dev_project_stamp(zip_path) == "/tmp/from-zip"


def test_is_ao3kit_project(tmp_path: Path):
    enrich = load_enrich()
    project = _fake_project(tmp_path / "repo")
    assert enrich._is_ao3kit_project(project) == project
    nested = tmp_path / "repo" / ".ao3kit"
    nested.mkdir()
    assert enrich._is_ao3kit_project(nested) is None
    empty = tmp_path / "empty"
    empty.mkdir()
    assert enrich._is_ao3kit_project(empty) is None
