"""Shared AO3 HTTP helpers: session, login, retries, Cloudflare, adult gate."""

from __future__ import annotations

import email.utils
import time
from pathlib import Path
from typing import Any, Callable, Mapping, MutableMapping
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv

from ao3kit.rate import (
    USER_AGENT,
    ensure_robots,
    note_request_pressure,
    note_request_success,
    note_retry_after,
    wait_for_request,
)

# Load project-local `.env` once (gitignored secrets such as AO3 login).
_PROJECT_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(_PROJECT_ROOT / ".env")

AO3_BASE = "https://archiveofourown.org"
AO3_DOMAIN = "archiveofourown.org"
AO3_LOGIN_URL = f"{AO3_BASE}/users/login"

DEFAULT_HEADERS = {
    "User-Agent": USER_AGENT,
}

# Transient upstream / edge failures worth retrying (includes Cloudflare 52x).
RETRY_STATUSES = frozenset({500, 502, 503, 504, 520, 521, 522, 523, 524, 525, 526, 530})

CLOUDFLARE_MARKERS = (
    " just a moment... ",
    " attention required! ",
    " access denied ",
    "cf-browser-verification",
    'id="challenge-error-text"',
    'id="cf-wrapper"',
    "_cf_chl_opt",
)

StatusCallback = Callable[[str], None]


class Ao3HttpError(RuntimeError):
    """Base error for AO3 HTTP failures."""


class CloudflareError(Ao3HttpError):
    """Raised when Cloudflare challenge/block pages keep returning."""


class LoginError(Ao3HttpError):
    """Raised when AO3 login fails."""


def normalize_ao3_url(url: str) -> str:
    """Force https for AO3 links; http redirects can trip Cloudflare."""
    if AO3_DOMAIN not in url.lower():
        return url
    if url.lower().startswith("http://"):
        return "https://" + url[len("http://") :]
    return url


def with_query_params(url: str, params: Mapping[str, str]) -> str:
    """Merge query params into url (overwriting existing keys)."""
    parsed = urlparse(url)
    query: dict[str, str] = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query.update(params)
    return urlunparse(parsed._replace(query=urlencode(list(query.items()))))


def with_view_adult(url: str) -> str:
    """Bypass AO3's mature/explicit confirmation interstitial."""
    return with_query_params(url, {"view_adult": "true"})


def is_cloudflare_response(response: requests.Response) -> bool:
    """Detect Cloudflare challenge / block HTML (ao3downloader markers)."""
    content_type = response.headers.get("Content-Type", "").lower()
    if not content_type.startswith("text/html"):
        return False
    body = response.text.lower()
    return any(marker in body for marker in CLOUDFLARE_MARKERS)


def _emit(on_status: StatusCallback | None, message: str) -> None:
    if on_status:
        on_status(message)


def _retry_delay(attempt: int, *, initial: float = 0.5, maximum: float = 30.0) -> float:
    return min(initial * (2**attempt), maximum)


# When AO3 returns 429 without a Retry-After header we must pick a pause.
# There is no published AO3 value for this — 60s is a pragmatic personal-use
# default (the old 300s was equally a guess, just more punishing).
DEFAULT_RETRY_AFTER_SECONDS = 60.0


def parse_retry_after(
    value: str | None,
    *,
    default: float = DEFAULT_RETRY_AFTER_SECONDS,
) -> tuple[float, bool]:
    """Parse a Retry-After header (seconds or HTTP-date).

    Returns ``(seconds, from_header)``. ``from_header`` is False when the
    header was missing/unparseable and ``default`` was used.
    """
    raw = (value or "").strip()
    if not raw:
        return float(default), False
    try:
        return max(float(raw), 1.0), True
    except ValueError:
        pass
    try:
        parsed = email.utils.parsedate_to_datetime(raw)
    except (TypeError, ValueError, OverflowError):
        return float(default), False
    if parsed is None:
        return float(default), False
    wait = parsed.timestamp() - time.time()
    return max(wait, 1.0), True


