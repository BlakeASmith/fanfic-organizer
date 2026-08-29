"""Plugin GitHub update helpers (Calibre-free)."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import types
import zipfile
from pathlib import Path

import pytest

PLUGIN = Path(__file__).resolve().parents[1] / "calibre-plugin"


def load_updates():
    if "calibre_plugins.fanfic_organizer.runtime" not in sys.modules:
        sys.modules.setdefault("calibre_plugins", types.ModuleType("calibre_plugins"))
        pkg = types.ModuleType("calibre_plugins.fanfic_organizer")
        sys.modules["calibre_plugins.fanfic_organizer"] = pkg
        runtime_spec = importlib.util.spec_from_file_location(
            "calibre_plugins.fanfic_organizer.runtime", PLUGIN / "runtime.py"
        )
        assert runtime_spec is not None and runtime_spec.loader is not None
        runtime = importlib.util.module_from_spec(runtime_spec)
        sys.modules["calibre_plugins.fanfic_organizer.runtime"] = runtime
        runtime_spec.loader.exec_module(runtime)
        pkg.runtime = runtime
    spec = importlib.util.spec_from_file_location("ao3_plugin_updates", PLUGIN / "updates.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _sample_release(
    *,
    tag: str = "v0.27.0",
    draft: bool = False,
    prerelease: bool = False,
    include_asset: bool = True,
) -> dict:
    assets = []
    version = tag[1:] if tag.startswith("v") else tag
    if include_asset:
        assets.append(
            {
                "name": f"FanFicOrganizer-{version}.zip",
                "browser_download_url": (
                    f"https://example.com/{tag}/FanFicOrganizer-{version}.zip"
                ),
                "size": 12345,
            }
        )
        assets.append(
            {
                "name": "fanfic-organizer.zip",
                "browser_download_url": f"https://example.com/{tag}/fanfic-organizer.zip",
                "size": 12345,
            }
        )
    return {
        "tag_name": tag,
        "name": tag,
        "draft": draft,
        "prerelease": prerelease,
        "published_at": "2026-08-24T12:00:00Z",
        "html_url": f"https://github.com/BlakeASmith/fanfic-organizer/releases/tag/{tag}",
        "body": "### Features\n- Add check for updates.",
        "assets": assets,
    }


def _write_bundle_zip(path: Path) -> None:
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("ao3kit/__init__.py", '__version__ = "0.27.0"\n')
        zf.writestr("run_ao3kit.py", "print('ok')\n")
        zf.writestr("__init__.py", "from calibre.customize import InterfaceActionBase\n")


def test_parse_version_accepts_optional_v_prefix():
    updates = load_updates()
    assert updates.parse_version("0.27.0") == (0, 27, 0)
    assert updates.parse_version("v0.27.0") == (0, 27, 0)


def test_release_from_api_prefers_versioned_zip_asset():
    updates = load_updates()
    record = _sample_release()
    record["assets"] = [
        {
            "name": "fanfic-organizer.zip",
            "browser_download_url": "https://example.com/alias.zip",
            "size": 1,
        },
        {
            "name": "FanFicOrganizer-0.27.0.zip",
            "browser_download_url": "https://example.com/versioned.zip",
            "size": 2,
        },
    ]
    parsed = updates.release_from_api(record)
    assert parsed is not None
    assert parsed.download_url.endswith("versioned.zip")


def test_release_from_api_skips_drafts_prereleases_and_missing_asset():
    updates = load_updates()
    assert updates.release_from_api(_sample_release(draft=True)) is None
    assert updates.release_from_api(
        _sample_release(tag="v0.27.0-pr.3+abc1234", prerelease=True)
    ) is None
    assert updates.release_from_api(_sample_release(include_asset=False)) is None
    parsed = updates.release_from_api(_sample_release())
    assert parsed is not None
    assert parsed.version == (0, 27, 0)
    assert parsed.version_text == "0.27.0"
    assert parsed.download_url.endswith("FanFicOrganizer-0.27.0.zip")


def test_release_from_api_accepts_preview_prereleases():
    updates = load_updates()
    record = _sample_release(
        tag="v0.31.0-preview.452+7a4f9b2",
        prerelease=True,
    )
    parsed = updates.release_from_api(record)
    assert parsed is not None
    assert parsed.is_preview
    assert parsed.version_text == "0.31.0-preview.452+7a4f9b2"
    assert parsed.download_url.endswith("FanFicOrganizer-0.31.0-preview.452+7a4f9b2.zip")


def test_fetch_releases_sorts_newest_first(monkeypatch: pytest.MonkeyPatch):
    updates = load_updates()
    payload = [
        _sample_release(tag="v0.25.0"),
        _sample_release(tag="v0.27.0"),
        _sample_release(tag="v0.26.0"),
        _sample_release(tag="v0.27.0-preview.5+abc1234", prerelease=True),
    ]

    monkeypatch.setattr(updates, "_github_request", lambda url: payload)
    releases = updates.fetch_releases()
    assert [item.version_text for item in releases] == [
        "0.27.0",
        "0.27.0-preview.5+abc1234",
        "0.26.0",
        "0.25.0",
    ]


def test_filter_releases_omits_previews_by_default():
    updates = load_updates()
    stable = updates.release_from_api(_sample_release(tag="v0.31.0"))
    preview = updates.release_from_api(
        _sample_release(tag="v0.32.0-preview.1+abc1234", prerelease=True)
    )
    assert stable is not None and preview is not None
    filtered = updates.filter_releases([preview, stable])
    assert [item.version_text for item in filtered] == ["0.31.0"]
    with_pre = updates.filter_releases(
        [preview, stable], include_prereleases=True
    )
    assert [item.version_text for item in with_pre] == [
        "0.32.0-preview.1+abc1234",
        "0.31.0",
    ]


def test_compare_to_installed(monkeypatch: pytest.MonkeyPatch):
    updates = load_updates()
    monkeypatch.setattr(
        updates,
        "installed_version_parsed",
        lambda: updates.ParsedVersion(0, 26, 1),
    )
    release = updates.release_from_api(_sample_release(tag="v0.27.0"))
    assert release is not None
    assert updates.compare_to_installed(release) == 1
    older = updates.release_from_api(_sample_release(tag="v0.25.0"))
    assert older is not None
    assert updates.compare_to_installed(older) == -1
    same = updates.release_from_api(_sample_release(tag="v0.26.1"))
    assert same is not None
    assert updates.compare_to_installed(same) == 0
    preview_newer = updates.release_from_api(
        _sample_release(tag="v0.27.0-preview.3+abc1234", prerelease=True)
    )
    assert preview_newer is not None
    assert updates.compare_to_installed(preview_newer) == 1
    preview_older = updates.release_from_api(
        _sample_release(tag="v0.26.0-preview.99+abc1234", prerelease=True)
    )
    assert preview_older is not None
    assert updates.compare_to_installed(preview_older) == -1


def test_stable_release_beats_preview_at_same_base():
    updates = load_updates()
    stable = updates.release_from_api(_sample_release(tag="v0.31.0"))
    preview = updates.release_from_api(
        _sample_release(tag="v0.31.0-preview.999+abc1234", prerelease=True)
    )
    assert stable is not None and preview is not None
    assert updates.compare_parsed_versions(stable.parsed, preview.parsed) == 1


def test_download_release_validates_zip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    updates = load_updates()
    good = tmp_path / "fanfic-organizer.zip"
    _write_bundle_zip(good)
    bad = tmp_path / "bad.zip"
    with zipfile.ZipFile(bad, "w") as zf:
        zf.writestr("readme.txt", "nope")

    def fake_urlopen(request, timeout=0):
        url = request.full_url
        if url.endswith("good.zip"):
            data = good.read_bytes()
        else:
            data = bad.read_bytes()

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self):
                return data

        return Response()

    monkeypatch.setattr(updates.urllib.request, "urlopen", fake_urlopen)
    dest = tmp_path / "out" / "fanfic-organizer.zip"
    updates.download_release("https://example.com/good.zip", dest)
    assert dest.is_file()
    with pytest.raises(updates.UpdateError):
        updates.download_release("https://example.com/bad.zip", tmp_path / "out2.zip")


def test_install_plugin_zip_runs_calibre_customize(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    updates = load_updates()
    zip_path = tmp_path / "fanfic-organizer.zip"
    _write_bundle_zip(zip_path)
    calls: list[list[str]] = []

    def fake_run(argv, **kwargs):
        calls.append(list(argv))
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(updates, "find_calibre_customize", lambda: "/fake/calibre-customize")
    monkeypatch.setattr(updates.subprocess, "run", fake_run)
    monkeypatch.setattr(updates, "apply_fanfic_organizer_gui_names", lambda: True)
    updates.install_plugin_zip(zip_path)
    assert calls[0] == ["/fake/calibre-customize", "-r", "AO3 Scraper"]
    assert calls[1] == ["/fake/calibre-customize", "-r", "Wranglekit"]
    assert calls[2] == ["/fake/calibre-customize", "-a", str(zip_path)]


def test_apply_fanfic_organizer_gui_names_replaces_legacy(tmp_path: Path):
    updates = load_updates()
    gui = tmp_path / "gui.json"
    gui.write_text(
        json.dumps({"action-layout-toolbar": ["AO3 Scraper", "Fanfic Organizer"]}),
        encoding="utf-8",
    )
    assert updates.apply_fanfic_organizer_gui_names(tmp_path)
    data = json.loads(gui.read_text(encoding="utf-8"))
    assert data["action-layout-toolbar"] == ["Fanfic Organizer"]


def test_spawn_calibre_restart_uses_detached_shell(monkeypatch: pytest.MonkeyPatch):
    updates = load_updates()
    captured: dict[str, object] = {}

    class FakePopen:
        def __init__(self, argv, **kwargs):
            captured["argv"] = argv
            captured["kwargs"] = kwargs

    monkeypatch.setattr(updates, "find_calibre", lambda: "/opt/calibre")
    monkeypatch.setattr(updates.subprocess, "Popen", FakePopen)
    updates.spawn_calibre_restart()
    assert captured["argv"][0] in {"sh", "cmd.exe"}
    assert "--shutdown-running-calibre" in captured["argv"][-1]
