from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from calibre_dev.install_release import (
    download_release_zip,
    install_release_zip,
    latest_zip_candidates,
    post_install_message,
    run_install,
)
from calibre_dev.release_urls import (
    GITHUB_REPO,
    RAW_INSTALL_SH,
    RELEASE_ZIP_NAME,
    pick_zip_download_url,
    release_tag,
    release_zip_url,
    release_zip_urls,
)

ROOT = Path(__file__).resolve().parents[1]


def test_release_zip_url_latest():
    url = release_zip_url()
    assert url.endswith(f"/releases/latest/download/{RELEASE_ZIP_NAME}")
    assert GITHUB_REPO in url


def test_release_zip_url_version():
    assert release_zip_url("0.26.1").endswith(
        "/releases/download/v0.26.1/FanFicOrganizer-0.26.1.zip"
    )
    assert release_zip_url("v0.26.1").endswith(
        "/releases/download/v0.26.1/FanFicOrganizer-0.26.1.zip"
    )


def test_release_zip_url_encodes_preview_plus():
    url = release_zip_url("0.31.0-preview.452+7a4f9b2")
    assert "%2B" in url
    assert url.endswith("FanFicOrganizer-0.31.0-preview.452%2B7a4f9b2.zip")


def test_release_zip_urls_fall_back_to_alias():
    urls = release_zip_urls("0.26.1")
    assert urls[0].endswith("FanFicOrganizer-0.26.1.zip")
    assert urls[1].endswith("fanfic-organizer.zip")


def test_pick_zip_download_url_prefers_versioned_asset():
    assets = [
        {
            "name": "fanfic-organizer.zip",
            "browser_download_url": "https://example.com/alias.zip",
        },
        {
            "name": "FanFicOrganizer-0.31.0.zip",
            "browser_download_url": "https://example.com/versioned.zip",
        },
    ]
    assert pick_zip_download_url(assets, tag="v0.31.0") == "https://example.com/versioned.zip"


def test_pick_zip_download_url_uses_legacy_alias_when_needed():
    assets = [
        {
            "name": "fanfic-organizer.zip",
            "browser_download_url": "https://example.com/alias.zip",
        }
    ]
    assert pick_zip_download_url(assets, tag="v0.26.1") == "https://example.com/alias.zip"


def test_release_tag():
    assert release_tag("0.26.1") == "v0.26.1"
    assert release_tag("v0.26.1") == "v0.26.1"


def test_raw_install_sh_points_at_repo():
    assert RAW_INSTALL_SH.endswith("/scripts/install.sh")
    assert GITHUB_REPO in RAW_INSTALL_SH


def test_download_release_zip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    payload = b"fake zip bytes"
    dest = tmp_path / RELEASE_ZIP_NAME
    versioned = (
        "https://github.com/BlakeASmith/fanfic-organizer/releases/"
        "download/v0.31.0/FanFicOrganizer-0.31.0.zip"
    )

    class FakeResponse:
        def read(self) -> bytes:
            return payload

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    def fake_urlopen(url, timeout=120):
        assert timeout == 120
        assert url == versioned
        return FakeResponse()

    import urllib.request

    monkeypatch.setattr(
        "calibre_dev.install_release.latest_zip_candidates",
        lambda: [versioned],
    )
    original = urllib.request.urlopen
    urllib.request.urlopen = fake_urlopen
    try:
        used = download_release_zip(dest)
    finally:
        urllib.request.urlopen = original

    assert used == versioned
    assert dest.read_bytes() == payload


def test_latest_zip_candidates_prefers_github_asset(
    monkeypatch: pytest.MonkeyPatch,
):
    class FakeResponse:
        def read(self) -> bytes:
            return json.dumps(
                {
                    "tag_name": "v0.31.0",
                    "assets": [
                        {
                            "name": "FanFicOrganizer-0.31.0.zip",
                            "browser_download_url": "https://example.com/FanFicOrganizer-0.31.0.zip",
                        }
                    ],
                }
            ).encode("utf-8")

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    monkeypatch.setattr(
        "calibre_dev.install_release.urllib.request.urlopen",
        lambda request, timeout=30: FakeResponse(),
    )
    urls = latest_zip_candidates()
    assert urls[0] == "https://example.com/FanFicOrganizer-0.31.0.zip"
    assert "fanfic-organizer.zip" in urls[-1]