def request(
    session: requests.Session,
    method: str,
    url: str,
    *,
    data: Mapping[str, Any] | None = None,
    stream: bool = False,
    timeout: float = 60,
    view_adult: bool = False,
    max_retries: int = 8,
    max_timeouts: int = 3,
    on_status: StatusCallback | None = None,
) -> requests.Response:
    """Perform an AO3 request with process-wide rate limiting and retries."""
    url = normalize_ao3_url(url)
    if view_adult and AO3_DOMAIN in url.lower():
        url = with_view_adult(url)

    ensure_robots()
    attempt = 0
    timeouts = 0
    on_status = on_status or getattr(session, "_ao3_on_status", None)

    while True:
        wait_for_request(url, on_status=on_status)
        retry_delay = _retry_delay(attempt)
        try:
            response = session.request(
                method,
                url,
                data=data,
                timeout=timeout,
                stream=stream,
            )
        except requests.Timeout as exc:
            timeouts += 1
            if timeouts >= max_timeouts or attempt >= max_retries:
                raise Ao3HttpError(
                    f"Timed out after {timeouts} attempt(s) fetching {url}"
                ) from exc
            attempt += 1
            _emit(on_status, f"Timeout — retrying in {retry_delay:.0f}s…")
            time.sleep(retry_delay)
            continue
        except requests.RequestException as exc:
            if attempt >= max_retries:
                raise Ao3HttpError(f"Request failed for {url}: {exc}") from exc
            attempt += 1
            _emit(on_status, f"Network error — retrying in {retry_delay:.0f}s…")
            time.sleep(retry_delay)
            continue

        timeouts = 0

        if response.status_code == 429:
            pause, from_header = parse_retry_after(
                response.headers.get("Retry-After")
            )
            note_retry_after(pause)
            response.close()
            if attempt >= max_retries:
                raise Ao3HttpError(
                    f"AO3 rate-limited {url} after {attempt + 1} attempt(s) "
                    f"(Retry-After={pause:.0f}s"
                    f"{'' if from_header else ', default — header missing'})"
                )
            if from_header:
                _emit(
                    on_status,
                    f"Rate limited by AO3 — waiting {pause:.0f}s (Retry-After header)…",
                )
            else:
                _emit(
                    on_status,
                    f"Rate limited by AO3 — waiting {pause:.0f}s "
                    f"(no Retry-After header; using default)…",
                )
            time.sleep(pause)
            attempt += 1
            continue

        if response.status_code in RETRY_STATUSES:
            if attempt >= max_retries:
                response.raise_for_status()
            note_request_pressure(status_code=response.status_code)
            attempt += 1
            _emit(
                on_status,
                f"AO3 returned {response.status_code} — retrying in {retry_delay:.0f}s…",
            )
            time.sleep(retry_delay)
            continue

        if is_cloudflare_response(response):
            if attempt >= max_retries:
                raise CloudflareError(
                    "Cloudflare is blocking or challenging this connection. "
                    "Wait a bit, try a different network/IP, or turn off a VPN, then retry."
                )
            note_request_pressure(status_code=response.status_code)
            attempt += 1
            _emit(
                on_status,
                f"Cloudflare challenge detected — waiting {retry_delay:.0f}s before retry…",
            )
            time.sleep(retry_delay)
            continue

        response.raise_for_status()
        note_request_success(url)
        return response


def get(
    session: requests.Session,
    url: str,
    **kwargs: Any,
) -> requests.Response:
    return request(session, "GET", url, **kwargs)


def get_text(session: requests.Session, url: str, **kwargs: Any) -> str:
    return get(session, url, **kwargs).text


def login_to_ao3(
    session: requests.Session,
    username: str,
    password: str,
    *,
    on_status: StatusCallback | None = None,
) -> None:
    """Log in to AO3 (same form flow as ao3downloader)."""
    on_status = on_status or getattr(session, "_ao3_on_status", None)
    _emit(on_status, "Logging in to AO3…")

    response = get(session, AO3_LOGIN_URL, on_status=on_status)
    soup = BeautifulSoup(response.text, "lxml")
    form = soup.find("form", id="new_user")
    if not form:
        if is_cloudflare_response(response):
            raise CloudflareError("Cloudflare blocked the AO3 login page")
        title = soup.title.get_text(strip=True) if soup.title else "unknown page"
        raise LoginError(f"Could not find AO3 login form (page title: {title})")

    token_field = form.find("input", attrs={"name": "authenticity_token"})
    if token_field is None or not token_field.get("value"):
        raise LoginError("Could not find AO3 login authenticity token")

    payload = {
        "user[login]": username,
        "user[password]": password,
        "user[remember_me]": "1",
        "authenticity_token": token_field["value"],
    }
    response = request(
        session,
        "POST",
        AO3_LOGIN_URL,
        data=payload,
        on_status=on_status,
    )
    soup = BeautifulSoup(response.text, "lxml")
    if soup.find("body", class_="logged-in") is None:
        raise LoginError("AO3 login failed: invalid username or password")

    _emit(on_status, "Logged in to AO3")


def create_session(
    username: str | None = None,
    password: str | None = None,
    *,
    on_status: StatusCallback | None = None,
    headers: MutableMapping[str, str] | None = None,
) -> requests.Session:
    """Create a requests session, optionally logging in to AO3."""
    session = requests.Session()
    session.headers.update(headers or DEFAULT_HEADERS)
    if on_status is not None:
        session._ao3_on_status = on_status  # type: ignore[attr-defined]
    if username and password:
        login_to_ao3(session, username, password, on_status=on_status)
    elif username or password:
        raise LoginError("Both username and password are required to log in to AO3")
    return session
