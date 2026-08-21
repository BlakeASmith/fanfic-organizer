"""JSON REST API for ao3kit.

Mounted at ``/api/v1`` by ``ao3kit.webapp`` (``python -m ao3kit serve``).
Interactive docs: ``/api/v1/docs``. Standalone: ``uvicorn ao3kit.api:app``.
"""

from __future__ import annotations

import os
import tempfile
import threading
import uuid
from dataclasses import asdict
from pathlib import Path
from typing import Any, Literal
from urllib.parse import unquote

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from ao3kit import __version__

# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

JobStatus = Literal["queued", "running", "done", "error"]


class AuthFields(BaseModel):
    username: str | None = None
    password: str | None = None


class ParseUrlRequest(BaseModel):
    url: str


class SearchCriteriaBody(BaseModel):
    tag_id: str | None = None
    sort_column: str = "kudos_count"
    complete: bool | None = None
    words_from: int | None = None
    words_to: int | None = None
    date_from: str | None = None
    date_to: str | None = None
    query: str | None = None
    language_id: str | None = "en"
    other_tag_names: str = ""
    excluded_tag_names: str = ""
    crossover: str = ""
    relationship_ids: list[int] = Field(default_factory=list)
    freeform_ids: list[int] = Field(default_factory=list)
    character_ids: list[int] = Field(default_factory=list)


class ScrapeRequest(AuthFields):
    url: str | None = None
    criteria: SearchCriteriaBody | None = None
    start_page: int = 1
    max_results: int | None = None
    min_score: float | None = None
    min_kudos: int | None = None
    min_words: int | None = None
    complete_only: bool = False
    delay: float | None = None


class ScrapeJobResponse(BaseModel):
    job_id: str
    status: JobStatus
    message: str = ""
    search_url: str | None = None
    works: list[dict[str, Any]] = Field(default_factory=list)
    error: str | None = None


class JobCreated(BaseModel):
    job_id: str


class TagSearchRequest(AuthFields):
    url: str | None = None
    name: str = ""
    fandoms: str = ""
    type: str = ""
    status: str = "canonical"
    sort_column: str = "name"
    sort_direction: str = "asc"
    page: int = 1


class ResolveRequest(AuthFields):
    tags: list[str] = Field(default_factory=list)
    drop_unmarked: bool | None = None
    delay: float | None = None
    use_cache: bool | None = None
    follow_canonical: bool | None = None
    ttl_days: float | None = None


class ApplyRequest(AuthFields):
    tags: list[str] = Field(default_factory=list)
    rules: str | None = None
    delay: float | None = None
    use_cache: bool | None = None
    follow_canonical: bool | None = None
    ttl_days: float | None = None


class EnrichRequest(AuthFields):
    records: list[dict[str, Any]] | None = None
    jsonl: str | None = None
    rules: str | None = None
    include_fandoms: bool = True
    delay: float | None = None
    use_cache: bool | None = None
    follow_canonical: bool | None = None
    ttl_days: float | None = None


class DownloadRequest(AuthFields):
    records: list[dict[str, Any]] | None = None
    jsonl: str | None = None
    delay: float | None = None


class DownloadJobResponse(BaseModel):
    job_id: str
    status: JobStatus
    message: str = ""
    downloaded: int = 0
    skipped: int = 0
    failed: int = 0
    records: list[dict[str, Any]] = Field(default_factory=list)
    error: str | None = None
    zip_ready: bool = False


class ConfigPatch(BaseModel):
    request_delay: float | None = None
    resolve_canonical: bool | None = None
    drop_unmarked: bool | None = None
    drop_errors: bool | None = None
    active_rules: str | None = None
    tag_cache_enabled: bool | None = None
    tag_cache_ttl_days: float | None = None
    follow_canonical: bool | None = None
    default_language_id: str | None = None
    notes: str | None = None


class RuleCreate(BaseModel):
    name: str
    source: str | None = None
    make_active: bool = False


