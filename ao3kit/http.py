"""Shared AO3 HTTP helpers: session, login, retries, Cloudflare, adult gate."""

from __future__ import annotations

import email.utils
import time
from pathlib import Path
from typing import Any, Callable, Mapping, MutableMapping
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import requests

from ao3kit.htmlsoup import parse_html

from ao3kit.rate import (
    get_default_retry_after,
    USER_AGENT,
    ensure_robots,
    interval_for_url,
    note_request_pressure,
    note_request_success,
    note_retry_after,
    record_request_event,
    url_kind,
    wait_for_request,
)
from ao3kit.session_cache import (
    clear_session_cache,
    load_session_cookies,
    persist_session,
)

# Load project-local `.env` once (gitignored secrets such as AO3 login).
_PROJECT_ROOT = Path(__file__).resolve().parents[1]
try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover — vendored in the plugin zip
    def load_dotenv(*_args: Any, **_kwargs: Any) -> bool:
        return False

load_dotenv(_PROJECT_ROOT / ".env")

AO3_BASE = "https://archiveofourown.org"
AO3_DOMAIN = "archiveofourown.org"
AO3_LOGIN_URL = f"{AO3_BASE}/users/login"
# Hung / unreachable AO3 pages used to sit on the old 60s socket timeout for
# several attempts (~3 min per URL). Match login: fail the socket sooner.
DEFAULT_REQUEST_TIMEOUT = 20.0
LOGIN_REQUEST_TIMEOUT = DEFAULT_REQUEST_TIMEOUT
# Large EPUB streams need a longer read window than HTML pages.
EPUB_DOWNLOAD_TIMEOUT = 120.0

DEFAULT_HEADERS = {
    "User-Agent": USER_AGENT,
}

# Cloudflare origin timeouts — the edge answered, but AO3 did not. Treat like
# a socket timeout and stop after ``max_timeouts`` instead of a long 5xx loop.
ORIGIN_TIMEOUT_STATUSES = frozenset({522, 524})

# Transient upstream / edge failures worth retrying (other Cloudflare 52x).
RETRY_STATUSES = frozenset({500, 502, 503, 504, 520, 521, 523, 525, 526, 530})

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


def _log_attempt(
    *,
    url: str,
    method: str,
    outcome: str,
    wait_s: float,
    interval_s: float,
    elapsed_s: float,
    attempt: int,
    status: int | None = None,
    retry_after_s: float | None = None,
    retry_after_from_header: bool | None = None,
) -> None:
    record_request_event(
        url=url,
        method=method,
        outcome=outcome,
        wait_s=wait_s,
        interval_s=interval_s,
        elapsed_s=elapsed_s,
        status=status,
        retry_after_s=retry_after_s,
        retry_after_from_header=retry_after_from_header,
        attempt=attempt + 1,
    )


