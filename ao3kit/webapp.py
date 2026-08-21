#!/usr/bin/env python3
"""FastAPI web UI for the AO3 scraper (also mounts the REST API at ``/api/v1``)."""

from __future__ import annotations

import json
import os
import queue
import tempfile
import threading
import uuid
from pathlib import Path
from typing import Any, Iterator

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from fastapi.templating import Jinja2Templates

from ao3kit.epubs import (
    ZIP_NAME,
    download_records,
    parse_jsonl_text,
)
from ao3kit.scrape import (
    QualityScoreConfig,
    SearchCriteria,
    WorkRecord,
    build_search_url,
    create_session,
    parse_search_url,
    scrape_search,
)

# ao3kit.http loads `.env` on import.
_ENV_USERNAME = os.environ.get("AO3_USERNAME") or ""

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
app = FastAPI(title="AO3 Scraper")
templates = Jinja2Templates(directory=str(_PROJECT_ROOT / "templates"))

# JSON REST API (OpenAPI at /api/v1/docs).
from ao3kit.api import app as api_app  # noqa: E402

app.mount("/api/v1", api_app)

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
    "delay": "5.0",
    "complete_only": False,
    "username": _ENV_USERNAME,
}

FIELD_DEFAULTS = {
    "sort_column": "kudos_count",
    "language_id": "en",
    "start_page": "1",
    "complete": "",
}

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

SCRAPE_JOBS: dict[str, dict[str, Any]] = {}
SCRAPE_JOBS_LOCK = threading.Lock()
DOWNLOAD_JOBS: dict[str, dict[str, Any]] = {}
DOWNLOAD_JOBS_LOCK = threading.Lock()


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


def score_class(score: float | None) -> str | None:
    if score is None:
        return None
    if score >= 14:
        return "green"
    if score >= 8:
        return "yellow"
    return "red"


def fmt_int(value: int | None) -> str:
    if value is None:
        return "—"
    return f"{value:,}"


def render_partial(name: str, **context: Any) -> str:
    template = templates.env.get_template(name)
    return template.render(sort_options=SORT_OPTIONS, **context)


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


def form_from_post(**kwargs: Any) -> dict:
    form = {**DEFAULT_FORM, **kwargs}
    form["complete_only"] = bool(form.get("complete_only"))
    form["use_form_criteria"] = use_form_criteria(form)
    return form


def merge_url_into_form(form: dict[str, Any], parsed: dict[str, str]) -> dict[str, Any]:
    if use_form_criteria(form):
        return form

    merged = dict(form)
    for key in URL_CRITERIA_FIELDS:
        current = str(merged.get(key, "")).strip()
        parsed_value = parsed.get(key, "")
        if parsed_value == "":
            continue
        if not current or current == FIELD_DEFAULTS.get(key, ""):
            merged[key] = parsed_value
    return merged


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


def work_to_row(work: WorkRecord, *, score_config: QualityScoreConfig) -> dict[str, Any]:
    data = work.to_dict(score_config=score_config)
    meta = data["metadata"]
    chapters = meta.get("chapters") or {}
    quality_score = meta.get("quality_score")

    return {
        "url": data["url"],
        "title": data["title"],
        "author": data.get("author"),
        "quality_score": quality_score,
        "score_class": score_class(quality_score),
        "chapters_display": chapters.get("display"),
        "chapters_complete": chapters.get("is_complete"),
        "words_fmt": fmt_int(meta.get("words")),
        "kudos_fmt": fmt_int(meta.get("kudos")),
        "hits_fmt": fmt_int(meta.get("hits")),
        "jsonl": json.dumps(data, ensure_ascii=False),
    }


def sse_html(event: str, html: str) -> str:
    lines = html.splitlines() or [""]
    chunks = [f"event: {event}"]
    chunks.extend(f"data: {line}" for line in lines)
    chunks.append("")
    return "\n".join(chunks) + "\n"


