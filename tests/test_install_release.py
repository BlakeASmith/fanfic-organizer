from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from calibre_dev.install_release import (
    download_release_zip,
    install_release_zip,
    post_install_message,
    run_install,
)
from calibre_dev.release_urls import (
    GITHUB_REPO,
    RAW_INSTALL_SH,
    RELEASE_ZIP_NAME,
    release_tag,
    release_zip_url,
)

ROOT = Path(__file__).resolve().parents[1]


def test_release_zip_url_latest():
    url = release_zip_url()
    assert url.endswith(f"/releases/latest/download/{RELEASE_ZIP_NAME}")
    assert GITHUB_REPO in url


def test_release_zip_url_version():
    assert release_zip_url("0.26.1").endswith(
        f"/releases/download/v0.26.1/{RELEASE_ZIP_NAME}"
    )
    assert release_zip_url("v0.26.1").endswith(
        f"/releases/download/v0.26.1/{RELEASE_ZIP_NAME}"
    )


def test_release_tag():
    assert release_tag("0.26.1") == "v0.26.1"
    assert release_tag("v0.26.1") == "v0.26.1"


def test_raw_install_sh_points_at_repo():
    assert RAW_INSTALL_SH.endswith("/scripts/install.sh")
    assert GITHUB_REPO in RAW_INSTALL_SH


def test_download_release_zip(tmp_path: Path):
    payload = b"fake zip bytes"
    dest = tmp_path / RELEASE_ZIP_NAME

    class FakeResponse:
        def read(self) -> bytes:
            return payload

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    def fake_urlopen(url, timeout=120):
        assert timeout == 120
        assert url == release_zip_url()
        return FakeResponse()

    import urllib.request

    original = urllib.request.urlopen
    urllib.request.urlopen = fake_urlopen
    try:
        used = download_release_zip(dest)
    finally:
        urllib.request.urlopen = original

    assert used == release_zip_url()
    assert dest.read_bytes() == payload


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


def test_install_plugin_from_checkout():
    script = ROOT / "scripts" / "install_plugin.py"
    result = subprocess.run(
        [sys.executable, str(script), "--help"],
        check=True,
        capture_output=True,
        text=True,
    )
    assert "Fanfic Organizer" in result.stdout