class RuleWrite(BaseModel):
    source: str
    make_active: bool = False


# ---------------------------------------------------------------------------
# Job stores
# ---------------------------------------------------------------------------

_SCRAPE_JOBS: dict[str, dict[str, Any]] = {}
_SCRAPE_LOCK = threading.Lock()
_DOWNLOAD_JOBS: dict[str, dict[str, Any]] = {}
_DOWNLOAD_LOCK = threading.Lock()


def _resolve_credentials(
    username: str | None,
    password: str | None,
) -> tuple[str | None, str | None]:
    user = (username or "").strip() or os.environ.get("AO3_USERNAME") or None
    pwd = password or os.environ.get("AO3_PASSWORD") or None
    if pwd is not None and not str(pwd):
        pwd = None
    if (user and not pwd) or (pwd and not user):
        raise HTTPException(
            status_code=400,
            detail="Both username and password are required to log in.",
        )
    return user, pwd


def _user_cfg():
    from ao3kit.config import load_user_config

    return load_user_config(ensure=True)


def _default_delay(override: float | None) -> float:
    if override is not None:
        return float(override)
    return float(_user_cfg().settings.request_delay)


def _criteria_from_body(body: SearchCriteriaBody):
    from ao3kit.scrape import SearchCriteria

    return SearchCriteria(
        tag_id=body.tag_id,
        sort_column=body.sort_column,
        complete=body.complete,
        words_from=body.words_from,
        words_to=body.words_to,
        date_from=body.date_from,
        date_to=body.date_to,
        query=body.query,
        language_id=body.language_id,
        other_tag_names=body.other_tag_names,
        excluded_tag_names=body.excluded_tag_names,
        crossover=body.crossover,
        relationship_ids=list(body.relationship_ids),
        freeform_ids=list(body.freeform_ids),
        character_ids=list(body.character_ids),
    )


def _build_scrape_criteria(req: ScrapeRequest):
    from ao3kit.scrape import build_search_url, parse_search_url

    start_page = max(1, req.start_page)

    if req.criteria is not None:
        criteria = _criteria_from_body(req.criteria)
        if not (criteria.tag_id or criteria.query):
            raise HTTPException(
                status_code=400,
                detail="Provide criteria.tag_id or criteria.query (or a search url).",
            )
        return criteria, start_page, build_search_url(criteria, page=start_page)

    if req.url:
        try:
            criteria, url_page = parse_search_url(req.url)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if req.start_page == 1:
            start_page = url_page
        return criteria, start_page, build_search_url(criteria, page=start_page)

    raise HTTPException(
        status_code=400,
        detail="Provide a search url or criteria with tag_id/query.",
    )


