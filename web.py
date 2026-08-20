#!/usr/bin/env python3
"""Simple FastAPI web UI for the AO3 scraper."""

from __future__ import annotations

import json
import os
import queue
import threading
from pathlib import Path
from typing import Any, Iterator

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.templating import Jinja2Templates

from scrape_ao3 import (
    QualityScoreConfig,
    SearchCriteria,
    WorkRecord,
    build_search_url,
    create_session,
    parse_search_url,
    scrape_search,
)

app = FastAPI(title="AO3 Scraper")
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

SORT_OPTIONS = [
    ("kudos_count", "Kudos"),
    ("hits", "Hits"),
    ("comments_count", "Comments"),
    ("bookmarks_count", "Bookmarks"),
    ("word_count", "Word count"),
    ("date_to_sort_on", "Date updated"),
    ("created_at", "Date posted"),
    ("title_to_sort_on", "Title"),
]

DEFAULT_FORM = {
    "url": "",
    "tag_id": "",
    "sort_column": "kudos_count",
    "complete": "",
    "language_id": "en",
    "words_from": "",
    "words_to": "",
    "date_from": "",
    "date_to": "",
    "query": "",
    "relationship_ids": "",
    "freeform_ids": "",
    "character_ids": "",
    "max_results": "",
    "start_page": "1",
    "min_score": "",
    "min_kudos": "",
    "min_words": "",
    "delay": "1.0",
    "complete_only": False,
    "no_normalize": False,
    "max_raw_score": "22",
    "min_kudos_for_score": "50",
    "username": "",
}


def parse_id_list(value: str | None) -> list[int]:
    if not value or not value.strip():
        return []
    ids: list[int] = []
    for part in value.replace(" ", "").split(","):
        if part.isdigit():
            ids.append(int(part))
    return ids


def optional_int(value: str | None) -> int | None:
    if value is None or str(value).strip() == "":
        return None
    return int(value)


def optional_float(value: str | None) -> float | None:
    if value is None or str(value).strip() == "":
        return None
    return float(value)


def score_class(score: float | None, *, use_normalization: bool) -> str | None:
    if score is None:
        return None
    low = 40 if use_normalization else 8
    high = 60 if use_normalization else 14
    if score >= high:
        return "green"
    if score >= low:
        return "yellow"
    return "red"


def fmt_int(value: int | None) -> str:
    if value is None:
        return "—"
    return f"{value:,}"


# Form fields populated from an AO3 search URL.
URL_CRITERIA_FIELDS = (
    "tag_id",
    "sort_column",
    "complete",
    "language_id",
    "words_from",
    "words_to",
    "date_from",
    "date_to",
    "query",
    "relationship_ids",
    "freeform_ids",
    "character_ids",
    "start_page",
)


def criteria_to_form(criteria: SearchCriteria, start_page: int) -> dict[str, str]:
    complete = ""
    if criteria.complete is True:
        complete = "true"
    elif criteria.complete is False:
        complete = "false"

    return {
        "tag_id": criteria.tag_id or "",
        "sort_column": criteria.sort_column,
        "complete": complete,
        "language_id": criteria.language_id or "",
        "words_from": str(criteria.words_from) if criteria.words_from is not None else "",
        "words_to": str(criteria.words_to) if criteria.words_to is not None else "",
        "date_from": criteria.date_from or "",
        "date_to": criteria.date_to or "",
        "query": criteria.query or "",
        "relationship_ids": ",".join(str(i) for i in criteria.relationship_ids),
        "freeform_ids": ",".join(str(i) for i in criteria.freeform_ids),
        "character_ids": ",".join(str(i) for i in criteria.character_ids),
        "start_page": str(start_page),
    }


def use_form_criteria(form: dict[str, Any]) -> bool:
    return str(form.get("use_form_criteria", "")).lower() in {"1", "true", "yes"}


