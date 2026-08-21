"""Tests for the JSON REST API (/api/v1)."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from ao3kit.api import _DOWNLOAD_JOBS, _SCRAPE_JOBS, create_api_app
from ao3kit.scrape import WorkMetadata, WorkRecord
from ao3kit.tags.metadata import ResolvedTag, SimplifiedTags, TagCacheStats


@pytest.fixture
def api_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("AO3KIT_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("AO3_USERNAME", raising=False)
    monkeypatch.delenv("AO3_PASSWORD", raising=False)
    _SCRAPE_JOBS.clear()
    _DOWNLOAD_JOBS.clear()
    return TestClient(create_api_app())


@pytest.fixture
def mounted_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("AO3KIT_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("AO3_USERNAME", raising=False)
    monkeypatch.delenv("AO3_PASSWORD", raising=False)
    _SCRAPE_JOBS.clear()
    _DOWNLOAD_JOBS.clear()
    # Import after env so config home is set for any incidental loads.
    from ao3kit.webapp import app

    return TestClient(app)


def test_health(api_client: TestClient):
    response = api_client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert "version" in body


def test_openapi_schema(api_client: TestClient):
    response = api_client.get("/openapi.json")
    assert response.status_code == 200
    schema = response.json()
    assert schema["info"]["title"] == "ao3kit API"
    paths = schema["paths"]
    assert "/health" in paths
    assert "/scrape" in paths


def test_mounted_under_webapp(mounted_client: TestClient):
    response = mounted_client.get("/api/v1/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_parse_url(api_client: TestClient):
    url = (
        "https://archiveofourown.org/works?"
        "work_search%5Bsort_column%5D=kudos_count"
        "&work_search%5Blanguage_id%5D=en"
        "&tag_id=Doctor+Who+%282005%29"
        "&page=2"
    )
    response = api_client.post("/scrape/parse-url", json={"url": url})
    assert response.status_code == 200
    body = response.json()
    assert body["start_page"] == 2
    assert body["criteria"]["sort_column"] == "kudos_count"
    assert body["criteria"]["language_id"] == "en"
    assert "Doctor Who" in (body["criteria"]["tag_id"] or "")
    assert "archiveofourown.org/works?" in body["search_url"]


def test_scrape_requires_criteria(api_client: TestClient):
    response = api_client.post("/scrape", json={})
    assert response.status_code == 400


def test_scrape_job_with_mock(
    api_client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    work = WorkRecord(
        work_id="1",
        url="https://archiveofourown.org/works/1",
        title="Test Work",
        author="Author",
        tags=["Fluff"],
        metadata=WorkMetadata(words=1000, kudos=100, hits=500),
    )

    def fake_scrape(criteria, **kwargs):
        on_work = kwargs.get("on_work")
        if on_work:
            on_work(work)
        return [work]

    monkeypatch.setattr("ao3kit.scrape.scrape_search", fake_scrape)
    monkeypatch.setattr(
        "ao3kit.scrape.create_session", lambda *a, **k: MagicMock()
    )

    start = api_client.post(
        "/scrape",
        json={
            "criteria": {"tag_id": "Doctor Who (2005)", "query": None},
            "max_results": 1,
            "delay": 0,
        },
    )
    assert start.status_code == 202
    job_id = start.json()["job_id"]

    body: dict[str, Any] | None = None
    for _ in range(50):
        status = api_client.get(f"/scrape/{job_id}")
        assert status.status_code == 200
        body = status.json()
        if body["status"] in {"done", "error"}:
            break
        time.sleep(0.05)

    assert body is not None
    assert body["status"] == "done"
    assert len(body["works"]) == 1
    assert body["works"][0]["title"] == "Test Work"
    assert body["search_url"]


def test_config_get_and_patch(api_client: TestClient):
    get_resp = api_client.get("/config")
    assert get_resp.status_code == 200
    assert get_resp.json()["settings"]["request_delay"] == 1.5

    patch = api_client.patch(
        "/config",
        json={"request_delay": 7.5, "notes": "api-test"},
    )
    assert patch.status_code == 200
    assert patch.json()["settings"]["request_delay"] == 7.5
    assert patch.json()["settings"]["notes"] == "api-test"

    again = api_client.get("/config")
    assert again.json()["settings"]["request_delay"] == 7.5


def test_config_rules_crud(api_client: TestClient):
    listed = api_client.get("/config/rules")
    assert listed.status_code == 200
    assert any(r["name"] == "default" for r in listed.json()["rules"])

    created = api_client.post(
        "/config/rules",
        json={
            "name": "custom",
            "source": (
                "from ao3kit.tags.rules import TagRulesConfig\n"
                "RULES = TagRulesConfig(resolve_canonical=False, rules=[])\n"
            ),
            "make_active": True,
        },
    )
    assert created.status_code == 201
    assert created.json()["active"] is True

    got = api_client.get("/config/rules/custom")
    assert got.status_code == 200
    assert "TagRulesConfig" in got.json()["source"]

    activated = api_client.post("/config/rules/default/activate")
    assert activated.status_code == 200
    assert "default" in activated.json()["active_rules"]


def test_tags_resolve_mocked(
    api_client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    class FakeResolver:
        def __init__(self, *a, **k):
            self.stats = TagCacheStats(memory_hits=1)

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return None

        def simplify(self, tags, drop_unmarked=False):
            return SimplifiedTags(
                original=list(tags),
                resolved=[
                    ResolvedTag(
                        original=t,
                        resolved=t.title(),
                        status="canonical",
                        changed=t != t.title(),
                    )
                    for t in tags
                ],
                simplified=[t.title() for t in tags],
            )

    monkeypatch.setattr("ao3kit.api._make_resolver", lambda **k: FakeResolver())

    response = api_client.post(
        "/tags/resolve",
        json={"tags": ["fluff", "slow burn"], "use_cache": False},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["simplified"] == ["Fluff", "Slow Burn"]
    assert body["cache_stats"]["memory_hits"] == 1


def test_tags_apply_mocked(
    api_client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    from ao3kit.tags.metadata import TagResolver

    resolver = TagResolver(
        session=object(),
        delay=0,
        owns_session=False,
        cache_path=None,
        persist=False,
    )

    def fake_resolve_one(name: str) -> ResolvedTag:
        return ResolvedTag(
            original=name, resolved=name, status="canonical", changed=False
        )

    resolver.resolve_one = fake_resolve_one  # type: ignore[method-assign]

    class Ctx:
        def __enter__(self):
            return resolver

        def __exit__(self, *exc):
            return None

    monkeypatch.setattr("ao3kit.api._make_resolver", lambda **k: Ctx())

    response = api_client.post(
        "/tags/apply",
        json={"tags": ["Fluff"], "use_cache": False},
    )
    assert response.status_code == 200
    body = response.json()
    assert "Fluff" in body["simplified"]
    assert "tags" in body


def test_download_requires_payload(api_client: TestClient):
    response = api_client.post("/download", json={})
    assert response.status_code == 400


def test_download_job_mocked(
    api_client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    from ao3kit.epubs import DownloadOutcome, DownloadReport

    def fake_download(records, dest, session, **kwargs):
        outcomes = [
            DownloadOutcome(record=dict(r, epub_file="epubs/1.epub"), status="downloaded")
            for r in records
        ]
        zip_path = kwargs.get("zip_path")
        if zip_path:
            Path(zip_path).write_bytes(b"PK\x05\x06" + b"\x00" * 18)
        return DownloadReport(outcomes=outcomes)

    monkeypatch.setattr("ao3kit.epubs.download_records", fake_download)
    monkeypatch.setattr(
        "ao3kit.scrape.create_session", lambda *a, **k: MagicMock()
    )

    start = api_client.post(
        "/download",
        json={
            "records": [
                {
                    "work_id": "1",
                    "url": "https://archiveofourown.org/works/1",
                    "title": "Test",
                }
            ],
            "delay": 0,
        },
    )
    assert start.status_code == 202
    job_id = start.json()["job_id"]

    body: dict[str, Any] | None = None
    for _ in range(50):
        status = api_client.get(f"/download/{job_id}")
        assert status.status_code == 200
        body = status.json()
        if body["status"] in {"done", "error"}:
            break
        time.sleep(0.05)

    assert body is not None
    assert body["status"] == "done"
    assert body["downloaded"] == 1
    assert body["zip_ready"] is True

    zip_resp = api_client.get(f"/download/{job_id}/zip")
    assert zip_resp.status_code == 200
    assert zip_resp.headers["content-type"].startswith("application/zip")