def _run_scrape_job(job_id: str) -> None:
    from ao3kit.scrape import QualityScoreConfig, create_session, scrape_search

    with _SCRAPE_LOCK:
        job = _SCRAPE_JOBS.get(job_id)
        if not job:
            return
        req: ScrapeRequest = job["request"]
        job["status"] = "running"
        job["message"] = "Starting scrape…"

    try:
        criteria, start_page, search_url = _build_scrape_criteria(req)
        auth_user, auth_pass = _resolve_credentials(req.username, req.password)
        delay = _default_delay(req.delay)
        score_config = QualityScoreConfig()

        with _SCRAPE_LOCK:
            stored = _SCRAPE_JOBS.get(job_id)
            if stored is not None:
                stored["search_url"] = search_url
                stored["message"] = "Fetching pages…"

        def on_status(message: str) -> None:
            with _SCRAPE_LOCK:
                stored = _SCRAPE_JOBS.get(job_id)
                if stored is not None:
                    stored["message"] = message

        session = create_session(auth_user, auth_pass, on_status=on_status)

        def on_page(search_page, page_url: str) -> None:
            total = (
                search_page.total_results
                if search_page.total_results is not None
                else "?"
            )
            with _SCRAPE_LOCK:
                stored = _SCRAPE_JOBS.get(job_id)
                if stored is None:
                    return
                matched = len(stored.get("works") or [])
                stored["message"] = (
                    f"Fetched {len(search_page.works)} works "
                    f"({total} total on AO3), {matched} matched so far…"
                )

        def on_work(work) -> None:
            data = work.to_dict(score_config=score_config)
            with _SCRAPE_LOCK:
                stored = _SCRAPE_JOBS.get(job_id)
                if stored is not None:
                    stored.setdefault("works", []).append(data)

        scrape_search(
            criteria,
            max_results=req.max_results,
            min_score=req.min_score,
            min_kudos=req.min_kudos,
            min_words=req.min_words,
            complete_only=req.complete_only,
            request_delay=delay,
            start_page=start_page,
            score_config=score_config,
            session=session,
            on_page=on_page,
            on_work=on_work,
        )

        with _SCRAPE_LOCK:
            stored = _SCRAPE_JOBS.get(job_id)
            if stored is not None:
                n = len(stored.get("works") or [])
                stored["status"] = "done"
                stored["message"] = f"Done — {n} work(s) matched."
    except HTTPException as exc:
        with _SCRAPE_LOCK:
            stored = _SCRAPE_JOBS.get(job_id)
            if stored is not None:
                stored["status"] = "error"
                stored["error"] = str(exc.detail)
                stored["message"] = str(exc.detail)
    except Exception as exc:  # noqa: BLE001
        with _SCRAPE_LOCK:
            stored = _SCRAPE_JOBS.get(job_id)
            if stored is not None:
                stored["status"] = "error"
                stored["error"] = str(exc)
                stored["message"] = str(exc)


def _parse_download_records(req: DownloadRequest) -> list[dict[str, Any]]:
    from ao3kit.epubs import parse_jsonl_text

    if req.records is not None:
        if not req.records:
            raise HTTPException(status_code=400, detail="records list is empty.")
        return list(req.records)
    if req.jsonl is not None:
        try:
            records = parse_jsonl_text(req.jsonl)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if not records:
            raise HTTPException(status_code=400, detail="jsonl is empty.")
        return records
    raise HTTPException(
        status_code=400,
        detail="Provide records or jsonl.",
    )


def _run_download_job(job_id: str) -> None:
    from ao3kit.epubs import ZIP_NAME, download_records
    from ao3kit.scrape import create_session

    with _DOWNLOAD_LOCK:
        job = _DOWNLOAD_JOBS.get(job_id)
        if not job:
            return
        req: DownloadRequest = job["request"]
        records: list[dict[str, Any]] = job["records"]
        dest = Path(job["dest"])
        job["status"] = "running"
        job["message"] = f"Downloading {len(records)} EPUB(s) from AO3…"

    try:
        auth_user, auth_pass = _resolve_credentials(req.username, req.password)
        delay = _default_delay(req.delay)
        zip_path = dest / ZIP_NAME

        def on_status(message: str) -> None:
            with _DOWNLOAD_LOCK:
                stored = _DOWNLOAD_JOBS.get(job_id)
                if stored is not None:
                    stored["message"] = message

        session = create_session(auth_user, auth_pass, on_status=on_status)

        def on_outcome(outcome, index: int, total: int) -> None:
            title = outcome.record.get("title") or outcome.record.get("work_id") or "?"
            extra = outcome.epub_file or outcome.error or ""
            with _DOWNLOAD_LOCK:
                stored = _DOWNLOAD_JOBS.get(job_id)
                if stored is not None:
                    stored["message"] = (
                        f"[{index}/{total}] {outcome.status} — {title} {extra}".rstrip()
                    )

        report = download_records(
            records,
            dest,
            session,
            request_delay=delay,
            skip_existing=True,
            make_zip=True,
            zip_path=zip_path,
            on_outcome=on_outcome,
        )

        enriched = [item.record for item in report.outcomes]
        with _DOWNLOAD_LOCK:
            stored = _DOWNLOAD_JOBS.get(job_id)
            if stored is not None:
                stored["status"] = "done"
                stored["downloaded"] = report.downloaded
                stored["skipped"] = report.skipped
                stored["failed"] = report.failed
                stored["records"] = enriched
                stored["zip_path"] = str(zip_path)
                stored["message"] = (
                    f"Done — downloaded={report.downloaded} "
                    f"skipped={report.skipped} failed={report.failed}"
                )
    except HTTPException as exc:
        with _DOWNLOAD_LOCK:
            stored = _DOWNLOAD_JOBS.get(job_id)
            if stored is not None:
                stored["status"] = "error"
                stored["error"] = str(exc.detail)
                stored["message"] = str(exc.detail)
    except Exception as exc:  # noqa: BLE001
        with _DOWNLOAD_LOCK:
            stored = _DOWNLOAD_JOBS.get(job_id)
            if stored is not None:
                stored["status"] = "error"
                stored["error"] = str(exc)
                stored["message"] = str(exc)