def build_criteria(form: dict[str, str | bool]) -> tuple[SearchCriteria, int]:
    start_page = optional_int(str(form.get("start_page", "1"))) or 1
    url = str(form.get("url", "")).strip()

    if url and not use_form_criteria(form):
        criteria, url_start_page = parse_search_url(url)
        if start_page == 1:
            start_page = url_start_page
        return criteria, start_page

    complete_raw = str(form.get("complete", ""))
    complete: bool | None = None
    if complete_raw == "true":
        complete = True
    elif complete_raw == "false":
        complete = False

    criteria = SearchCriteria(
        tag_id=str(form.get("tag_id", "")).strip() or None,
        sort_column=str(form.get("sort_column", "kudos_count")),
        complete=complete,
        words_from=optional_int(str(form.get("words_from", ""))),
        words_to=optional_int(str(form.get("words_to", ""))),
        date_from=str(form.get("date_from", "")).strip() or None,
        date_to=str(form.get("date_to", "")).strip() or None,
        query=str(form.get("query", "")).strip() or None,
        language_id=str(form.get("language_id", "en")).strip() or None,
        relationship_ids=parse_id_list(str(form.get("relationship_ids", ""))),
        freeform_ids=parse_id_list(str(form.get("freeform_ids", ""))),
        character_ids=parse_id_list(str(form.get("character_ids", ""))),
    )
    return criteria, start_page


def form_from_post(**kwargs: Any) -> dict:
    form = {**DEFAULT_FORM, **kwargs}
    form["complete_only"] = bool(form.get("complete_only"))
    form["no_normalize"] = bool(form.get("no_normalize"))
    form["use_form_criteria"] = use_form_criteria(form)
    return form


def work_to_payload(
    work: WorkRecord,
    *,
    score_config: QualityScoreConfig,
    use_normalization: bool,
) -> dict[str, Any]:
    data = work.to_dict(score_config=score_config)
    meta = data["metadata"]
    chapters = meta.get("chapters") or {}
    quality_score = meta.get("quality_score")
    sc = score_class(quality_score, use_normalization=use_normalization)

    return {
        "row": {
            "url": data["url"],
            "title": data["title"],
            "author": data.get("author"),
            "quality_score": quality_score,
            "score_class": sc,
            "chapters_display": chapters.get("display"),
            "chapters_complete": chapters.get("is_complete"),
            "words_fmt": fmt_int(meta.get("words")),
            "kudos_fmt": fmt_int(meta.get("kudos")),
            "hits_fmt": fmt_int(meta.get("hits")),
        },
        "jsonl": json.dumps(data, ensure_ascii=False),
    }


def sse_message(event_type: str, payload: dict[str, Any]) -> str:
    body = json.dumps({"type": event_type, **payload}, ensure_ascii=False)
    return f"data: {body}\n\n"


def run_scrape_with_events(
    form: dict[str, Any],
    password: str,
    event_queue: queue.SimpleQueue[Any],
) -> None:
    try:
        criteria, start_page_num = build_criteria(form)
        if use_form_criteria(form):
            has_criteria = bool(criteria.tag_id or criteria.query)
        else:
            has_criteria = bool(str(form.get("url", "")).strip()) or bool(
                criteria.tag_id or criteria.query
            )
        if not has_criteria:
            raise ValueError(
                "Provide an AO3 search URL or at least a fandom tag / query."
            )

        use_normalization = not form["no_normalize"]
        score_config = QualityScoreConfig(
            use_normalization=use_normalization,
            user_max_score=float(form["max_raw_score"]),
            min_kudos_to_score=int(form["min_kudos_for_score"]),
        )

        auth_user = str(form.get("username", "")).strip() or os.environ.get(
            "AO3_USERNAME"
        )
        auth_pass = password or os.environ.get("AO3_PASSWORD") or ""
        if (auth_user and not auth_pass) or (auth_pass and not auth_user):
            raise ValueError("Both username and password are required to log in.")

        event_queue.put(
            (
                "start",
                {
                    "search_url": build_search_url(criteria, page=start_page_num),
                    "message": "Starting scrape…",
                },
            )
        )

        if auth_user:
            event_queue.put(("status", {"message": "Logging in to AO3…"}))
        session = create_session(auth_user, auth_pass)
        if auth_user:
            event_queue.put(("status", {"message": "Logged in. Fetching pages…"}))

        page_num = 0
        matched = 0

        def on_page(search_page, page_url: str) -> None:
            nonlocal page_num
            page_num += 1
            event_queue.put(
                (
                    "page",
                    {
                        "page": page_num,
                        "url": page_url,
                        "works_on_page": len(search_page.works),
                        "total_results": search_page.total_results,
                        "matched_so_far": matched,
                    },
                )
            )

        def on_work(work: WorkRecord) -> None:
            nonlocal matched
            matched += 1
            event_queue.put(
                (
                    "work",
                    work_to_payload(
                        work,
                        score_config=score_config,
                        use_normalization=use_normalization,
                    ),
                )
            )

        scrape_search(
            criteria,
            max_results=optional_int(str(form.get("max_results", ""))),
            min_score=optional_float(str(form.get("min_score", ""))),
            min_kudos=optional_int(str(form.get("min_kudos", ""))),
            min_words=optional_int(str(form.get("min_words", ""))),
            complete_only=bool(form.get("complete_only")),
            request_delay=float(form.get("delay") or 1.0),
            start_page=start_page_num,
            score_config=score_config,
            session=session,
            on_page=on_page,
            on_work=on_work,
        )

        event_queue.put(("done", {"count": matched}))
    except Exception as exc:
        event_queue.put(("error", {"message": str(exc)}))
    finally:
        event_queue.put(None)


