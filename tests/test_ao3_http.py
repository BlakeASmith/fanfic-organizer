"""Tests for shared AO3 HTTP helpers."""

from __future__ import annotations

import pytest
import requests

from ao3_http import (
    CloudflareError,
    LoginError,
    Ao3HttpError,
    DEFAULT_HEADERS,
    create_session,
    ensure_logged_in,
    get,
    get_text,
    is_cloudflare_response,
    is_login_wall,
    login_main,
    login_to_ao3,
    normalize_ao3_url,
    parse_retry_after,
    request,
    verify_login,
    with_view_adult,
)
from ao3kit.rate import USER_AGENT


class FakeResponse:
    def __init__(
        self,
        *,
        text: str = "",
        status_code: int = 200,
        headers: dict | None = None,
    ):
        self.text = text
        self.status_code = status_code
        self.headers = headers or {"Content-Type": "text/html"}

    def close(self) -> None:
        return

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            error = requests.HTTPError(f"{self.status_code}")
            error.response = self
            raise error


class FakeSession:
    def __init__(self, responses: list[FakeResponse]) -> None:
        self._responses = list(responses)
        self.calls: list[tuple[str, str]] = []
        self.timeouts: list[float | None] = []
        self.headers: dict = {}

    def request(self, method, url, data=None, timeout=None, stream=False):
        self.calls.append((method, url))
        self.timeouts.append(timeout)
        if not self._responses:
            raise AssertionError(f"unexpected {method} {url}")
        return self._responses.pop(0)


def test_default_headers_use_shared_user_agent():
    assert DEFAULT_HEADERS["User-Agent"] == USER_AGENT


def test_parse_retry_after_seconds_and_missing():
    assert parse_retry_after("7") == (7.0, True)
    assert parse_retry_after("") == (60.0, False)
    assert parse_retry_after("not-a-date") == (60.0, False)


def test_normalize_ao3_url_upgrades_http():
    assert (
        normalize_ao3_url("http://archiveofourown.org/works/1")
        == "https://archiveofourown.org/works/1"
    )


def test_with_view_adult_merges_query():
    assert with_view_adult("https://archiveofourown.org/works/1") == (
        "https://archiveofourown.org/works/1?view_adult=true"
    )
    assert with_view_adult(
        "https://archiveofourown.org/works/1?view_full_work=true"
    ) == ("https://archiveofourown.org/works/1?view_full_work=true&view_adult=true")


def test_is_cloudflare_response_detects_challenge():
    response = FakeResponse(text="<html><title> Just a moment... </title></html>")
    assert is_cloudflare_response(response) is True

    ok = FakeResponse(text="<html><title>Work Title | Archive of Our Own</title></html>")
    assert is_cloudflare_response(ok) is False