def run_scrape_job(scrape_id: str, event_queue: queue.SimpleQueue[Any]) -> None:
    with SCRAPE_JOBS_LOCK:
        job = SCRAPE_JOBS.get(scrape_id)
    if not job:
        event_queue.put(
            (
                "error",
                render_partial(
                    "partials/status_oob.html",
                    message="Scrape job not found.",
                    kind="error",
                ),
            )
        )
        event_queue.put(None)
        return

    form = job["form"]
    password = job["password"]

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

        score_config = QualityScoreConfig()
        search_url = build_search_url(criteria, page=start_page_num)

        event_queue.put(
            (
                "start",
                render_partial(
                    "partials/scrape_start.html",
                    message="Starting scrape…",
                    search_url=search_url,
                ),
            )
        )

        auth_user = str(form.get("username", "")).strip() or os.environ.get(
            "AO3_USERNAME"
        )
        auth_pass = password or os.environ.get("AO3_PASSWORD") or ""
        if (auth_user and not auth_pass) or (auth_pass and not auth_user):
            raise ValueError("Both username and password are required to log in.")

        if auth_user:
            event_queue.put(
                (
                    "status",
                    render_partial(
                        "partials/status_oob.html",
                        message="Logging in to AO3…",
                    ),
                )
            )

        def on_http_status(message: str) -> None:
            event_queue.put(
                (
                    "status",
                    render_partial(
                        "partials/status_oob.html",
                        message=message,
                    ),
                )
            )

        session = create_session(auth_user, auth_pass, on_status=on_http_status)
        if auth_user:
            event_queue.put(
                (
                    "status",
                    render_partial(
                        "partials/status_oob.html",
                        message="Logged in. Fetching pages…",
                    ),
                )
            )

        page_num = 0
        matched = 0

        def on_page(search_page, page_url: str) -> None:
            nonlocal page_num
            page_num += 1
            total = (
                search_page.total_results
                if search_page.total_results is not None
                else "?"
            )
            event_queue.put(
                (
                    "status",
                    render_partial(
                        "partials/status_oob.html",
                        message=(
                            f"Page {page_num}: fetched {len(search_page.works)} works "
                            f"({total} total on AO3), {matched} matched so far…"
                        ),
                    ),
                )
            )

        def on_work(work: WorkRecord) -> None:
            nonlocal matched
            matched += 1
            row = work_to_row(work, score_config=score_config)
            event_queue.put(
                (
                    "work",
                    render_partial(
                        "partials/work_row.html",
                        row=row,
                        count=matched,
                        jsonl_line=row["jsonl"],
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
            request_delay=float(form.get("delay") or 5.0),
            start_page=start_page_num,
            score_config=score_config,
            session=session,
            on_page=on_page,
            on_work=on_work,
        )

        event_queue.put(
            (
                "done",
                render_partial(
                    "partials/status_oob.html",
                    message=f"Done — {matched} work(s) matched.",
                    kind="done",
                ),
            )
        )
    except Exception as exc:
        event_queue.put(
            (
                "error",
                render_partial(
                    "partials/status_oob.html",
                    message=str(exc),
                    kind="error",
                ),
            )
        )
    finally:
        event_queue.put(None)
        with SCRAPE_JOBS_LOCK:
            SCRAPE_JOBS.pop(scrape_id, None)


def run_download_job(download_id: str, event_queue: queue.SimpleQueue[Any]) -> None:
    with DOWNLOAD_JOBS_LOCK:
        job = DOWNLOAD_JOBS.get(download_id)
    if not job:
        event_queue.put(
            (
                "error",
                render_partial(
                    "partials/status_oob.html",
                    message="Download job not found.",
                    kind="error",
                ),
            )
        )
        event_queue.put(None)
        return

    try:
        records = parse_jsonl_text(job["jsonl"])
        if not records:
            raise ValueError("JSONL output is empty.")

        dest = Path(job["dest"])
        dest.mkdir(parents=True, exist_ok=True)
        zip_path = dest / ZIP_NAME

        event_queue.put(
            (
                "status",
                render_partial(
                    "partials/status_oob.html",
                    message=f"Downloading {len(records)} EPUB(s) from AO3…",
                ),
            )
        )

        auth_user = str(job.get("username") or "").strip() or os.environ.get(
            "AO3_USERNAME"
        )
        auth_pass = job.get("password") or os.environ.get("AO3_PASSWORD") or ""
        if (auth_user and not auth_pass) or (auth_pass and not auth_user):
            raise ValueError("Both username and password are required to log in.")

        def on_http_status(message: str) -> None:
            event_queue.put(
                (
                    "status",
                    render_partial("partials/status_oob.html", message=message),
                )
            )

        session = create_session(auth_user, auth_pass, on_status=on_http_status)

        def on_outcome(outcome, index: int, total: int) -> None:
            title = outcome.record.get("title") or outcome.record.get("work_id") or "?"
            extra = outcome.epub_file or outcome.error or ""
            event_queue.put(
                (
                    "status",
                    render_partial(
                        "partials/status_oob.html",
                        message=f"[{index}/{total}] {outcome.status} — {title} {extra}".rstrip(),
                    ),
                )
            )

        report = download_records(
            records,
            dest,
            session,
            request_delay=float(job.get("delay") or 5.0),
            skip_existing=True,
            make_zip=True,
            zip_path=zip_path,
            on_outcome=on_outcome,
        )
        with DOWNLOAD_JOBS_LOCK:
            stored = DOWNLOAD_JOBS.get(download_id)
            if stored is not None:
                stored["zip_path"] = str(zip_path)

        enriched = "\n".join(
            json.dumps(item.record, ensure_ascii=False) for item in report.outcomes
        )
        if enriched:
            enriched += "\n"

        event_queue.put(
            (
                "done",
                render_partial(
                    "partials/download_done.html",
                    download_id=download_id,
                    downloaded=report.downloaded,
                    skipped=report.skipped,
                    failed=report.failed,
                    jsonl=enriched,
                ),
            )
        )
    except Exception as exc:
        event_queue.put(
            (
                "error",
                render_partial(
                    "partials/status_oob.html",
                    message=str(exc),
                    kind="error",
                ),
            )
        )
    finally:
        event_queue.put(None)


def stream_download_events(download_id: str) -> Iterator[str]:
    with DOWNLOAD_JOBS_LOCK:
        if download_id not in DOWNLOAD_JOBS:
            raise HTTPException(status_code=404, detail="Download job not found")

    event_queue: queue.SimpleQueue[Any] = queue.SimpleQueue()
    thread = threading.Thread(
        target=run_download_job,
        args=(download_id, event_queue),
        daemon=True,
    )
    thread.start()

    while True:
        item = event_queue.get()
        if item is None:
            break
        event_type, html = item
        yield sse_html(event_type, html)


def stream_scrape_events(scrape_id: str) -> Iterator[str]:
    with SCRAPE_JOBS_LOCK:
        if scrape_id not in SCRAPE_JOBS:
            raise HTTPException(status_code=404, detail="Scrape job not found")

    event_queue: queue.SimpleQueue[Any] = queue.SimpleQueue()
    thread = threading.Thread(
        target=run_scrape_job,
        args=(scrape_id, event_queue),
        daemon=True,
    )
    thread.start()

    while True:
        item = event_queue.get()
        if item is None:
            break
        event_type, html = item
        yield sse_html(event_type, html)


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


@app.post("/parse-url/fill", response_class=HTMLResponse)
async def parse_url_fill(
    request: Request,
    url: str = Form(""),
    use_form_criteria_flag: str = Form("0"),
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
    start_page: str = Form("1"),
) -> HTMLResponse:
    form = form_from_post(
        url=url,
        use_form_criteria=use_form_criteria_flag,
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
        start_page=start_page,
    )

    url = str(form.get("url", "")).strip()
    if url and not use_form_criteria(form):
        try:
            criteria, page = parse_search_url(url)
            form = merge_url_into_form(form, criteria_to_form(criteria, page))
        except ValueError as exc:
            return HTMLResponse(
                render_partial(
                    "partials/search_criteria.html",
                    form=form,
                )
                + render_partial(
                    "partials/status_oob.html",
                    message=str(exc),
                    kind="error",
                )
            )

    return templates.TemplateResponse(
        request,
        "partials/search_criteria.html",
        {"form": form, "sort_options": SORT_OPTIONS},
    )


@app.post("/scrape/start", response_class=HTMLResponse)
async def scrape_start(
    request: Request,
    url: str = Form(""),
    use_form_criteria_flag: str = Form("0"),
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
    delay: str = Form("5.0"),
    complete_only: str | None = Form(None),
    username: str = Form(""),
    password: str = Form(""),
) -> HTMLResponse:
    form = form_from_post(
        url=url,
        use_form_criteria=use_form_criteria_flag,
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
        username=username,
    )

    scrape_id = uuid.uuid4().hex
    with SCRAPE_JOBS_LOCK:
        SCRAPE_JOBS[scrape_id] = {"form": form, "password": password}

    return templates.TemplateResponse(
        request,
        "partials/scrape_session.html",
        {"scrape_id": scrape_id},
    )


@app.get("/scrape/events/{scrape_id}")
async def scrape_events(scrape_id: str) -> StreamingResponse:
    return StreamingResponse(
        stream_scrape_events(scrape_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@app.post("/download/start", response_class=HTMLResponse)
async def download_start(
    request: Request,
    jsonl: str = Form(""),
    username: str = Form(""),
    password: str = Form(""),
    epub_delay: str = Form("5.0"),
) -> HTMLResponse:
    try:
        records = parse_jsonl_text(jsonl)
    except ValueError as exc:
        return HTMLResponse(
            render_partial(
                "partials/status_oob.html",
                message=str(exc),
                kind="error",
            )
        )
    if not records:
        return HTMLResponse(
            render_partial(
                "partials/status_oob.html",
                message="Run a scrape first — there is no JSONL to download.",
                kind="error",
            )
        )

    download_id = uuid.uuid4().hex
    dest = Path(tempfile.mkdtemp(prefix="ao3-import-"))
    with DOWNLOAD_JOBS_LOCK:
        DOWNLOAD_JOBS[download_id] = {
            "jsonl": jsonl,
            "username": username,
            "password": password,
            "delay": optional_float(epub_delay) or 5.0,
            "dest": str(dest),
            "zip_path": None,
        }

    return templates.TemplateResponse(
        request,
        "partials/download_session.html",
        {"download_id": download_id, "count": len(records)},
    )


@app.get("/download/events/{download_id}")
async def download_events(download_id: str) -> StreamingResponse:
    return StreamingResponse(
        stream_download_events(download_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/download/{download_id}/zip")
async def download_zip(download_id: str) -> FileResponse:
    with DOWNLOAD_JOBS_LOCK:
        job = DOWNLOAD_JOBS.get(download_id)
        zip_path = Path(job["zip_path"]) if job and job.get("zip_path") else None
    if zip_path is None or not zip_path.exists():
        raise HTTPException(status_code=404, detail="Import zip is not ready")
    return FileResponse(
        zip_path,
        media_type="application/zip",
        filename=ZIP_NAME,
    )


# ---------------------------------------------------------------------------
# User settings & rules
# ---------------------------------------------------------------------------


def _user_cfg():
    from ao3kit.config import load_user_config

    return load_user_config(ensure=True)


def _settings_context(
    request: Request,
    *,
    message: str | None = None,
    error: bool = False,
) -> dict[str, Any]:
    cfg = _user_cfg()
    return {
        "request": request,
        "cfg": cfg,
        "settings": cfg.settings,
        "rule_files": cfg.list_rule_files(),
        "active_path": cfg.active_rules_path().resolve(),
        "message": message,
        "error": error,
    }


@app.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request, "settings.html", _settings_context(request)
    )


@app.post("/settings/config", response_class=HTMLResponse)
async def settings_save_config(
    request: Request,
    request_delay: str = Form("5.0"),
    active_rules: str = Form("rules/default.py"),
    notes: str = Form(""),
    resolve_canonical: str | None = Form(None),
    drop_unmarked: str | None = Form(None),
    tag_cache_enabled: str | None = Form(None),
    follow_canonical: str | None = Form(None),
    tag_cache_ttl_days: str = Form("90"),
) -> HTMLResponse:
    cfg = _user_cfg()
    try:
        delay = float(request_delay)
    except ValueError:
        return templates.TemplateResponse(
            request,
            "partials/settings_flash.html",
            {"message": "Invalid request delay", "error": True},
        )
    try:
        ttl = float(tag_cache_ttl_days)
    except ValueError:
        return templates.TemplateResponse(
            request,
            "partials/settings_flash.html",
            {"message": "Invalid tag cache TTL", "error": True},
        )
    cfg.update_settings(
        request_delay=delay,
        active_rules=active_rules.strip() or cfg.settings.active_rules,
        notes=notes,
        resolve_canonical=resolve_canonical == "1",
        drop_unmarked=drop_unmarked == "1",
        tag_cache_enabled=tag_cache_enabled == "1",
        tag_cache_ttl_days=ttl,
        follow_canonical=follow_canonical == "1",
    )
    # Full page refresh keeps selects in sync when not using HTMX swap carefully.
    if request.headers.get("hx-request"):
        return templates.TemplateResponse(
            request,
            "partials/settings_flash.html",
            {"message": "Preferences saved.", "error": False},
        )
    return templates.TemplateResponse(
        request,
        "settings.html",
        _settings_context(request, message="Preferences saved."),
    )


@app.get("/settings/rules/{name}", response_class=HTMLResponse)
async def settings_rule_edit(request: Request, name: str) -> HTMLResponse:
    cfg = _user_cfg()
    try:
        source = cfg.read_rule(name)
        path = cfg.rule_path(name)
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return templates.TemplateResponse(
        request,
        "settings_rule_edit.html",
        {
            "request": request,
            "name": name,
            "path": str(path),
            "source": source,
            "is_active": path.resolve() == cfg.active_rules_path().resolve(),
            "message": None,
            "error": False,
        },
    )


@app.post("/settings/rules/{name}", response_class=HTMLResponse)
async def settings_rule_save(
    request: Request,
    name: str,
    source: str = Form(""),
    make_active: str | None = Form(None),
) -> HTMLResponse:
    cfg = _user_cfg()
    try:
        path = cfg.write_rule(name, source)
        if make_active == "1":
            cfg.set_active_rules(name)
        # Validate the module loads.
        from ao3kit.tags.rules import load_tag_rules

        load_tag_rules(path)
        message = "Rule saved."
        error = False
    except Exception as exc:  # noqa: BLE001 - show to user
        path = cfg.rule_path(name)
        message = f"Save failed: {exc}"
        error = True
        # Still keep editor content.
    return templates.TemplateResponse(
        request,
        "settings_rule_edit.html",
        {
            "request": request,
            "name": name,
            "path": str(path),
            "source": source,
            "is_active": path.resolve() == cfg.active_rules_path().resolve(),
            "message": message,
            "error": error,
        },
    )


@app.post("/settings/rules/new", response_class=HTMLResponse)
async def settings_rule_new(
    request: Request,
    name: str = Form(...),
) -> HTMLResponse:
    cfg = _user_cfg()
    try:
        cfg.create_rule(name)
    except (ValueError, FileExistsError) as exc:
        return templates.TemplateResponse(
            request,
            "settings.html",
            _settings_context(request, message=str(exc), error=True),
        )
    return templates.TemplateResponse(
        request,
        "settings.html",
        _settings_context(request, message=f"Created rules/{name}.py"),
    )


@app.post("/settings/rules/install-example", response_class=HTMLResponse)
async def settings_install_example(request: Request) -> HTMLResponse:
    from ao3kit.config import copy_example_rules

    cfg = _user_cfg()
    try:
        path = copy_example_rules(cfg, name="example")
        message = f"Installed {path.name}"
        error = False
    except FileExistsError as exc:
        message = str(exc)
        error = True
    return templates.TemplateResponse(
        request,
        "settings.html",
        _settings_context(request, message=message, error=error),
    )