def request(
    session: requests.Session,
    method: str,
    url: str,
    *,
    data: Mapping[str, Any] | None = None,
    stream: bool = False,
    timeout: float | tuple[float, float] = DEFAULT_REQUEST_TIMEOUT,
    view_adult: bool = False,
    max_retries: int = 5,
    max_timeouts: int = 2,
    on_status: StatusCallback | None = None,
) -> requests.Response:
    """Perform an AO3 request with process-wide rate limiting and retries.

    Socket hangs and Cloudflare origin timeouts (522/524) share ``max_timeouts``
    so a dead or missing page fails in tens of seconds instead of minutes.
    """
    url = normalize_ao3_url(url)
    if view_adult and AO3_DOMAIN in url.lower():
        url = with_view_adult(url)

    on_status = on_status or getattr(session, "_ao3_on_status", None)
    ensure_robots(on_status=on_status)
    attempt = 0
    timeouts = 0

    while True:
        interval = interval_for_url(url)
        waited = wait_for_request(url, on_status=on_status)
        retry_delay = _retry_delay(attempt)
        started = time.monotonic()
        try:
            response = session.request(
                method,
                url,
                data=data,
                timeout=timeout,
                stream=stream,
            )
        except requests.Timeout as exc:
            elapsed = time.monotonic() - started
            timeouts += 1
            _log_attempt(
                url=url,
                method=method,
                outcome="timeout",
                wait_s=waited,
                interval_s=interval,
                elapsed_s=elapsed,
                attempt=attempt,
            )
            if timeouts >= max_timeouts or attempt >= max_retries:
                raise Ao3HttpError(
                    f"Timed out after {timeouts} attempt(s) fetching {url}"
                ) from exc
            attempt += 1
            _emit(on_status, f"Timeout — retrying in {retry_delay:.0f}s…")
            time.sleep(retry_delay)
            continue
        except requests.RequestException as exc:
            elapsed = time.monotonic() - started
            _log_attempt(
                url=url,
                method=method,
                outcome="error",
                wait_s=waited,
                interval_s=interval,
                elapsed_s=elapsed,
                attempt=attempt,
            )
            if attempt >= max_retries:
                raise Ao3HttpError(f"Request failed for {url}: {exc}") from exc
            attempt += 1
            _emit(on_status, f"Network error — retrying in {retry_delay:.0f}s…")
            time.sleep(retry_delay)
            continue

        elapsed = time.monotonic() - started

        if response.status_code == 429:
            timeouts = 0
            pause, from_header = parse_retry_after(
                response.headers.get("Retry-After")
            )
            if not from_header and url_kind(url) == "tag":
                pause = min(pause, get_default_retry_after())
            note_retry_after(pause, url=url)
            _log_attempt(
                url=url,
                method=method,
                outcome="429",
                wait_s=waited,
                interval_s=interval,
                elapsed_s=elapsed,
                attempt=attempt,
                status=429,
                retry_after_s=pause,
                retry_after_from_header=from_header,
            )
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

        if response.status_code in ORIGIN_TIMEOUT_STATUSES:
            timeouts += 1
            _log_attempt(
                url=url,
                method=method,
                outcome="timeout",
                wait_s=waited,
                interval_s=interval,
                elapsed_s=elapsed,
                attempt=attempt,
                status=response.status_code,
            )
            if timeouts >= max_timeouts or attempt >= max_retries:
                raise Ao3HttpError(
                    f"Timed out after {timeouts} attempt(s) fetching {url} "
                    f"(HTTP {response.status_code})"
                )
            note_request_pressure(status_code=response.status_code)
            attempt += 1
            _emit(
                on_status,
                f"AO3 origin timed out ({response.status_code}) — "
                f"retrying in {retry_delay:.0f}s…",
            )
            response.close()
            time.sleep(retry_delay)
            continue

        timeouts = 0

        if response.status_code in RETRY_STATUSES:
            _log_attempt(
                url=url,
                method=method,
                outcome="5xx",
                wait_s=waited,
                interval_s=interval,
                elapsed_s=elapsed,
                attempt=attempt,
                status=response.status_code,
            )
            if attempt >= max_retries:
                response.raise_for_status()
            note_request_pressure(status_code=response.status_code)
            attempt += 1
            _emit(
                on_status,
                f"AO3 returned {response.status_code} — retrying in {retry_delay:.0f}s…",
            )
            response.close()
            time.sleep(retry_delay)
            continue

        if is_cloudflare_response(response):
            _log_attempt(
                url=url,
                method=method,
                outcome="cloudflare",
                wait_s=waited,
                interval_s=interval,
                elapsed_s=elapsed,
                attempt=attempt,
                status=response.status_code,
            )
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
            response.close()
            time.sleep(retry_delay)
            continue

        try:
            response.raise_for_status()
        except Exception:
            _log_attempt(
                url=url,
                method=method,
                outcome="error",
                wait_s=waited,
                interval_s=interval,
                elapsed_s=elapsed,
                attempt=attempt,
                status=response.status_code,
            )
            raise
        note_request_success(url)
        _log_attempt(
            url=url,
            method=method,
            outcome="ok",
            wait_s=waited,
            interval_s=interval,
            elapsed_s=elapsed,
            attempt=attempt,
            status=response.status_code,
        )
        return response


def get(
    session: requests.Session,
    url: str,
    **kwargs: Any,
) -> requests.Response:
    response = request(session, "GET", url, **kwargs)
    if kwargs.get("stream"):
        return response
    if _should_refresh_login(session, url, response):
        response.close()
        session._ao3_logged_in = False  # type: ignore[attr-defined]
        clear_session_cache()
        _emit(
            kwargs.get("on_status") or getattr(session, "_ao3_on_status", None),
            "Saved AO3 session expired — logging in again…",
        )
        if ensure_logged_in(session, on_status=kwargs.get("on_status")):
            return request(session, "GET", url, **kwargs)
        raise LoginError("AO3 session expired and re-login failed")
    if is_session_logged_in(session) and _html_is_logged_in(response):
        persist_session(session)
    return response


def _response_html(response: requests.Response) -> str | None:
    headers = getattr(response, "headers", None) or {}
    content_type = str(headers.get("Content-Type") or "").lower()
    if content_type and "html" not in content_type and not content_type.startswith("text/"):
        return None
    try:
        text = response.text
    except Exception:
        return None
    return text if text else None


def _body_classes(html: str) -> list[str]:
    soup = parse_html(html)
    body = soup.find("body")
    if body is None:
        return []
    classes = body.get("class") or []
    if isinstance(classes, str):
        return classes.split()
    return [str(item) for item in classes]


def _html_is_logged_in(response: requests.Response) -> bool:
    html = _response_html(response)
    if html is None:
        return False
    return "logged-in" in _body_classes(html)


def _should_refresh_login(
    session: requests.Session, url: str, response: requests.Response
) -> bool:
    if not is_session_logged_in(session):
        return False
    if AO3_DOMAIN not in url.lower():
        return False
    if "/users/login" in urlparse(url).path:
        return False
    html = _response_html(response)
    if html is None:
        return False
    if "logged-out" in _body_classes(html):
        return True
    return is_login_wall(html)