def test_request_retries_cloudflare_then_succeeds(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("ao3_http.time.sleep", lambda _s: None)
    session = FakeSession(
        [
            FakeResponse(text='<html> Just a moment... </html>'),
            FakeResponse(text="<html>ok</html>"),
        ]
    )
    response = request(session, "GET", "https://archiveofourown.org/works/1")
    assert response.text == "<html>ok</html>"
    assert len(session.calls) == 2


def test_request_raises_after_cloudflare_retries(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("ao3_http.time.sleep", lambda _s: None)
    session = FakeSession(
        [FakeResponse(text='id="cf-wrapper" challenge')] * 3
    )
    with pytest.raises(CloudflareError):
        request(
            session,
            "GET",
            "https://archiveofourown.org/works/1",
            max_retries=2,
        )


def test_request_honors_retry_after_on_429(monkeypatch: pytest.MonkeyPatch):
    sleeps: list[float] = []
    monkeypatch.setattr("ao3_http.time.sleep", lambda s: sleeps.append(s))
    session = FakeSession(
        [
            FakeResponse(status_code=429, headers={"Retry-After": "7", "Content-Type": "text/html"}),
            FakeResponse(text="ok"),
        ]
    )
    response = request(session, "GET", "https://archiveofourown.org/works/1")
    assert response.text == "ok"
    assert sleeps == [7]
    from ao3kit.rate import rate_report

    outcomes = [event["outcome"] for event in rate_report(recent=10)["recent"]]
    assert outcomes == ["ok", "429"]
    retry = rate_report()["stats"]["retry_after"]
    assert retry["with_header"] == 1
    assert retry["values"][0]["seconds"] == 7.0


def test_tag_429_without_header_uses_short_pause(monkeypatch: pytest.MonkeyPatch):
    sleeps: list[float] = []
    monkeypatch.setattr("ao3_http.time.sleep", lambda s: sleeps.append(s))
    session = FakeSession(
        [
            FakeResponse(status_code=429, headers={"Content-Type": "text/html"}),
            FakeResponse(text="ok"),
        ]
    )
    response = request(session, "GET", "https://archiveofourown.org/tags/Fluff")
    assert response.text == "ok"
    from ao3kit.rate import DEFAULT_MIN_INTERVAL, TAG_DEFAULT_RETRY_AFTER, _STATE

    assert sleeps == [TAG_DEFAULT_RETRY_AFTER]
    assert _STATE.base_interval == pytest.approx(DEFAULT_MIN_INTERVAL)
    assert _STATE.tag_interval >= 2.0


def test_tag_429_with_retry_after_pauses_whole_host(
    monkeypatch: pytest.MonkeyPatch,
):
    sleeps: list[float] = []
    monkeypatch.setattr("ao3_http.time.sleep", lambda s: sleeps.append(s))
    session = FakeSession(
        [
            FakeResponse(
                status_code=429,
                headers={"Retry-After": "201", "Content-Type": "text/html"},
            ),
            FakeResponse(text="ok"),
        ]
    )
    import time

    from ao3kit.rate import DEFAULT_MIN_INTERVAL, _STATE

    before = time.time()
    response = request(session, "GET", "https://archiveofourown.org/tags/Humor")
    assert response.text == "ok"
    assert sleeps == [201]
    snap = _STATE.store.read()
    assert snap.next_allowed_at >= before + 200
    assert snap.base_interval == pytest.approx(DEFAULT_MIN_INTERVAL)


def test_request_raises_after_too_many_429s(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("ao3_http.time.sleep", lambda _s: None)
    session = FakeSession(
        [
            FakeResponse(
                status_code=429,
                headers={"Retry-After": "1", "Content-Type": "text/html"},
            )
        ]
        * 4
    )
    with pytest.raises(Ao3HttpError, match="rate-limited"):
        request(
            session,
            "GET",
            "https://archiveofourown.org/works/1",
            max_retries=2,
        )


def test_request_adds_view_adult(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("ao3_http.time.sleep", lambda _s: None)
    session = FakeSession([FakeResponse(text="ok")])
    request(
        session,
        "GET",
        "https://archiveofourown.org/works/9",
        view_adult=True,
    )
    assert session.calls == [
        ("GET", "https://archiveofourown.org/works/9?view_adult=true")
    ]


def test_create_session_requires_both_credentials():
    with pytest.raises(LoginError):
        create_session("only-user", None)


def test_create_session_logs_in_by_default(monkeypatch: pytest.MonkeyPatch):
    calls: list[str] = []

    def fake_login(session, username, password, on_status=None):
        calls.append(username)
        session._ao3_logged_in = True

    monkeypatch.setattr("ao3kit.http.login_to_ao3", fake_login)
    session = create_session("emily", "secret")
    assert calls == ["emily"]
    assert session._ao3_logged_in is True
    session.close()


def test_create_session_can_defer_login(monkeypatch: pytest.MonkeyPatch):
    def boom(*_a, **_k):
        raise AssertionError("login should not run")

    monkeypatch.setattr("ao3kit.http.login_to_ao3", boom)
    session = create_session("emily", "secret", login=False)
    assert session._ao3_logged_in is False
    assert session._ao3_username == "emily"
    session.close()


def test_create_session_reuses_cached_cookies(monkeypatch: pytest.MonkeyPatch):
    from requests.cookies import RequestsCookieJar

    jar = RequestsCookieJar()
    jar.set("user_credentials", "tok", domain="archiveofourown.org", path="/")

    def boom(*_a, **_k):
        raise AssertionError("login should not run")

    monkeypatch.setattr("ao3kit.http.load_session_cookies", lambda username: jar)
    monkeypatch.setattr("ao3kit.http.login_to_ao3", boom)
    messages: list[str] = []
    session = create_session("emily", "secret", on_status=messages.append)
    assert session._ao3_logged_in is True
    assert session.cookies["user_credentials"] == "tok"
    assert "Using saved AO3 session" in messages
    session.close()


def test_verify_login_skips_session_cache(monkeypatch: pytest.MonkeyPatch):
    def boom(_username: str):
        raise AssertionError("cache should not be read for verify_login")

    def fake_login(session, username, password, on_status=None):
        session._ao3_logged_in = True

    monkeypatch.setattr("ao3kit.http.load_session_cookies", boom)
    monkeypatch.setattr("ao3kit.http.login_to_ao3", fake_login)
    assert verify_login("emily", "secret") == "emily"


def test_get_relogs_when_saved_session_expired(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("ao3_http.time.sleep", lambda _s: None)
    session = FakeSession(
        [
            FakeResponse(text='<html><body class="logged-out">anon</body></html>'),
            FakeResponse(text='<html><body class="logged-in">ok</body></html>'),
        ]
    )
    session._ao3_username = "emily"
    session._ao3_password = "secret"
    session._ao3_logged_in = True
    logins: list[str] = []

    def fake_login(sess, username, password, on_status=None):
        logins.append(username)
        sess._ao3_logged_in = True

    monkeypatch.setattr("ao3kit.http.login_to_ao3", fake_login)
    monkeypatch.setattr("ao3kit.http.clear_session_cache", lambda: None)
    response = get(session, "https://archiveofourown.org/works?commit=Search")
    assert "ok" in response.text
    assert logins == ["emily"]
    assert len(session.calls) == 2


def test_ensure_logged_in_uses_stored_credentials(monkeypatch: pytest.MonkeyPatch):
    calls: list[tuple[str, str]] = []

    def fake_login(session, username, password, on_status=None):
        calls.append((username, password))
        session._ao3_logged_in = True

    monkeypatch.setattr("ao3kit.http.login_to_ao3", fake_login)
    session = create_session("emily", "secret", login=False)
    assert ensure_logged_in(session) is True
    assert calls == [("emily", "secret")]
    assert ensure_logged_in(session) is True
    assert calls == [("emily", "secret")]
    session.close()


def test_ensure_logged_in_without_credentials_is_false():
    session = create_session()
    assert ensure_logged_in(session) is False
    session.close()


def test_is_login_wall_detects_registered_users_page():
    html = """
    <html><body>
    <div id="main" class="sessions-new">
      This work is only available to registered users of the Archive.
    </div>
    </body></html>
    """
    assert is_login_wall(html) is True
    assert is_login_wall("<html><body><div id='main' class='tag home'>ok</div></body></html>") is False


def test_get_text_logs_in_only_on_login_wall(monkeypatch: pytest.MonkeyPatch):
    wall = """
    <html><body>
    <div id="main" class="sessions-new">
      This work is only available to registered users of the Archive.
    </div>
    </body></html>
    """
    session = FakeSession(
        [
            FakeResponse(text=wall),
            FakeResponse(text="<html>tag profile</html>"),
        ]
    )
    session._ao3_username = "emily"
    session._ao3_password = "secret"
    session._ao3_logged_in = False

    def fake_login(sess, username, password, on_status=None):
        sess._ao3_logged_in = True

    monkeypatch.setattr("ao3kit.http.login_to_ao3", fake_login)
    monkeypatch.setattr("ao3kit.http.time.sleep", lambda _s: None)

    html = get_text(
        session,
        "https://archiveofourown.org/tags/Kissing",
        login_if_needed=True,
    )
    assert html == "<html>tag profile</html>"
    assert len(session.calls) == 2


def test_get_text_skips_login_when_page_is_public(monkeypatch: pytest.MonkeyPatch):
    def boom(*_a, **_k):
        raise AssertionError("login should not run")

    monkeypatch.setattr("ao3kit.http.login_to_ao3", boom)
    monkeypatch.setattr("ao3kit.http.time.sleep", lambda _s: None)
    session = FakeSession([FakeResponse(text="<html>public tag</html>")])
    session._ao3_username = "emily"
    session._ao3_password = "secret"
    session._ao3_logged_in = False
    html = get_text(
        session,
        "https://archiveofourown.org/tags/Kissing",
        login_if_needed=True,
    )
    assert html == "<html>public tag</html>"
    assert session.calls == [
        ("GET", "https://archiveofourown.org/tags/Kissing")
    ]


_LOGIN_FORM = (
    '<html><body><form id="new_user">'
    '<input name="authenticity_token" value="tok" />'
    "</form></body></html>"
)


def test_login_to_ao3_success(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("ao3_http.time.sleep", lambda _s: None)
    session = FakeSession(
        [
            FakeResponse(text=_LOGIN_FORM),
            FakeResponse(text='<html><body class="logged-in"></body></html>'),
        ]
    )
    login_to_ao3(session, "emily", "secret")
    assert session.calls[0][0] == "GET"
    assert session.calls[1] == ("POST", "https://archiveofourown.org/users/login")
    assert session._ao3_logged_in is True
    from ao3_http import LOGIN_REQUEST_TIMEOUT

    assert session.timeouts == [LOGIN_REQUEST_TIMEOUT, LOGIN_REQUEST_TIMEOUT]


def test_login_to_ao3_rejects_bad_password(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("ao3_http.time.sleep", lambda _s: None)
    session = FakeSession(
        [
            FakeResponse(text=_LOGIN_FORM),
            FakeResponse(text='<html><body class="logged-out"></body></html>'),
        ]
    )
    with pytest.raises(LoginError, match="invalid username or password"):
        login_to_ao3(session, "emily", "wrong")


def test_verify_login_requires_both_credentials():
    with pytest.raises(LoginError):
        verify_login("", "")
    with pytest.raises(LoginError):
        verify_login("emily", "")
    with pytest.raises(LoginError):
        verify_login("", "secret")


def test_verify_login_returns_username(monkeypatch: pytest.MonkeyPatch):
    class Session:
        closed = False

        def close(self) -> None:
            self.closed = True

    session = Session()
    monkeypatch.setattr("ao3_http.create_session", lambda *a, **k: session)
    assert verify_login("emily", "secret") == "emily"
    assert session.closed is True


def test_login_main_success(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]):
    monkeypatch.setattr("ao3_http.verify_login", lambda *a, **k: "emily")
    assert login_main(["--username", "emily", "--password", "x"]) == 0
    assert "emily" in capsys.readouterr().out


def test_login_main_fails(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]):
    def boom(*_a, **_k):
        raise LoginError("AO3 login failed: invalid username or password")

    monkeypatch.setattr("ao3_http.verify_login", boom)
    assert login_main(["--username", "emily", "--password", "bad"]) == 1
    assert "invalid" in capsys.readouterr().err
