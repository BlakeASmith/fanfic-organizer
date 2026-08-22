"""Persist AO3 session cookies across CLI / plugin / web processes.

Does not store the password. The cookie file is gitignored under ``.ao3kit/``.
Disable with ``AO3KIT_SESSION_CACHE=0``; override path with ``AO3KIT_SESSION_FILE``.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from requests.cookies import RequestsCookieJar, create_cookie

SESSION_FILENAME = "ao3_session.json"
SESSION_MAX_AGE_DAYS = 14.0
AUTH_COOKIE_NAMES = frozenset(
    {
        "user_credentials",
        "_otwarchive_session",
        "remember_user_token",
    }
)


def session_cache_enabled() -> bool:
    raw = os.environ.get("AO3KIT_SESSION_CACHE", "1").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def default_session_cache_path() -> Path:
    env = os.environ.get("AO3KIT_SESSION_FILE", "").strip()
    if env:
        return Path(env).expanduser().resolve()
    from ao3kit.config import default_home

    return default_home() / SESSION_FILENAME


def clear_session_cache(path: Path | None = None) -> None:
    dest = path or default_session_cache_path()
    try:
        dest.unlink()
    except FileNotFoundError:
        return


def cookies_look_authenticated(jar: RequestsCookieJar, *, now: float | None = None) -> bool:
    stamp = time.time() if now is None else now
    for cookie in jar:
        if cookie.name not in AUTH_COOKIE_NAMES:
            continue
        expires = cookie.expires
        if expires is not None and float(expires) <= stamp:
            continue
        if cookie.value:
            return True
    return False


def load_session_cookies(
    username: str,
    *,
    path: Path | None = None,
    now: float | None = None,
) -> RequestsCookieJar | None:
    """Return a cookie jar for ``username`` if a fresh cached session exists."""
    if not session_cache_enabled():
        return None
    user = username.strip()
    if not user:
        return None
    dest = path or default_session_cache_path()
    if not dest.is_file():
        return None
    try:
        data = json.loads(dest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    saved_user = str(data.get("username") or "").strip()
    if saved_user.casefold() != user.casefold():
        return None
    saved_at = data.get("saved_at")
    stamp = time.time() if now is None else now
    try:
        age_days = (stamp - float(saved_at)) / 86400.0
    except (TypeError, ValueError):
        return None
    if age_days < 0 or age_days > SESSION_MAX_AGE_DAYS:
        return None
    jar = _dicts_to_jar(data.get("cookies") or [], now=stamp)
    if not cookies_look_authenticated(jar, now=stamp):
        return None
    return jar


def save_session_cookies(
    username: str,
    jar: RequestsCookieJar,
    *,
    path: Path | None = None,
    now: float | None = None,
) -> Path | None:
    """Write cookies for ``username``. Returns the path, or None if skipped."""
    if not session_cache_enabled():
        return None
    user = username.strip()
    if not user or not cookies_look_authenticated(jar, now=now):
        return None
    dest = path or default_session_cache_path()
    dest.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": 1,
        "username": user,
        "saved_at": time.time() if now is None else now,
        "cookies": _jar_to_dicts(jar),
    }
    tmp = dest.with_name(dest.name + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=True), encoding="utf-8")
    tmp.replace(dest)
    try:
        dest.chmod(0o600)
    except OSError:
        pass
    return dest


def persist_session(session: Any) -> None:
    """Save cookies from a requests session if it has a username and auth cookies."""
    user = getattr(session, "_ao3_username", None)
    jar = getattr(session, "cookies", None)
    if not user or jar is None:
        return
    save_session_cookies(str(user), jar)


def _jar_to_dicts(jar: RequestsCookieJar) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for cookie in jar:
        rest: dict[str, Any] = {}
        if cookie.has_nonstandard_attr("HttpOnly"):
            rest["HttpOnly"] = True
        rows.append(
            {
                "name": cookie.name,
                "value": cookie.value,
                "domain": cookie.domain,
                "path": cookie.path,
                "expires": cookie.expires,
                "secure": bool(cookie.secure),
                "rest": rest,
            }
        )
    return rows


def _dicts_to_jar(rows: Any, *, now: float) -> RequestsCookieJar:
    jar = RequestsCookieJar()
    if not isinstance(rows, list):
        return jar
    for row in rows:
        if not isinstance(row, dict):
            continue
        name = str(row.get("name") or "")
        value = row.get("value")
        if not name or value is None:
            continue
        expires = row.get("expires")
        if expires is not None:
            try:
                if float(expires) <= now:
                    continue
            except (TypeError, ValueError):
                expires = None
        domain = str(row.get("domain") or "archiveofourown.org")
        jar.set_cookie(
            create_cookie(
                name,
                str(value),
                domain=domain,
                path=str(row.get("path") or "/"),
                expires=expires,
                secure=bool(row.get("secure", True)),
                discard=False,
                rest=row.get("rest") or {},
            )
        )
    return jar
