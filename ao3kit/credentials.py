"""AO3 username and password for the desk and CLI.

Stored in the XDG config dir (mode 0600), separate from ``config.yaml`` so
``config show`` does not print the password. Environment variables and a
project ``.env`` still win when they are already set.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

FILENAME = "ao3-login.json"


def credentials_path(home: Path | None = None) -> Path:
    if home is not None:
        return Path(home) / FILENAME
    from ao3kit.paths import config_dir

    return config_dir() / FILENAME


def read_credentials(home: Path | None = None) -> tuple[str, str]:
    path = credentials_path(home)
    if not path.is_file():
        return "", ""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return "", ""
    if not isinstance(data, dict):
        return "", ""
    return str(data.get("username") or "").strip(), str(data.get("password") or "")


def write_credentials(username: str, password: str, *, home: Path | None = None) -> None:
    path = credentials_path(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(
        {"username": username.strip(), "password": password},
        ensure_ascii=False,
    )
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(payload + "\n", encoding="utf-8")
    os.chmod(tmp, 0o600)
    tmp.replace(path)
    os.chmod(path, 0o600)


def clear_credentials(home: Path | None = None) -> None:
    path = credentials_path(home)
    try:
        path.unlink()
    except FileNotFoundError:
        return


def apply_saved_login_env(home: Path | None = None) -> None:
    """Fill ``AO3_USERNAME`` / ``AO3_PASSWORD`` when they are still empty."""
    username, password = read_credentials(home)
    if username and not os.environ.get("AO3_USERNAME"):
        os.environ["AO3_USERNAME"] = username
    if password and not os.environ.get("AO3_PASSWORD"):
        os.environ["AO3_PASSWORD"] = password
