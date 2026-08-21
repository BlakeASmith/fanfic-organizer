"""Tests for shared AO3 HTTP helpers."""

from __future__ import annotations

import pytest
import requests

from ao3_http import (
    CloudflareError,
    LoginError,
    Ao3HttpError,
    create_session,
    is_cloudflare_response,
    normalize_ao3_url,
    parse_retry_after,
    request,
    with_view_adult,
)


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
        self.headers: dict = {}

    def request(self, method, url, data=None, timeout=None, stream=False):
        self.calls.append((method, url))
        if not self._responses:
            raise AssertionError(f"unexpected {method} {url}")
        return self._responses.pop(0)


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