def is_login_wall(html: str) -> bool:
    """True when AO3 served the registered-users login interstitial."""
    soup = parse_html(html)
    main = soup.find("div", id="main")
    classes = main.get("class", []) if main else []
    if "sessions-new" in classes:
        return True
    text = soup.get_text(" ", strip=True)
    return "only available to registered users" in text.lower()


def attach_credentials(
    session: requests.Session, username: str, password: str
) -> None:
    """Remember AO3 credentials on the session for a later login."""
    session._ao3_username = username  # type: ignore[attr-defined]
    session._ao3_password = password  # type: ignore[attr-defined]


def is_session_logged_in(session: requests.Session) -> bool:
    return bool(getattr(session, "_ao3_logged_in", False))


def ensure_logged_in(
    session: requests.Session,
    *,
    username: str | None = None,
    password: str | None = None,
    on_status: StatusCallback | None = None,
) -> bool:
    """Log in if credentials are available and the session is anonymous.

    Returns True if the session is (now) logged in.
    """
    if is_session_logged_in(session):
        return True
    user = username or getattr(session, "_ao3_username", None)
    pwd = password or getattr(session, "_ao3_password", None)
    if user is not None:
        user = str(user).strip() or None
    if not user or not pwd:
        return False
    login_to_ao3(session, str(user), str(pwd), on_status=on_status)
    return True


def get_text(
    session: requests.Session,
    url: str,
    *,
    login_if_needed: bool = False,
    **kwargs: Any,
) -> str:
    html = get(session, url, **kwargs).text
    if (
        login_if_needed
        and is_login_wall(html)
        and ensure_logged_in(session, on_status=kwargs.get("on_status"))
    ):
        html = get(session, url, **kwargs).text
    return html


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

    response = get(
        session, AO3_LOGIN_URL, on_status=on_status, timeout=LOGIN_REQUEST_TIMEOUT
    )
    soup = parse_html(response.text)
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
        timeout=LOGIN_REQUEST_TIMEOUT,
    )
    soup = parse_html(response.text)
    if soup.find("body", class_="logged-in") is None:
        raise LoginError("AO3 login failed: invalid username or password")

    attach_credentials(session, username, password)
    session._ao3_logged_in = True  # type: ignore[attr-defined]
    persist_session(session)
    _emit(on_status, "Logged in to AO3")


def create_session(
    username: str | None = None,
    password: str | None = None,
    *,
    on_status: StatusCallback | None = None,
    headers: MutableMapping[str, str] | None = None,
    login: bool = True,
    use_session_cache: bool = True,
) -> requests.Session:
    """Create a requests session, optionally logging in to AO3.

    Credentials are stored on the session when both are provided.
    ``login=True`` (default) authenticates immediately — scrape and EPUB
    download need this for restricted works. ``login=False`` stays anonymous
    until a page returns a login wall (tag lookups), unless a saved session
    for this username is restored.

    ``use_session_cache`` reuses cookies from the XDG state session file
    so plugin/CLI subprocesses skip the login GET+POST. ``verify_login``
    turns this off so Test login always hits AO3.
    """
    session = requests.Session()
    session.headers.update(headers or DEFAULT_HEADERS)
    session._ao3_logged_in = False  # type: ignore[attr-defined]
    if on_status is not None:
        session._ao3_on_status = on_status  # type: ignore[attr-defined]
    if username and password:
        attach_credentials(session, username, password)
        restored = False
        if use_session_cache:
            jar = load_session_cookies(username)
            if jar is not None:
                session.cookies.update(jar)
                session._ao3_logged_in = True  # type: ignore[attr-defined]
                restored = True
                _emit(on_status, "Using saved AO3 session")
        if login and not restored:
            login_to_ao3(session, username, password, on_status=on_status)
    elif username or password:
        raise LoginError("Both username and password are required to log in to AO3")
    return session


def verify_login(
    username: str | None,
    password: str | None,
    *,
    on_status: StatusCallback | None = None,
) -> str:
    """Attempt AO3 login and return the username on success.

    Unlike ``create_session``, empty credentials are an error (anonymous
    access is not treated as a successful login test).
    """
    user = (username or "").strip() or None
    pwd = password or None
    if pwd is not None and not str(pwd):
        pwd = None
    if not user or not pwd:
        raise LoginError("Both username and password are required to log in to AO3")
    session = create_session(
        user, pwd, on_status=on_status, use_session_cache=False
    )
    session.close()
    return user


def login_main(argv: list[str] | None = None) -> int:
    """CLI: ``python -m ao3kit login`` — verify AO3 credentials."""
    import argparse
    import os
    import sys

    parser = argparse.ArgumentParser(description="Test AO3 login credentials.")
    parser.add_argument("--username", help="AO3 username (or set AO3_USERNAME)")
    parser.add_argument("--password", help="AO3 password (or set AO3_PASSWORD)")
    args = parser.parse_args(argv)
    username = args.username or os.environ.get("AO3_USERNAME")
    password = args.password or os.environ.get("AO3_PASSWORD")
    try:
        user = verify_login(
            username,
            password,
            on_status=lambda msg: print(msg, file=sys.stderr),
        )
    except LoginError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except Ao3HttpError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(f"AO3 login succeeded for {user}")
    return 0