def stream_scrape_events(
    form: dict[str, Any], password: str
) -> Iterator[str]:
    event_queue: queue.SimpleQueue[Any] = queue.SimpleQueue()
    thread = threading.Thread(
        target=run_scrape_with_events,
        args=(form, password, event_queue),
        daemon=True,
    )
    thread.start()

    while True:
        item = event_queue.get()
        if item is None:
            break
        event_type, payload = item
        yield sse_message(event_type, payload)


@app.get("/", response_class=HTMLResponse)
async def index(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "request": request,
            "form": DEFAULT_FORM,
            "sort_options": SORT_OPTIONS,
        },
    )


@app.post("/parse-url")
async def parse_url(url: str = Form("")) -> JSONResponse:
    url = url.strip()
    if not url:
        return JSONResponse({"error": "URL is required"}, status_code=400)
    try:
        criteria, start_page = parse_search_url(url)
        return JSONResponse(criteria_to_form(criteria, start_page))
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)


@app.post("/scrape/stream")
async def scrape_stream(
    url: str = Form(""),
    use_form_criteria: str = Form("0"),
    tag_id: str = Form(""),
    sort_column: str = Form("kudos_count"),
    complete: str = Form(""),
    language_id: str = Form("en"),
    words_from: str = Form(""),
    words_to: str = Form(""),
    date_from: str = Form(""),
    date_to: str = Form(""),
    query: str = Form(""),
    relationship_ids: str = Form(""),
    freeform_ids: str = Form(""),
    character_ids: str = Form(""),
    max_results: str = Form(""),
    start_page: str = Form("1"),
    min_score: str = Form(""),
    min_kudos: str = Form(""),
    min_words: str = Form(""),
    delay: str = Form("1.0"),
    complete_only: str | None = Form(None),
    no_normalize: str | None = Form(None),
    max_raw_score: str = Form("22"),
    min_kudos_for_score: str = Form("50"),
    username: str = Form(""),
    password: str = Form(""),
) -> StreamingResponse:
    form = form_from_post(
        url=url,
        use_form_criteria=use_form_criteria,
        tag_id=tag_id,
        sort_column=sort_column,
        complete=complete,
        language_id=language_id,
        words_from=words_from,
        words_to=words_to,
        date_from=date_from,
        date_to=date_to,
        query=query,
        relationship_ids=relationship_ids,
        freeform_ids=freeform_ids,
        character_ids=character_ids,
        max_results=max_results,
        start_page=start_page,
        min_score=min_score,
        min_kudos=min_kudos,
        min_words=min_words,
        delay=delay,
        complete_only=complete_only,
        no_normalize=no_normalize,
        max_raw_score=max_raw_score,
        min_kudos_for_score=min_kudos_for_score,
        username=username,
    )

    return StreamingResponse(
        stream_scrape_events(form, password),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
