"""Install Calibre when it is missing (stdlib-only; safe for curl bundle)."""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

CALIBRE_LINUX_INSTALLER_URL = "https://download.calibre-ebook.com/linux-installer.sh"
DEFAULT_LINUX_INSTALL_DIR = Path.home() / ".local" / "opt" / "calibre"
MAC_CALIBRE_BIN = Path("/Applications/calibre.app/Contents/MacOS")


def linux_isolated_install_dir() -> Path:
    override = (os.environ.get("FANFIC_ORGANIZER_CALIBRE_DIR") or "").strip()
    if override:
        return Path(override).expanduser().resolve()
    return DEFAULT_LINUX_INSTALL_DIR.expanduser().resolve()


def isolated_calibre_bin_dirs() -> list[Path]:
    """Directories that may contain ``calibre`` / ``calibre-customize`` binaries."""
    dirs: list[Path] = []
    if sys.platform == "darwin":
        dirs.append(MAC_CALIBRE_BIN)
    else:
        for base in (
            linux_isolated_install_dir() / "calibre",
            Path.home() / "calibre-bin" / "calibre",
            Path("/opt") / "calibre",
        ):
            dirs.append(base)
    if os.name == "nt":
        dirs.extend(
            [
                Path(os.environ.get("ProgramFiles", "")) / "Calibre2",
                Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "calibre",
                Path.home() / "AppData" / "Local" / "Programs" / "calibre",
            ]
        )
    return dirs


def calibre_tool_candidates(name: str) -> list[Path]:
    ext = ".exe" if os.name == "nt" else ""
    tool_name = f"{name}{ext}"
    candidates: list[Path] = []
    for directory in isolated_calibre_bin_dirs():
        candidates.append(directory / tool_name)
    return candidates


def try_find_calibre_tool(name: str) -> str | None:
    found = shutil.which(name)
    if found:
        return found
    for candidate in calibre_tool_candidates(name):
        if candidate.is_file():
            return str(candidate)
    return None


def _run_checked(
    argv: list[str],
    *,
    input_text: str | None = None,
    timeout: float | None = None,
) -> None:
    subprocess.run(
        argv,
        check=True,
        capture_output=True,
        text=True,
        input=input_text,
        timeout=timeout,
    )


def _download_bytes(url: str, *, timeout: float = 300) -> bytes:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            data = response.read()
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"Could not download {url} (HTTP {exc.code}).") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Could not download {url}: {exc.reason}.") from exc
    if not data:
        raise RuntimeError(f"Download from {url} was empty.")
    return data


def install_calibre_linux_isolated(install_dir: Path | None = None) -> Path:
    """Run Calibre's official Linux installer in isolated (no-root) mode."""
    target = (install_dir or linux_isolated_install_dir()).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    script = _download_bytes(CALIBRE_LINUX_INSTALLER_URL, timeout=300).decode(
        "utf-8", errors="replace"
    )
    _run_checked(
        ["sh", "/dev/stdin", f"install_dir={target}", "isolated=y"],
        input_text=script,
        timeout=600,
    )
    bin_dir = target / "calibre"
    customize = bin_dir / "calibre-customize"
    if not customize.is_file():
        raise RuntimeError(
            f"Calibre install finished but {customize} was not found."
        )
    return bin_dir


def install_calibre_macos() -> None:
    brew = shutil.which("brew")
    if not brew:
        raise RuntimeError(
            "Calibre is not installed and Homebrew was not found. "
            "Install Calibre from https://calibre-ebook.com/download_osx "
            "or install Homebrew, then rerun this installer."
        )
    env = os.environ.copy()
    env.setdefault("HOMEBREW_NO_AUTO_UPDATE", "1")
    _run_checked(
        [brew, "install", "--cask", "calibre", "--no-quarantine"],
        timeout=900,
    )


def install_calibre_windows() -> None:
    winget = shutil.which("winget")
    if winget:
        _run_checked(
            [
                winget,
                "install",
                "--id",
                "calibre.calibre",
                "--exact",
                "--silent",
                "--accept-package-agreements",
                "--accept-source-agreements",
            ],
            timeout=900,
        )
        return
    raise RuntimeError(
        "Calibre is not installed and winget was not found. "
        "Install Calibre from https://calibre-ebook.com/download_windows "
        "or install winget, then rerun this installer."
    )


def install_calibre() -> None:
    """Install Calibre using the best method for the current OS."""
    system = platform.system()
    if system == "Linux":
        install_calibre_linux_isolated()
        return
    if system == "Darwin":
        install_calibre_macos()
        return
    if system == "Windows" or os.name == "nt":
        install_calibre_windows()
        return
    raise RuntimeError(
        f"Automatic Calibre install is not supported on {system}. "
        "Install Calibre from https://calibre-ebook.com/download, then rerun."
    )


def ensure_calibre_customize(
    *,
    install_if_missing: bool = True,
) -> str:
    """Return ``calibre-customize`` path, installing Calibre when allowed."""
    found = try_find_calibre_tool("calibre-customize")
    if found:
        return found
    if not install_if_missing:
        raise FileNotFoundError(
            "calibre-customize not found. Install Calibre or add it to PATH."
        )
    install_calibre()
    found = try_find_calibre_tool("calibre-customize")
    if found:
        return found
    raise FileNotFoundError(
        "Calibre was installed but calibre-customize is still not on PATH. "
        "Open a new terminal or add Calibre's folder to PATH, then rerun."
    )


__all__ = [
    "CALIBRE_LINUX_INSTALLER_URL",
    "DEFAULT_LINUX_INSTALL_DIR",
    "ensure_calibre_customize",
    "install_calibre",
    "install_calibre_linux_isolated",
    "isolated_calibre_bin_dirs",
    "linux_isolated_install_dir",
    "try_find_calibre_tool",
]
