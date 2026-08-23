"""Plugin job spec builders and CLI argv (Calibre-free)."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[1] / "calibre-plugin"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_scrape_run():
    return _load("scrape_run", PLUGIN / "scrape_run.py")


def load_job_plans():
    jobs_mod = _load("jobs", PLUGIN / "jobs.py")
    scrape_mod = load_scrape_run()
    sys.modules["jobs"] = jobs_mod
    sys.modules["scrape_run"] = scrape_mod
    return _load("job_plans", PLUGIN / "job_plans.py")


def test_build_job_argv_includes_jobs_dir():
    mod = load_scrape_run()
    assert mod.build_job_start_argv("/tmp/job") == ["job", "start", "--dir", "/tmp/job"]
    assert mod.build_job_start_argv("/tmp/job", jobs_dir="/tmp/jobs") == [
        "job",
        "start",
        "--jobs-dir",
        "/tmp/jobs",
        "--dir",
        "/tmp/job",
    ]
    assert mod.build_job_list_argv(jobs_dir="/tmp/jobs") == [
        "job",
        "list",
        "--jobs-dir",
        "/tmp/jobs",
    ]
    assert mod.build_job_stop_argv("abc", jobs_dir="/tmp/jobs") == [
        "job",
        "stop",
        "--jobs-dir",
        "/tmp/jobs",
        "abc",
    ]
    assert mod.build_job_retry_argv("abc", jobs_dir="/tmp/jobs") == [
        "job",
        "retry",
        "--jobs-dir",
        "/tmp/jobs",
        "abc",
    ]
    assert mod.build_job_delete_argv(["abc", "def"], jobs_dir="/tmp/jobs") == [
        "job",
        "delete",
        "--jobs-dir",
        "/tmp/jobs",
        "abc",
        "def",
    ]
    assert mod.build_job_clear_argv(finished=True, jobs_dir="/tmp/jobs") == [
        "job",
        "clear",
        "--jobs-dir",
        "/tmp/jobs",
        "--finished",
    ]
    assert mod.build_job_clear_argv(inactive=True) == ["job", "clear", "--inactive"]
    assert mod.build_job_status_argv("abc", jobs_dir="/tmp/jobs")[-1] == "abc"


def test_plugin_job_is_retryable():
    jobs = _load("plugin_jobs", PLUGIN / "jobs.py")
    assert not jobs.job_is_retryable({"id": "warm", "exit_code": 1})
    assert not jobs.job_is_retryable({"id": "x", "running": True, "exit_code": 1})
    assert not jobs.job_is_retryable({"id": "x", "ingest": "pending"})
    assert not jobs.job_is_retryable({"id": "x", "exit_code": 0, "ingest": "done"})
    assert jobs.job_is_retryable({"id": "x", "exit_code": 1, "ingest": "skipped"})
    assert jobs.job_is_retryable({"id": "x", "ingest": "cancelled"})
    assert jobs.job_is_retryable({"id": "x", "ingest": "failed", "exit_code": 0})
    assert jobs.job_is_retryable({"id": "x", "ingest": "done", "exit_code": 1})
    assert not jobs.job_is_deletable({"id": "warm"})
    assert not jobs.job_is_deletable({"id": "x", "running": True})
    assert not jobs.job_is_deletable({"id": "x", "ingest": "pending"})
    assert jobs.job_is_deletable({"id": "x", "exit_code": 1, "ingest": "skipped"})
    assert jobs.job_clear_bucket({"id": "x", "exit_code": 0, "ingest": "done"}) == "finished"
    assert jobs.job_clear_bucket({"id": "x", "ingest": "cancelled"}) == "stopped"


def test_first_line_handles_empty_and_whitespace():
    jobs = _load("plugin_jobs", PLUGIN / "jobs.py")
    assert jobs.first_line("") == ""
    assert jobs.first_line(None) == ""
    assert jobs.first_line("\n") == ""
    assert jobs.first_line([]) == ""
    assert jobs.first_line("alpha\nbeta") == "alpha"
    assert jobs.first_line("  hello  ", 4) == "hell"


def test_job_watch_phase_and_header_while_saving():
    jobs = _load("plugin_jobs", PLUGIN / "jobs.py")
    running = {"title": "Search AO3", "running": True, "message": "Page 2/5"}
    saving = {
        "title": "Search AO3",
        "running": False,
        "exit_code": 0,
        "finished_at": "2026-01-01T00:00:00Z",
        "ingest": "pending",
        "message": "Wrote 3 works",
    }
    done = {
        "title": "Search AO3",
        "running": False,
        "exit_code": 0,
        "finished_at": "2026-01-01T00:00:00Z",
        "ingest": "done",
        "result": "Imported 3 books.",
    }
    failed = {
        "title": "Search AO3",
        "running": False,
        "exit_code": 1,
        "ingest": "skipped",
        "message": "AO3 returned 429",
    }
    assert jobs.job_watch_phase(running) == "running"
    assert jobs.job_watch_phase(saving) == "saving"
    assert jobs.job_watch_phase(done) == "done"
    assert jobs.job_watch_phase(failed) == "failed"
    header_saving = jobs.format_job_header(saving, Path("/tmp/job.log"))
    assert "Finished" not in header_saving
    assert "Saving" in header_saving
    assert "/tmp/job.log" not in header_saving
    header_done = jobs.format_job_header(done)
    assert "Done" in header_done
    assert "Imported 3 books." in header_done
    assert jobs.job_was_notified({"notified": True})
    assert not jobs.job_was_notified({})


def test_plan_scrape_adds_enrich_step(tmp_path: Path):
    plans = load_job_plans()
    job_dir = tmp_path / "scrape-1"
    spec = plans.plan_scrape(
        {
            "url": "https://archiveofourown.org/works?tag_id=X",
            "use_form_criteria": False,
            "max_results": "5",
            "simplify_tags": True,
            "download_epubs": False,
        },
        job_dir,
    )
    assert spec["id"] == job_dir.name
    assert spec["kind"] == "scrape"
    assert spec["steps"][0][0] == "scrape"
    assert spec["steps"][1][:2] == ["tags", "enrich"]
    assert spec["plugin"]["action"] == "import_records"
    assert spec["plugin"]["skip_existing_epub"] is True
    assert spec["result"]["source"] == "jsonl_count"
    assert spec["result"]["path"].endswith("cleaned.jsonl")
    assert spec["plugin"]["incremental_import"] is True
    assert spec["plugin"]["results_jsonl"].endswith("results.jsonl")
    written = json.loads((job_dir / "spec.json").read_text(encoding="utf-8"))
    assert written["steps"] == spec["steps"]


def test_plan_import_without_ao3_steps(tmp_path: Path):
    plans = load_job_plans()
    job_dir = tmp_path / "import-1"
    spec = plans.plan_import(
        [{"work_id": "1", "title": "One", "url": "https://archiveofourown.org/works/1"}],
        job_dir,
        options={"simplify_tags": False, "include_series": False},
        bundle_root=tmp_path / "bundle",
    )
    assert spec["steps"] == []
    assert spec["plugin"]["action"] == "import_records"
    assert spec["plugin"]["skip_existing_epub"] is True
    assert spec["plugin"]["incremental_import"] is True
    assert spec["plugin"]["results_jsonl"].endswith("input.jsonl")
    assert spec["result"]["source"] == "jsonl_count"
    assert (job_dir / "work" / "input.jsonl").is_file()


def test_plan_download_selected_incremental(tmp_path: Path):
    plans = load_job_plans()
    job_dir = tmp_path / "dl-1"
    spec = plans.plan_download_selected(
        [
            {
                "book_id": 12,
                "record": {
                    "work_id": "90876776",
                    "url": "https://archiveofourown.org/works/90876776",
                    "title": "Time Storm",
                },
            }
        ],
        [],
        job_dir,
        {},
    )
    assert spec["kind"] == "download"
    assert spec["steps"][0][0] == "download"
    assert spec["plugin"]["incremental_epubs"] is True
    assert spec["plugin"]["action"] == "attach_epubs"
    assert spec["result"]["field"] == "epub_file"
    assert "--force" not in spec["steps"][0]


def test_plan_cover_selected(tmp_path: Path):
    plans = load_job_plans()
    job_dir = tmp_path / "cover-1"
    spec = plans.plan_cover_selected(
        [
            {
                "book_id": 12,
                "record": {
                    "work_id": "90876776",
                    "title": "Time Storm",
                    "epub_file": "epubs/90876776.epub",
                },
                "has_epub": True,
            }
        ],
        [],
        job_dir,
        {
            "set_calibre_cover": True,
            "username": "emily",
            "password": "secret",
        },
    )
    assert spec["kind"] == "cover"
    assert spec["steps"][0][0] == "cover"
    assert "--jsonl" in spec["steps"][0]
    assert "--png-dir" in spec["steps"][0]
    assert "--replace" in spec["steps"][0]
    assert "--username" not in spec["steps"][0]
    assert "--password" not in spec["steps"][0]
    assert spec["plugin"]["action"] == "apply_covers"
    assert spec["plugin"]["set_calibre_cover"] is True
    assert (job_dir / "work" / "bundle" / "results.jsonl").is_file()


def test_plan_complete_selected_does_series_download_and_enrich(tmp_path: Path):
    plans = load_job_plans()
    job_dir = tmp_path / "complete-1"
    spec = plans.plan_complete_selected(
        [
            {
                "work_id": "90876776",
                "url": "https://archiveofourown.org/works/90876776",
                "title": "Time Storm",
            }
        ],
        [],
        job_dir,
        {"download_epubs": False, "simplify_tags": False, "update_existing": False},
    )
    assert spec["kind"] == "complete"
    assert spec["title"].startswith("Complete selected")
    assert spec["steps"][0][0] == "scrape"
    assert "--series-from" in spec["steps"][0]
    assert "--download" in spec["steps"][0]
    assert spec["steps"][1][:2] == ["tags", "enrich"]
    assert spec["plugin"]["action"] == "import_records"
    assert spec["plugin"]["update_existing"] is True
    assert spec["plugin"]["skip_existing_epub"] is True
    assert spec["plugin"]["incremental_import"] is True
    written = json.loads((job_dir / "spec.json").read_text(encoding="utf-8"))
    assert written["kind"] == "complete"
    assert written["steps"] == spec["steps"]


def test_plan_graph_serve_is_singleton_job(tmp_path: Path):
    plans = load_job_plans()
    scrape = load_scrape_run()
    job_dir = tmp_path / "graph"
    spec = plans.plan_graph_serve(job_dir)
    assert spec["id"] == "graph"
    assert spec["kind"] == "graph"
    assert spec["steps"] == [scrape.build_graph_serve_argv()]
    assert spec["plugin"]["action"] == "none"
    assert (job_dir / "spec.json").is_file()


def test_merge_ready_with_jsonl():
    plans = load_job_plans()
    merged = plans.merge_ready_with_jsonl(
        [{"book_id": 1, "record": {"work_id": "10", "title": "old"}}],
        [{"work_id": "10", "title": "new", "cleaned": {"tags": ["Fluff"]}}],
        work_id_of=lambda rec: rec.get("work_id"),
    )
    assert merged[0]["record"]["title"] == "new"
    assert merged[0]["book_id"] == 1