def _make_resolver(
    *,
    username: str | None,
    password: str | None,
    delay: float | None,
    use_cache: bool | None,
    follow_canonical: bool | None,
    ttl_days: float | None = None,
):
    from ao3kit.tags.metadata import DEFAULT_TAG_CACHE_PATH, TagResolver

    cfg = _user_cfg()
    auth_user, auth_pass = _resolve_credentials(username, password)
    request_delay = _default_delay(delay)
    cache_on = (
        cfg.settings.tag_cache_enabled if use_cache is None else use_cache
    )
    follow = (
        cfg.settings.follow_canonical
        if follow_canonical is None
        else follow_canonical
    )
    ttl = (
        cfg.settings.tag_cache_ttl_days if ttl_days is None else float(ttl_days)
    )
    return TagResolver(
        username=auth_user,
        password=auth_pass,
        delay=request_delay,
        cache_path=DEFAULT_TAG_CACHE_PATH if cache_on else None,
        follow_canonical=follow,
        persist=cache_on,
        ttl_days=ttl,
    )


def _load_rules(rules_name: str | None):
    from ao3kit.tags.rules import load_tag_rules

    cfg = _user_cfg()
    if rules_name:
        path = cfg.rule_path(rules_name)
        if not path.is_file():
            raise HTTPException(status_code=404, detail=f"Rules not found: {rules_name}")
        return load_tag_rules(path)
    rules = cfg.load_active_rules()
    rules.resolve_canonical = cfg.settings.resolve_canonical
    rules.drop_unmarked = cfg.settings.drop_unmarked
    rules.drop_errors = cfg.settings.drop_errors
    return rules


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------


