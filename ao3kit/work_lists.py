"""Scrape AO3 work listing pages beyond ``/works`` search."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable
from urllib.parse import parse_qs, quote, unquote, urlencode, urlparse

import requests

from ao3kit.http import AO3_BASE
from ao3kit.rate import ensure_rate_limits
from ao3kit.scrape import (
    SearchCriteria,
    SearchPage,
    WorkRecord,
    build_search_url,
    criteria_from_work_search_params,
    fetch_page,
    parse_pagination_max,
    parse_search_page,
    work_matches_filters,
    work_search_params_from_criteria,
)

StatusCallback = Callable[[SearchPage, str], None]
WorkCallback = Callable[[WorkRecord], None]

COLLECTION_HOME_PATH_RE = re.compile(
    r"^/collections/(?P<name>[^/]+)/?$",
    re.IGNORECASE,
)
COLLECTION_WORKS_PATH_RE = re.compile(
    r"^/collections/(?P<name>[^/]+)/works/?$",
    re.IGNORECASE,
)
USER_WORKS_PATH_RE = re.compile(
    r"^/users/(?P<user>[^/]+)/works(?:/(?P<sub>collected>collected))?/?$",
    re.IGNORECASE,
)
USER_BOOKMARKS_PATH_RE = re.compile(
    r"^/users/(?P<user>[^/]+)/bookmarks/?$",
    re.IGNORECASE,
)


@dataclass
class WorkListTarget:
    """A paginated AO3 page whose main content is work blurbs."""

    kind: str
    list_path: str
    criteria: SearchCriteria
    start_page: int = 1
    passthrough_params: list[tuple[str, str]] | None = None

    def build_url(self, page: int | None = None) -> str:
        page = self.start_page if page is None else page
        if self.passthrough_params is not None:
            params = [
                (key, value)
                for key, value in self.passthrough_params
                if key != "page"
            ]
            if page > 1:
                params.append(("page", str(page)))
            query = urlencode(params, quote_via=quote)
            return f"{AO3_BASE}{self.list_path}?{query}" if query else f"{AO3_BASE}{self.list_path}"

        if self.kind == "search" and self.list_path in ("/works", "/works/search"):
            return build_search_url(self.criteria, page=page)

        params = work_search_params_from_criteria(self.criteria)
        if page > 1:
            params.append(("page", str(page)))
        query = urlencode(params, quote_via=quote)
        return f"{AO3_BASE}{self.list_path}?{query}" if query else f"{AO3_BASE}{self.list_path}"


def normalize_list_path(path: str) -> tuple[str, str | None]:
    """Return ``(list_path, collection_name)`` for supported listing paths."""
    path = unquote(path).rstrip("/") or "/"
    match = COLLECTION_HOME_PATH_RE.match(path)
    if match:
        name = match.group("name")
        return f"/collections/{name}/works", name
    match = COLLECTION_WORKS_PATH_RE.match(path)
    if match:
        return path, match.group("name")
    match = USER_WORKS_PATH_RE.match(path)
    if match:
        user = match.group("user")
        if match.groupdict().get("sub") == "collected":
            return f"/users/{user}/works/collected", user
        return f"/users/{user}/works", user
    match = USER_BOOKMARKS_PATH_RE.match(path)
    if match:
        return f"/users/{match.group('user')}/bookmarks", match.group("user")
    return path, None


def _passthrough_params(params: dict[str, list[str]]) -> list[tuple[str, str]]:
    ordered: list[tuple[str, str]] = []
    for key, values in params.items():
        for value in values:
            ordered.append((key, value))
    return ordered


def _page_from_params(params: dict[str, list[str]]) -> int:
    raw = (params.get("page") or [""])[0]
    if not raw:
        return 1
    return int(str(raw).replace(",", ""))


def parse_work_list_url(url: str) -> WorkListTarget:
    """Parse an AO3 work listing URL that is not a series page."""
    parsed = urlparse(url)
    if parsed.netloc and "archiveofourown.org" not in parsed.netloc:
        raise ValueError(f"Not an AO3 URL: {url}")

    list_path, _owner = normalize_list_path(parsed.path)
    params = parse_qs(parsed.query, keep_blank_values=True)
    start_page = _page_from_params(params)

    if list_path.startswith("/collections/") and list_path.endswith("/works"):
        criteria = criteria_from_work_search_params(params)
        return WorkListTarget(
            kind="collection",
            list_path=list_path,
            criteria=criteria,
            start_page=start_page,
        )

    if list_path.startswith("/users/") and list_path.endswith("/works"):
        criteria = criteria_from_work_search_params(params)
        return WorkListTarget(
            kind="user_works",
            list_path=list_path,
            criteria=criteria,
            start_page=start_page,
        )

    if list_path.endswith("/works/collected"):
        criteria = criteria_from_work_search_params(params)
        return WorkListTarget(
            kind="user_works_collected",
            list_path=list_path,
            criteria=criteria,
            start_page=start_page,
        )

    if list_path.endswith("/bookmarks"):
        return WorkListTarget(
            kind="bookmarks",
            list_path=list_path,
            criteria=SearchCriteria(language_id=None),
            start_page=start_page,
            passthrough_params=_passthrough_params(params),
        )

    raise ValueError(
        "Expected an AO3 work listing URL (/works, /tags/.../works, "
        "/collections/..., /users/.../works, or /users/.../bookmarks), "
        f"got path {parsed.path!r}"
    )


def work_list_payload(target: WorkListTarget) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "kind": target.kind,
        "list_path": target.list_path,
        "start_page": target.start_page,
        "search_url": target.build_url(page=target.start_page),
    }
    if target.kind == "collection":
        payload["collection"] = target.list_path.removeprefix("/collections/").removesuffix(
            "/works"
        )
    elif target.kind.startswith("user_") or target.kind == "bookmarks":
        parts = [part for part in target.list_path.split("/") if part]
        if len(parts) >= 2 and parts[0] == "users":
            payload["user"] = parts[1]
    if target.passthrough_params is None:
        payload["criteria"] = target.criteria.to_dict()
    return payload


def scrape_work_list(
    target: WorkListTarget,
    *,
    max_results: int | None = None,
    min_score: float | None = None,
    min_kudos: int | None = None,
    min_words: int | None = None,
    complete_only: bool = False,
    start_page: int | None = None,
    score_config=None,
    session: requests.Session | None = None,
    on_page: StatusCallback | None = None,
    on_work: WorkCallback | None = None,
) -> list[WorkRecord]:
    from ao3kit.http import create_session

    session = session or create_session()
    ensure_rate_limits()
    matched: list[WorkRecord] = []
    page = max(1, int(start_page or target.start_page or 1))
    max_page: int | None = None

    while True:
        url = target.build_url(page=page)
        html = fetch_page(url, session=session)
        search_page = parse_search_page(html)
        if max_page is None:
            max_page = parse_pagination_max(html)
        if on_page:
            on_page(search_page, url)

        for work in search_page.works:
            if not work_matches_filters(
                work,
                min_score=min_score,
                min_kudos=min_kudos,
                min_words=min_words,
                complete_only=complete_only,
                score_config=score_config,
            ):
                continue
            matched.append(work)
            if on_work:
                on_work(work)
            if max_results is not None and len(matched) >= max_results:
                return matched

        if not search_page.works:
            break
        if (
            search_page.page_end is not None
            and search_page.total_results is not None
            and search_page.page_end >= search_page.total_results
        ):
            break
        if max_page is not None and page >= max_page:
            break
        if max_page is None and len(search_page.works) < 20:
            break
        page += 1
        if max_page is not None and page > max_page:
            break

    return matched
