"""Fanfic desk search planning and HTTP status page."""

from __future__ import annotations

import json
import threading
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from ao3kit.desk import (
    DeskServer,
    job_detail,
    list_job_views,
    page_html,
    search_argv,
    search_title,
    start_search,
)
from ao3kit.jobs import JobSpec, JobStatus, save_spec, write_status


def test_search_argv_url_and_download() -> None:
    argv, error = search_argv(
        {"url": "https://archiveofourown.org/works?tag_id=1", "max_results": "10"},
        jsonl=Path("/tmp/results.jsonl"),
        epub_dir=Path("/tmp/work"),
    )
    assert error is None
    assert argv is not None
    assert argv[:4] == ["scrape", "-o", "/tmp/results.jsonl", "--verbose"]
    assert "--url" in argv
    assert "--tag-id" not in argv
    assert "--download" in argv
    assert "--epub-dir" in argv
    assert "--max-results" in argv
    assert argv[argv.index("--max-results") + 1] == "10"


def test_search_argv_form_filters() -> None:
    argv, error = search_argv(
        {
            "tag_id": "Harry Potter",
            "query": "draco",
            "complete": "true",
            "include_series": True,
            "download": False,
            "max_results": 25,
        },
        jsonl=Path("results.jsonl"),
        epub_dir=Path("work"),
    )
    assert error is None
    assert argv is not None
    assert "--tag-id" in argv and "Harry Potter" in argv
    assert "--query" in argv and "draco" in argv
    assert "--complete" in argv
    assert "--include-series" in argv
    assert "--download" not in argv


def test_search_argv_requires_a_target() -> None:
    argv, error = search_argv({}, jsonl=Path("a"), epub_dir=Path("b"))
    assert argv is None
    assert error


def test_search_argv_rejects_bad_max() -> None:
    argv, error = search_argv(
        {"query": "river", "max_results": "0"},
        jsonl=Path("a"),
        epub_dir=Path("b"),
    )
    assert argv is None
    assert "Max results" in (error or "")


def test_search_title_uses_download_verb() -> None:
    assert search_title({"tag_id": "Merlin"}).startswith("Search and download")
    assert search_title({"query": "x", "download": False}).startswith("Search:")


def _server(tmp_path: Path, *, config_home: Path | None = None) -> tuple[DeskServer, str]:
    server = DeskServer(
        ("127.0.0.1", 0),
        tmp_path,
        calibre_port=8081,
        config_home=config_home,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address[:2]
    return server, f"http://{host}:{port}"


def test_page_links_calibre_and_lists_jobs(tmp_path: Path) -> None:
    assert "8081" in page_html(calibre_port=8081)
    server, base = _server(tmp_path)
    try:
        with urlopen(base + "/") as response:
            html = response.read().decode()
        assert "Fanfic desk" in html
        assert "Settings" in html
        assert "const calibrePort = 8081" in html
        with urlopen(base + "/api/jobs") as response:
            payload = json.load(response)
        assert payload["jobs"] == []
    finally:
        server.shutdown()
        server.server_close()


def test_search_post_rejects_empty_and_starts_planned_job(tmp_path: Path, monkeypatch) -> None:
    captured: dict[str, JobSpec] = {}

    def fake_start(spec: JobSpec, **kwargs: object) -> tuple[JobStatus, None]:
        captured["spec"] = spec
        return JobStatus(id=spec.id, title=spec.title, kind="scrape", running=True, message="Started"), None

    monkeypatch.setattr("ao3kit.desk.start_job", fake_start)
    server, base = _server(tmp_path)
    try:
        req = Request(
            base + "/api/search",
            data=b"{}",
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            urlopen(req)
            raise AssertionError("empty search should fail")
        except HTTPError as exc:
            assert exc.code == 400
            denied = json.load(exc)
        assert denied["error"]

        body = json.dumps({"query": "river song", "download": False, "max_results": 5}).encode()
        req = Request(
            base + "/api/search",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(req) as response:
            started = json.load(response)
        assert started["job"]["running"] is True
        spec = captured["spec"]
        assert spec.steps[0][0] == "scrape"
        assert "--query" in spec.steps[0]
        assert "river song" in spec.steps[0]
        assert "--download" not in spec.steps[0]
    finally:
        server.shutdown()
        server.server_close()


def test_job_detail_tails_log(tmp_path: Path) -> None:
    job_dir = tmp_path / "job-1"
    job_dir.mkdir()
    save_spec(
        job_dir / "spec.json",
        JobSpec(id="job-1", title="Search", kind="scrape", steps=[["scrape", "-o", "out.jsonl"]]),
    )
    write_status(
        job_dir / "status.json",
        JobStatus(id="job-1", title="Search", kind="scrape", running=False, exit_code=0, result="1 work"),
    )
    (job_dir / "job.log").write_text("line one\nline two\n", encoding="utf-8")
    views = list_job_views(tmp_path)
    assert views[0]["id"] == "job-1"
    assert views[0]["title"] == "Search"
    assert "log_path" not in views[0]
    assert views[0]["retryable"] is False
    detail = job_detail("job-1", jobs_dir=tmp_path)
    assert detail is not None
    assert "line two" in detail["log"]
    assert job_detail("../secret", jobs_dir=tmp_path) is None


def _settings_body(**overrides: object) -> dict[str, object]:
    body: dict[str, object] = {
        "username": "river",
        "password": "secret-pass",
        "min_request_interval": 2,
        "tag_warm_interval": 12,
        "default_language_id": "en",
        "max_results": 15,
        "download_epubs": False,
        "include_series": True,
        "include_metatags": False,
        "drop_unmarked": True,
        "cover_enabled": False,
    }
    body.update(overrides)
    return body


def test_settings_save_hides_password_and_keeps_it(tmp_path: Path) -> None:
    home = tmp_path / "cfg"
    jobs = tmp_path / "jobs"
    server, base = _server(jobs, config_home=home)
    try:
        req = Request(
            base + "/api/settings",
            data=json.dumps(_settings_body()).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(req) as response:
            saved = json.load(response)
        assert saved["username"] == "river"
        assert saved["password_set"] is True
        assert saved["download_epubs"] is False
        assert saved["max_results"] == 15
        assert "password" not in saved
        secret_path = home / "ao3-login.json"
        assert "secret-pass" in secret_path.read_text(encoding="utf-8")
        assert secret_path.stat().st_mode & 0o777 == 0o600
        with urlopen(base + "/api/settings") as response:
            again = json.load(response)
        assert "secret-pass" not in json.dumps(again)
        req = Request(
            base + "/api/settings",
            data=json.dumps(_settings_body(password="", min_request_interval=3)).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(req) as response:
            kept = json.load(response)
        assert kept["password_set"] is True
        assert kept["min_request_interval"] == 3
        assert "secret-pass" in secret_path.read_text(encoding="utf-8")
    finally:
        server.shutdown()
        server.server_close()


def test_login_uses_saved_password(tmp_path: Path, monkeypatch) -> None:
    from ao3kit.credentials import write_credentials
    from ao3kit.desk import check_login

    write_credentials("river", "secret-pass", home=tmp_path)
    seen: dict[str, str] = {}

    def fake_verify(username: str, password: str, **kwargs: object) -> str:
        seen["username"] = username
        seen["password"] = password
        return username

    monkeypatch.setattr("ao3kit.http.verify_login", fake_verify)
    result, error = check_login({"username": "river", "password": ""}, home=tmp_path)
    assert error is None
    assert result is not None
    assert result["username"] == "river"
    assert seen == {"username": "river", "password": "secret-pass"}