def create_api_app() -> FastAPI:
    """Build the versioned REST API application (mount under ``/api/v1``)."""

    api = FastAPI(
        title="ao3kit API",
        version=__version__,
        description="JSON REST API for AO3 scrape, tags, download, and config.",
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
    )

    @api.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    # -- Scrape -------------------------------------------------------------

    @api.post("/scrape/parse-url")
    def scrape_parse_url(body: ParseUrlRequest) -> dict[str, Any]:
        from ao3kit.scrape import build_search_url, parse_search_url

        try:
            criteria, start_page = parse_search_url(body.url)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {
            "criteria": asdict(criteria),
            "start_page": start_page,
            "search_url": build_search_url(criteria, page=start_page),
        }

    @api.post("/scrape", status_code=202, response_model=JobCreated)
    def scrape_start(body: ScrapeRequest) -> JobCreated:
        # Validate early so clients get 400 instead of a failed job.
        _build_scrape_criteria(body)
        _resolve_credentials(body.username, body.password)

        job_id = uuid.uuid4().hex
        with _SCRAPE_LOCK:
            _SCRAPE_JOBS[job_id] = {
                "request": body,
                "status": "queued",
                "message": "Queued",
                "search_url": None,
                "works": [],
                "error": None,
            }
        thread = threading.Thread(
            target=_run_scrape_job, args=(job_id,), daemon=True
        )
        thread.start()
        return JobCreated(job_id=job_id)

    @api.get("/scrape/{job_id}", response_model=ScrapeJobResponse)
    def scrape_status(job_id: str) -> ScrapeJobResponse:
        with _SCRAPE_LOCK:
            job = _SCRAPE_JOBS.get(job_id)
            if job is None:
                raise HTTPException(status_code=404, detail="Scrape job not found")
            return ScrapeJobResponse(
                job_id=job_id,
                status=job["status"],
                message=job.get("message") or "",
                search_url=job.get("search_url"),
                works=list(job.get("works") or []),
                error=job.get("error"),
            )

    # -- Tags ---------------------------------------------------------------

    @api.post("/tags/search")
    def tags_search(body: TagSearchRequest) -> dict[str, Any]:
        from ao3kit.tags.metadata import (
            TagSearchCriteria,
            fetch_tag_search,
            parse_tag_search_url,
        )

        auth_user, auth_pass = _resolve_credentials(body.username, body.password)
        try:
            if body.url:
                criteria, page = parse_tag_search_url(body.url)
            else:
                criteria = TagSearchCriteria(
                    name=body.name,
                    fandoms=body.fandoms,
                    type=body.type,
                    wrangling_status=body.status,
                    sort_column=body.sort_column,
                    sort_direction=body.sort_direction,
                )
                page = body.page
            page_data = fetch_tag_search(
                criteria,
                page=page,
                username=auth_user,
                password=auth_pass,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        return page_data.to_dict()

    @api.post("/tags/resolve")
    def tags_resolve(body: ResolveRequest) -> dict[str, Any]:
        if not body.tags:
            raise HTTPException(status_code=400, detail="Provide tags.")
        cfg = _user_cfg()
        drop = (
            cfg.settings.drop_unmarked
            if body.drop_unmarked is None
            else body.drop_unmarked
        )
        try:
            with _make_resolver(
                username=body.username,
                password=body.password,
                delay=body.delay,
                use_cache=body.use_cache,
                follow_canonical=body.follow_canonical,
                ttl_days=body.ttl_days,
            ) as resolver:
                result = resolver.simplify(list(body.tags), drop_unmarked=drop)
                return {
                    **result.to_dict(),
                    "cache_stats": resolver.stats.to_dict(),
                }
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @api.post("/tags/apply")
    def tags_apply(body: ApplyRequest) -> dict[str, Any]:
        from ao3kit.tags.rules import TagRulesEngine

        if not body.tags:
            raise HTTPException(status_code=400, detail="Provide tags.")
        rules = _load_rules(body.rules)
        try:
            with _make_resolver(
                username=body.username,
                password=body.password,
                delay=body.delay,
                use_cache=body.use_cache,
                follow_canonical=body.follow_canonical,
                ttl_days=body.ttl_days,
            ) as resolver:
                engine = TagRulesEngine(rules, resolver)
                result = engine.apply(list(body.tags))
                return {
                    **result.to_dict(),
                    "cache_stats": resolver.stats.to_dict(),
                }
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @api.post("/tags/enrich")
    def tags_enrich(body: EnrichRequest) -> dict[str, Any]:
        from ao3kit.epubs import parse_jsonl_text
        from ao3kit.tags.clean import enrich_records
        from ao3kit.tags.rules import load_tag_rules

        if body.records is not None:
            records = list(body.records)
        elif body.jsonl is not None:
            try:
                records = parse_jsonl_text(body.jsonl)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
        else:
            raise HTTPException(
                status_code=400, detail="Provide records or jsonl."
            )
        if not records:
            raise HTTPException(status_code=400, detail="No records to enrich.")

        cfg = _user_cfg()
        if body.rules:
            path = cfg.rule_path(body.rules)
            if not path.is_file():
                raise HTTPException(
                    status_code=404, detail=f"Rules not found: {body.rules}"
                )
            rules = load_tag_rules(path)
        else:
            rules = None

        try:
            with _make_resolver(
                username=body.username,
                password=body.password,
                delay=body.delay,
                use_cache=body.use_cache,
                follow_canonical=body.follow_canonical,
                ttl_days=body.ttl_days,
            ) as resolver:
                enriched = enrich_records(
                    records,
                    rules=rules,
                    resolver=resolver,
                    include_fandoms=body.include_fandoms,
                    delay=body.delay,
                )
                return {"records": enriched, "cache_stats": resolver.stats.to_dict()}
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @api.get("/tags/{name:path}")
    def tags_get(
        name: str,
        synonym_map: bool = Query(False),
        username: str | None = None,
        password: str | None = None,
    ) -> dict[str, Any]:
        from ao3kit.tags.metadata import fetch_tag_profile

        auth_user, auth_pass = _resolve_credentials(username, password)
        tag_name = unquote(name)
        try:
            profile = fetch_tag_profile(
                tag_name, username=auth_user, password=auth_pass
            )
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        if synonym_map:
            return profile.synonym_map()
        return profile.to_dict()

    # -- Tag sets -----------------------------------------------------------

    @api.get("/tag-sets")
    def tag_sets_search(
        q: str = Query(..., min_length=1),
        page: int = Query(1, ge=1),
        username: str | None = None,
        password: str | None = None,
    ) -> dict[str, Any]:
        from ao3kit.tags.metadata import fetch_tag_sets_search

        auth_user, auth_pass = _resolve_credentials(username, password)
        try:
            result = fetch_tag_sets_search(
                q, page=page, username=auth_user, password=auth_pass
            )
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        return result.to_dict()

    @api.get("/tag-sets/{tag_set_id}")
    def tag_set_get(
        tag_set_id: int,
        username: str | None = None,
        password: str | None = None,
    ) -> dict[str, Any]:
        from ao3kit.tags.metadata import fetch_tag_set

        auth_user, auth_pass = _resolve_credentials(username, password)
        try:
            detail = fetch_tag_set(
                tag_set_id, username=auth_user, password=auth_pass
            )
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        return detail.to_dict()

    # -- Download -----------------------------------------------------------

    @api.post("/download", status_code=202, response_model=JobCreated)
    def download_start(body: DownloadRequest) -> JobCreated:
        records = _parse_download_records(body)
        _resolve_credentials(body.username, body.password)

        job_id = uuid.uuid4().hex
        dest = Path(tempfile.mkdtemp(prefix="ao3-api-import-"))
        with _DOWNLOAD_LOCK:
            _DOWNLOAD_JOBS[job_id] = {
                "request": body,
                "records": records,
                "dest": str(dest),
                "status": "queued",
                "message": "Queued",
                "downloaded": 0,
                "skipped": 0,
                "failed": 0,
                "error": None,
                "zip_path": None,
            }
        thread = threading.Thread(
            target=_run_download_job, args=(job_id,), daemon=True
        )
        thread.start()
        return JobCreated(job_id=job_id)

    @api.get("/download/{job_id}", response_model=DownloadJobResponse)
    def download_status(job_id: str) -> DownloadJobResponse:
        with _DOWNLOAD_LOCK:
            job = _DOWNLOAD_JOBS.get(job_id)
            if job is None:
                raise HTTPException(status_code=404, detail="Download job not found")
            zip_path = job.get("zip_path")
            zip_ready = bool(zip_path and Path(zip_path).is_file())
            return DownloadJobResponse(
                job_id=job_id,
                status=job["status"],
                message=job.get("message") or "",
                downloaded=int(job.get("downloaded") or 0),
                skipped=int(job.get("skipped") or 0),
                failed=int(job.get("failed") or 0),
                records=list(job.get("records") or []),
                error=job.get("error"),
                zip_ready=zip_ready,
            )

    @api.get("/download/{job_id}/zip")
    def download_zip(job_id: str) -> FileResponse:
        from ao3kit.epubs import ZIP_NAME

        with _DOWNLOAD_LOCK:
            job = _DOWNLOAD_JOBS.get(job_id)
            zip_path = Path(job["zip_path"]) if job and job.get("zip_path") else None
        if zip_path is None or not zip_path.exists():
            raise HTTPException(status_code=404, detail="Import zip is not ready")
        return FileResponse(
            zip_path,
            media_type="application/zip",
            filename=ZIP_NAME,
        )

    # -- Config -------------------------------------------------------------

    @api.get("/config")
    def config_get() -> dict[str, Any]:
        cfg = _user_cfg()
        return {
            "home": str(cfg.home),
            "settings": cfg.settings.to_dict(),
        }

    @api.patch("/config")
    def config_patch(body: ConfigPatch) -> dict[str, Any]:
        cfg = _user_cfg()
        changes = body.model_dump(exclude_none=True)
        if not changes:
            raise HTTPException(status_code=400, detail="No settings to update.")
        cfg.update_settings(**changes)
        return {
            "home": str(cfg.home),
            "settings": cfg.settings.to_dict(),
        }

    @api.get("/config/rules")
    def config_rules_list() -> dict[str, Any]:
        cfg = _user_cfg()
        active = cfg.active_rules_path().resolve()
        files = []
        for path in cfg.list_rule_files():
            files.append(
                {
                    "name": path.stem,
                    "path": str(path),
                    "active": path.resolve() == active,
                }
            )
        return {"rules": files, "active_rules": cfg.settings.active_rules}

    @api.post("/config/rules", status_code=201)
    def config_rules_create(body: RuleCreate) -> dict[str, Any]:
        cfg = _user_cfg()
        try:
            path = cfg.create_rule(body.name, source=body.source)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except FileExistsError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if body.make_active:
            cfg.set_active_rules(body.name)
        return {
            "name": path.stem,
            "path": str(path),
            "active": path.resolve() == cfg.active_rules_path().resolve(),
        }

    @api.post("/config/rules/install-example")
    def config_rules_install_example() -> dict[str, Any]:
        from ao3kit.config import copy_example_rules

        cfg = _user_cfg()
        try:
            path = copy_example_rules(cfg, name="example")
        except FileExistsError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except FileNotFoundError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        return {"name": path.stem, "path": str(path)}

    @api.get("/config/rules/{name}")
    def config_rules_get(name: str) -> dict[str, Any]:
        cfg = _user_cfg()
        try:
            source = cfg.read_rule(name)
            path = cfg.rule_path(name)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {
            "name": path.stem,
            "path": str(path),
            "source": source,
            "active": path.resolve() == cfg.active_rules_path().resolve(),
        }

    @api.put("/config/rules/{name}")
    def config_rules_put(name: str, body: RuleWrite) -> dict[str, Any]:
        from ao3kit.tags.rules import load_tag_rules

        cfg = _user_cfg()
        try:
            path = cfg.write_rule(name, body.source)
            load_tag_rules(path)
            if body.make_active:
                cfg.set_active_rules(name)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {
            "name": path.stem,
            "path": str(path),
            "active": path.resolve() == cfg.active_rules_path().resolve(),
        }

    @api.post("/config/rules/{name}/activate")
    def config_rules_activate(name: str) -> dict[str, Any]:
        cfg = _user_cfg()
        try:
            path = cfg.set_active_rules(name)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {
            "name": path.stem,
            "path": str(path),
            "active_rules": cfg.settings.active_rules,
        }

    return api


# Module-level app for ``uvicorn ao3kit.api:app`` / mounting.
app = create_api_app()