def test_install_release_zip_calls_customize(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    zip_path = tmp_path / RELEASE_ZIP_NAME
    zip_path.write_bytes(b"zip")
    calls: list[list[str]] = []

    def fake_remove(customize: str) -> None:
        calls.append([customize, "-r"])

    def fake_run(argv, **kwargs):
        calls.append(list(argv))
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(
        "calibre_dev.install_release.remove_legacy_calibre_plugins",
        fake_remove,
    )
    monkeypatch.setattr("calibre_dev.install_release.subprocess.run", fake_run)
    monkeypatch.setattr(
        "calibre_dev.install_release.apply_fanfic_organizer_gui_names",
        lambda: True,
    )

    install_release_zip(zip_path, customize="/fake/customize")

    assert calls[0] == ["/fake/customize", "-r"]
    assert calls[1] == ["/fake/customize", "-a", str(zip_path)]


def test_post_install_message_when_running(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        "calibre_dev.install_release.list_calibre_gui_pids",
        lambda: [42],
    )
    message = post_install_message(calibre_bin="/bin/calibre")
    assert "already running" in message
    assert "quit calibre" in message.lower()


def test_post_install_message_when_started():
    message = post_install_message(
        calibre_bin="/bin/calibre",
        running=False,
        started=True,
    )
    assert "Calibre was started" in message


def test_run_install_with_local_zip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    zip_path = tmp_path / RELEASE_ZIP_NAME
    zip_path.write_bytes(b"zip")
    started = False

    monkeypatch.setattr(
        "calibre_dev.install_release.install_release_zip",
        lambda path, **kwargs: None,
    )
    monkeypatch.setattr(
        "calibre_dev.install_release.ensure_calibre_customize",
        lambda **kwargs: "/fake/calibre-customize",
    )
    monkeypatch.setattr(
        "calibre_dev.install_release.find_calibre",
        lambda: "/bin/calibre",
    )
    monkeypatch.setattr(
        "calibre_dev.install_release.list_calibre_gui_pids",
        lambda: [],
    )

    def fake_start(calibre_bin: str | None = None) -> None:
        nonlocal started
        started = True

    monkeypatch.setattr(
        "calibre_dev.install_release.start_calibre_gui",
        fake_start,
    )

    result = run_install(zip_path=zip_path)
    assert result["ok"] is True
    assert result["started"] is True
    assert started is True


def test_install_sh_syntax():
    script = ROOT / "scripts" / "install.sh"
    subprocess.run(["bash", "-n", str(script)], check=True)


def test_install_plugin_bundle_imports_without_ao3kit(tmp_path: Path):
    """Simulate the curl download bundle (no calibre.py / lock / ao3kit)."""
    bundle = tmp_path / "bundle"
    shutil = __import__("shutil")
    for rel in (
        "calibre_dev/__init__.py",
        "calibre_dev/release_urls.py",
        "calibre_dev/plugin_install.py",
        "calibre_dev/calibre_install.py",
        "calibre_dev/install_release.py",
    ):
        src = ROOT / rel
        dest = bundle / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)
    (bundle / "calibre_dev" / "__init__.py").write_text(
        '"""bundle"""\n', encoding="utf-8"
    )
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    result = subprocess.run(
        [sys.executable, "-c", "from calibre_dev.install_release import main; raise SystemExit(main(['--help']))"],
        cwd=bundle,
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert "Fanfic Organizer" in result.stdout


def test_install_plugin_from_checkout():
    script = ROOT / "scripts" / "install_plugin.py"
    result = subprocess.run(
        [sys.executable, str(script), "--help"],
        check=True,
        capture_output=True,
        text=True,
    )
    assert "Fanfic Organizer" in result.stdout
