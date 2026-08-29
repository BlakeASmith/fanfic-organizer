"""Tests for detachable ao3kit background jobs."""

from __future__ import annotations

import json
from pathlib import Path

from ao3kit.jobs import (
    JobSpec,
    JobStatus,
    clear_jobs,
    count_jsonl_records,
    default_jobs_dir,
    delete_job,
    evaluate_job_result,
    find_job,
    format_status_text,
    infer_result_spec,
    job_clear_bucket,
    job_is_deletable,
    job_is_retryable,
    jsonl_count_result,
    list_jobs,
    live_job_status,
    main,
    new_job_id,
    progress_from_line,
    redact_argv,
    retry_job,
    run_job_dir,
    save_spec,
    split_steps,
    start_job,
    write_status,
)
from ao3kit.proc import running_pid, write_pid


def test_split_steps_on_and():
    assert split_steps(["scrape", "-o", "out.jsonl", "--verbose"]) == [
        ["scrape", "-o", "out.jsonl", "--verbose"]
    ]
    assert split_steps(
        ["scrape", "-o", "a.jsonl", "--and", "tags", "enrich", "--jsonl", "a.jsonl"]
    ) == [
        ["scrape", "-o", "a.jsonl"],
        ["tags", "enrich", "--jsonl", "a.jsonl"],
    ]


def test_redact_argv_hides_password():
    argv = ["cover", "--jsonl", "in.jsonl", "--username", "emily", "--password", "secret"]
    assert redact_argv(argv) == [
        "cover",
        "--jsonl",
        "in.jsonl",
        "--username",
        "emily",
        "--password",
        "***",
    ]
    assert redact_argv(["login", "-p", "secret"]) == ["login", "-p", "***"]


def test_progress_from_line():
    assert progress_from_line("Caching tags [3/10] Fluff") == [3, 10]
    assert progress_from_line("12 unique tags across batch (4 already cached, 8 need AO3)") == [
        0,
        8,
    ]
    assert progress_from_line("hello") is None


def test_new_job_id_includes_kind():
    job_id = new_job_id("tags.enrich")
    assert "tags-enrich" in job_id


def test_run_empty_steps_pending_ingest(tmp_path: Path):
    job_dir = tmp_path / "jobs" / "import-1"
    spec = JobSpec(
        id="import-1",
        title="Import JSONL",
        kind="import",
        steps=[],
        plugin={"action": "import_records"},
    )
    save_spec(job_dir / "spec.json", spec)
    assert run_job_dir(job_dir) == 0
    status = live_job_status(job_dir)
    assert status.running is False
    assert status.exit_code == 0
    assert status.ingest == "pending"
    assert (job_dir / "job.log").is_file()
    assert running_pid(job_dir / "job.pid") is None


def test_run_failed_step_skips_ingest(tmp_path: Path):
    job_dir = tmp_path / "jobs" / "bad"
    spec = JobSpec(
        id="bad",
        title="Bad",
        kind="scrape",
        steps=[["definitely-not-a-command"]],
        plugin={"action": "import_records"},
        cwd=str(tmp_path),
    )
    save_spec(job_dir / "spec.json", spec)
    code = run_job_dir(job_dir)
    assert code != 0
    status = live_job_status(job_dir)
    assert status.ingest == "skipped"
    assert status.exit_code != 0


def test_foreground_start_config_show(tmp_path: Path, capsys):
    jobs_dir = tmp_path / "jobs"
    code = main(
        [
            "start",
            "--jobs-dir",
            str(jobs_dir),
            "--foreground",
            "--title",
            "Show config",
            "--kind",
            "config",
            "--",
            "config",
            "show",
        ]
    )
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["running"] is False
    assert payload["exit_code"] == 0
    assert payload["title"] == "Show config"
    children = list(jobs_dir.iterdir())
    assert len(children) == 1
    log = (children[0] / "job.log").read_text(encoding="utf-8")
    assert "Step 1/1" in log


def test_cli_list_and_status(tmp_path: Path, capsys):
    jobs_dir = tmp_path / "jobs"
    spec = JobSpec(id="listed", title="Listed", kind="scrape", steps=[])
    save_spec(jobs_dir / "listed" / "spec.json", spec)
    write_status(
        jobs_dir / "listed" / "status.json",
        JobStatus(id="listed", title="Listed", kind="scrape", message="idle"),
    )
    assert main(["list", "--jobs-dir", str(jobs_dir)]) == 0
    listed = json.loads(capsys.readouterr().out)
    assert listed[0]["id"] == "listed"
    assert main(["status", "--jobs-dir", str(jobs_dir), "listed"]) == 0
    status = json.loads(capsys.readouterr().out)
    assert status["title"] == "Listed"


def test_cli_log_tail(tmp_path: Path, capsys):
    jobs_dir = tmp_path / "jobs"
    job_dir = jobs_dir / "logged"
    spec = JobSpec(id="logged", title="Logged", kind="scrape")
    save_spec(job_dir / "spec.json", spec)
    (job_dir / "job.log").write_text("alpha\nbeta\ngamma\n", encoding="utf-8")
    assert main(["log", "--jobs-dir", str(jobs_dir), "--lines", "2", "logged"]) == 0
    assert capsys.readouterr().out == "beta\ngamma\n"


def test_cli_stop_stale_pid(tmp_path: Path, capsys):
    jobs_dir = tmp_path / "jobs"
    job_dir = jobs_dir / "stale"
    save_spec(job_dir / "spec.json", JobSpec(id="stale", title="Stale"))
    write_pid(job_dir / "job.pid", 99999999)
    write_status(
        job_dir / "status.json",
        JobStatus(id="stale", running=True, pid=99999999, ingest="pending"),
    )
    assert main(["stop", "--jobs-dir", str(jobs_dir), "stale"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["running"] is False
    assert payload["ingest"] == "pending"
    assert running_pid(job_dir / "job.pid") is None


def test_start_already_running(tmp_path: Path, capsys):
    jobs_dir = tmp_path / "jobs"
    job_dir = jobs_dir / "live"
    save_spec(job_dir / "spec.json", JobSpec(id="live", title="Live"))
    write_pid(job_dir / "job.pid", __import__("os").getpid())
    code = main(["start", "--jobs-dir", str(jobs_dir), "--dir", str(job_dir)])
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert "Already running" in payload["message"]


def test_cli_main_dispatches_job(tmp_path: Path, capsys):
    from ao3kit.cli import main as cli_main

    jobs_dir = tmp_path / "jobs"
    jobs_dir.mkdir()
    assert cli_main(["job", "list", "--jobs-dir", str(jobs_dir)]) == 0
    assert json.loads(capsys.readouterr().out) == []


def test_format_status_text():
    text = format_status_text(
        JobStatus(id="x", title="Search AO3", running=True, pid=12, message="page 2")
    )
    assert "Search AO3" in text
    assert "pid 12" in text
    assert "page 2" in text


def test_find_job(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("AO3KIT_JOBS_DIR", str(tmp_path / "jobs"))
    spec = JobSpec(id="found", title="Found")
    save_spec(tmp_path / "jobs" / "found" / "spec.json", spec)
    assert find_job("found") == tmp_path / "jobs" / "found"
    assert find_job("missing") is None
    assert default_jobs_dir() == tmp_path / "jobs"


def test_list_jobs_running_first(tmp_path: Path):
    root = tmp_path / "jobs"
    save_spec(root / "old" / "spec.json", JobSpec(id="old", title="Old"))
    write_status(
        root / "old" / "status.json",
        JobStatus(id="old", title="Old", started_at="2026-01-01T00:00:00+00:00"),
    )
    save_spec(root / "new" / "spec.json", JobSpec(id="new", title="New"))
    write_pid(root / "new" / "job.pid", __import__("os").getpid())
    write_status(
        root / "new" / "status.json",
        JobStatus(
            id="new",
            title="New",
            running=True,
            started_at="2026-01-02T00:00:00+00:00",
        ),
    )
    jobs = list_jobs(root)
    ids = [item.id for item in jobs]
    assert ids[0] == "new"


def test_infer_result_spec_from_scrape_output():
    spec = infer_result_spec([["scrape", "-o", "out.jsonl", "--verbose"]])
    assert spec["source"] == "jsonl_count"
    assert spec["path"] == "out.jsonl"
    assert spec["label"] == "work"
    download = infer_result_spec(
        [["download", "-i", "in.jsonl", "-d", "/tmp/bundle", "--no-zip"]]
    )
    assert download["field"] == "epub_file"
    assert download["path"].endswith("results.jsonl")
    assert infer_result_spec([["config", "show"]])["source"] == "last_log"


def test_evaluate_jsonl_count(tmp_path: Path):
    jsonl = tmp_path / "work" / "out.jsonl"
    jsonl.parent.mkdir(parents=True)
    jsonl.write_text(
        '{"work_id":"1"}\n{"work_id":"2","epub_file":"a.epub"}\n\n',
        encoding="utf-8",
    )
    display, value = evaluate_job_result(
        jsonl_count_result(jsonl, label="work"),
        tmp_path,
    )
    assert value == 2
    assert display == "2 works"
    display, value = evaluate_job_result(
        jsonl_count_result(jsonl, label="EPUB", field="epub_file"),
        tmp_path,
    )
    assert value == 1
    assert display == "1 EPUB"


def test_evaluate_log_match_and_last_log(tmp_path: Path):
    log = tmp_path / "job.log"
    log.write_text(
        "--- Step 1/1 ---\nWrote 4 matching works.\n{\"exit_code\": 0}\n",
        encoding="utf-8",
    )
    display, _value = evaluate_job_result({"source": "last_log"}, tmp_path, log_path=log)
    assert display == "Wrote 4 matching works."
    display, value = evaluate_job_result(
        {"source": "log_match", "pattern": r"Wrote (\d+) matching"},
        tmp_path,
        log_path=log,
    )
    assert value == "4"
    assert display == "4"
    display, _value = evaluate_job_result(
        {"source": "last_log"},
        tmp_path,
        log_path=log,
        exit_code=130,
    )
    assert display == "Stopped"


def test_evaluate_json_field(tmp_path: Path):
    payload = tmp_path / "work" / "stats.json"
    payload.parent.mkdir(parents=True)
    payload.write_text('{"imported": 7, "nested": {"n": 3}}\n', encoding="utf-8")
    display, value = evaluate_job_result(
        {"source": "json_field", "path": "work/stats.json", "field": "imported"},
        tmp_path,
    )
    assert value == 7
    assert display == "7"
    display, value = evaluate_job_result(
        {
            "source": "json_field",
            "path": str(payload),
            "field": "nested.n",
            "template": "{value} series",
        },
        tmp_path,
    )
    assert display == "3 series"


def test_run_job_writes_jsonl_result(tmp_path: Path):
    job_dir = tmp_path / "jobs" / "counted"
    jsonl = job_dir / "work" / "out.jsonl"
    spec = JobSpec(
        id="counted",
        title="Counted",
        kind="import",
        steps=[],
        result=jsonl_count_result(jsonl, label="work"),
        plugin={"action": "import_records"},
    )
    save_spec(job_dir / "spec.json", spec)
    jsonl.parent.mkdir(parents=True)
    jsonl.write_text('{"work_id":"1"}\n{"work_id":"2"}\n', encoding="utf-8")
    assert run_job_dir(job_dir) == 0
    status = live_job_status(job_dir)
    assert status.result == "2 works"
    assert status.result_value == 2


def test_live_status_keeps_ingest_result(tmp_path: Path):
    job_dir = tmp_path / "jobs" / "imported"
    jsonl = job_dir / "work" / "out.jsonl"
    save_spec(
        job_dir / "spec.json",
        JobSpec(
            id="imported",
            title="Imported",
            result=jsonl_count_result(jsonl, label="work"),
        ),
    )
    jsonl.parent.mkdir(parents=True)
    jsonl.write_text('{"work_id":"1"}\n', encoding="utf-8")
    write_status(
        job_dir / "status.json",
        JobStatus(
            id="imported",
            ingest="done",
            exit_code=0,
            result="Imported 1 book into the library.",
        ),
    )
    status = live_job_status(job_dir)
    assert status.result == "Imported 1 book into the library."


def test_format_status_text_prefers_result():
    text = format_status_text(
        JobStatus(
            id="x",
            title="Search AO3",
            running=False,
            exit_code=0,
            message="Wrote 4 matching works.",
            result="4 works",
        )
    )
    assert "4 works" in text


def test_count_jsonl_records_skips_blank(tmp_path: Path):
    path = tmp_path / "a.jsonl"
    path.write_text("{}\n\n{}\n", encoding="utf-8")
    assert count_jsonl_records(path) == 2


def test_start_job_uses_fake_spawn(tmp_path: Path, monkeypatch):
    from ao3kit.proc import spawn_daemon as real_spawn

    seen: list[list[str]] = []

    class FakeProc:
        def poll(self):
            return None

        @property
        def pid(self):
            return 4242

    def fake_popen(argv, **kwargs):
        seen.append(list(argv))
        write_pid(Path(argv[argv.index("--dir") + 1]) / "job.pid", 4242)
        return FakeProc()

    def wrapped(argv, **kwargs):
        kwargs.pop("popen", None)
        return real_spawn(argv, popen=fake_popen, wait_seconds=1, **kwargs)

    monkeypatch.setattr("ao3kit.jobs.spawn_daemon", wrapped)
    spec = JobSpec(
        id="spawned",
        title="Spawned",
        kind="scrape",
        steps=[["scrape", "-o", "out.jsonl"]],
    )
    status, error = start_job(spec, jobs_dir=tmp_path / "jobs")
    assert error is None
    assert status.pid == 4242
    assert seen
    assert seen[0][-2:] == ["--dir", str(tmp_path / "jobs" / "spawned")]


def test_failed_attach_epubs_still_pending_ingest(tmp_path: Path):
    job_dir = tmp_path / "jobs" / "dl"
    spec = JobSpec(
        id="dl",
        title="Download",
        kind="download",
        steps=[["definitely-not-a-command"]],
        plugin={"action": "attach_epubs"},
        cwd=str(tmp_path),
    )
    save_spec(job_dir / "spec.json", spec)
    assert run_job_dir(job_dir) != 0
    status = live_job_status(job_dir)
    assert status.ingest == "pending"
    assert status.exit_code != 0


def test_failed_incremental_import_still_pending_ingest(tmp_path: Path):
    job_dir = tmp_path / "jobs" / "fill"
    spec = JobSpec(
        id="fill",
        title="Fill",
        kind="fill",
        steps=[["definitely-not-a-command"]],
        plugin={"action": "import_records", "incremental_import": True},
        cwd=str(tmp_path),
    )
    save_spec(job_dir / "spec.json", spec)
    assert run_job_dir(job_dir) != 0
    status = live_job_status(job_dir)
    assert status.ingest == "pending"
    assert status.exit_code != 0


def test_stop_does_not_cancel_finished_pending_ingest(tmp_path: Path, capsys):
    jobs_dir = tmp_path / "jobs"
    job_dir = jobs_dir / "done"
    save_spec(
        job_dir / "spec.json",
        JobSpec(id="done", title="Done", plugin={"action": "import_records"}),
    )
    write_status(
        job_dir / "status.json",
        JobStatus(id="done", running=False, exit_code=0, ingest="pending"),
    )
    assert main(["stop", "--jobs-dir", str(jobs_dir), "done"]) == 0
    capsys.readouterr()
    status = live_job_status(job_dir)
    assert status.ingest == "pending"
    assert status.exit_code == 0


def test_list_jobs_skips_warm_for_custom_dir(tmp_path: Path):
    root = tmp_path / "jobs"
    save_spec(root / "only" / "spec.json", JobSpec(id="only", title="Only"))
    ids = [item.id for item in list_jobs(root)]
    assert ids == ["only"]


def test_job_is_retryable():
    assert not job_is_retryable({"id": "warm", "exit_code": 1})
    assert not job_is_retryable({"id": "x", "running": True, "exit_code": 1})
    assert not job_is_retryable({"id": "x", "ingest": "pending", "exit_code": 1})
    assert not job_is_retryable({"id": "x", "exit_code": 0, "ingest": "done"})
    assert job_is_retryable({"id": "x", "exit_code": 1, "ingest": "skipped"})
    assert job_is_retryable({"id": "x", "exit_code": 130, "ingest": "cancelled"})
    assert job_is_retryable({"id": "x", "exit_code": 0, "ingest": "failed"})
    assert job_is_retryable(JobStatus(id="x", exit_code=2, ingest="skipped"))


def test_retry_unknown_job(tmp_path: Path, capsys):
    jobs_dir = tmp_path / "jobs"
    jobs_dir.mkdir()
    assert main(["retry", "--jobs-dir", str(jobs_dir), "missing"]) == 2
    err = capsys.readouterr().err
    assert "Unknown job" in err


def test_retry_refuses_warm(capsys):
    assert main(["retry", "warm"]) == 2
    assert "warmer" in capsys.readouterr().err.lower()


def test_retry_refuses_running(tmp_path: Path):
    jobs_dir = tmp_path / "jobs"
    job_dir = jobs_dir / "live"
    save_spec(job_dir / "spec.json", JobSpec(id="live", title="Live"))
    write_pid(job_dir / "job.pid", __import__("os").getpid())
    write_status(
        job_dir / "status.json",
        JobStatus(id="live", running=True, pid=__import__("os").getpid()),
    )
    status, error = retry_job("live", jobs_dir=jobs_dir, foreground=True)
    assert error is not None
    assert "already running" in error.lower()
    assert status.running is True


def test_retry_refuses_pending_ingest(tmp_path: Path):
    jobs_dir = tmp_path / "jobs"
    job_dir = jobs_dir / "ingest"
    save_spec(
        job_dir / "spec.json",
        JobSpec(id="ingest", title="Ingest", plugin={"action": "import_records"}),
    )
    write_status(
        job_dir / "status.json",
        JobStatus(id="ingest", running=False, exit_code=0, ingest="pending"),
    )
    status, error = retry_job("ingest", jobs_dir=jobs_dir, foreground=True)
    assert error is not None
    assert "calibre" in error.lower()
    assert status.ingest == "pending"
    assert status.retry_count == 0


def test_retry_failed_job_keeps_work_and_log(tmp_path: Path, capsys):
    jobs_dir = tmp_path / "jobs"
    job_dir = jobs_dir / "dl"
    work = job_dir / "work"
    work.mkdir(parents=True)
    cached = work / "already.epub"
    cached.write_text("keep-me", encoding="utf-8")
    save_spec(
        job_dir / "spec.json",
        JobSpec(id="dl", title="Download", kind="download", steps=[]),
    )
    (job_dir / "job.log").write_text("first run failed\n", encoding="utf-8")
    write_status(
        job_dir / "status.json",
        JobStatus(
            id="dl",
            title="Download",
            running=False,
            exit_code=1,
            ingest="skipped",
            notified=True,
            result="failed",
            message="Step failed",
            retry_count=0,
        ),
    )
    assert main(
        ["retry", "--jobs-dir", str(jobs_dir), "--foreground", "dl"]
    ) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["retry_count"] == 1
    assert payload["notified"] is False
    assert payload["exit_code"] == 0
    assert cached.read_text(encoding="utf-8") == "keep-me"
    log = (job_dir / "job.log").read_text(encoding="utf-8")
    assert "first run failed" in log
    assert "--- retry 1 " in log
    status, error = retry_job("dl", jobs_dir=jobs_dir, foreground=True)
    assert error is None
    assert status.retry_count == 2
    log = (job_dir / "job.log").read_text(encoding="utf-8")
    assert "--- retry 2 " in log


def test_retry_status_from_dict_coerces_count():
    status = JobStatus.from_dict({"id": "x", "retry_count": "3"})
    assert status.retry_count == 3
    status = JobStatus.from_dict({"id": "x"})
    assert status.retry_count == 0


def test_job_is_deletable_and_clear_bucket():
    assert not job_is_deletable({"id": "warm", "exit_code": 0})
    assert not job_is_deletable({"id": "x", "running": True})
    assert not job_is_deletable({"id": "x", "ingest": "pending"})
    assert job_is_deletable({"id": "x", "exit_code": 0, "ingest": "done"})
    assert job_clear_bucket({"id": "x", "exit_code": 0, "ingest": "done"}) == "finished"
    assert job_clear_bucket({"id": "x", "exit_code": 1, "ingest": "skipped"}) == "failed"
    assert job_clear_bucket({"id": "x", "ingest": "cancelled"}) == "stopped"
    assert job_clear_bucket({"id": "x", "ingest": "pending"}) is None


def test_delete_job_removes_directory(tmp_path: Path, capsys):
    jobs_dir = tmp_path / "jobs"
    job_dir = jobs_dir / "done"
    save_spec(job_dir / "spec.json", JobSpec(id="done", title="Done"))
    write_status(
        job_dir / "status.json",
        JobStatus(id="done", running=False, exit_code=0, ingest="done"),
    )
    (job_dir / "work").mkdir()
    (job_dir / "work" / "out.jsonl").write_text("{}\n", encoding="utf-8")
    assert main(["delete", "--jobs-dir", str(jobs_dir), "done"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["deleted"] == ["done"]
    assert not job_dir.exists()
    assert find_job("done", jobs_dir) is None


def test_delete_refuses_running_and_pending(tmp_path: Path):
    jobs_dir = tmp_path / "jobs"
    live = jobs_dir / "live"
    save_spec(live / "spec.json", JobSpec(id="live", title="Live"))
    write_pid(live / "job.pid", __import__("os").getpid())
    write_status(live / "status.json", JobStatus(id="live", running=True))
    ok, error = delete_job("live", jobs_dir=jobs_dir)
    assert ok is False
    assert "running" in (error or "").lower()
    assert live.is_dir()

    pending = jobs_dir / "pend"
    save_spec(pending / "spec.json", JobSpec(id="pend", title="Pend"))
    write_status(
        pending / "status.json",
        JobStatus(id="pend", running=False, exit_code=0, ingest="pending"),
    )
    ok, error = delete_job("pend", jobs_dir=jobs_dir)
    assert ok is False
    assert "calibre" in (error or "").lower()
    assert pending.is_dir()


def test_delete_unknown_and_warm(tmp_path: Path, capsys):
    jobs_dir = tmp_path / "jobs"
    jobs_dir.mkdir()
    assert main(["delete", "--jobs-dir", str(jobs_dir), "missing"]) == 2
    assert "Unknown job" in capsys.readouterr().err
    assert main(["delete", "warm"]) == 2
    assert "warmer" in capsys.readouterr().err.lower()


def test_clear_finished_keeps_failed(tmp_path: Path, capsys):
    jobs_dir = tmp_path / "jobs"
    good = jobs_dir / "good"
    bad = jobs_dir / "bad"
    stopped = jobs_dir / "stopped"
    save_spec(good / "spec.json", JobSpec(id="good", title="Good"))
    write_status(
        good / "status.json",
        JobStatus(id="good", running=False, exit_code=0, ingest="done"),
    )
    save_spec(bad / "spec.json", JobSpec(id="bad", title="Bad"))
    write_status(
        bad / "status.json",
        JobStatus(id="bad", running=False, exit_code=1, ingest="skipped"),
    )
    save_spec(stopped / "spec.json", JobSpec(id="stopped", title="Stopped"))
    write_status(
        stopped / "status.json",
        JobStatus(id="stopped", running=False, exit_code=130, ingest="cancelled"),
    )
    assert main(["clear", "--jobs-dir", str(jobs_dir), "--finished"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["deleted"] == ["good"]
    assert not good.exists()
    assert bad.is_dir()
    assert stopped.is_dir()

    result = clear_jobs(jobs_dir, failed=True, stopped=True)
    assert set(result["deleted"]) == {"bad", "stopped"}
    assert not bad.exists()
    assert not stopped.exists()


def test_clear_requires_filter(tmp_path: Path, capsys):
    jobs_dir = tmp_path / "jobs"
    jobs_dir.mkdir()
    assert main(["clear", "--jobs-dir", str(jobs_dir)]) == 2
    assert "--finished" in capsys.readouterr().err


def test_clear_inactive(tmp_path: Path, capsys):
    jobs_dir = tmp_path / "jobs"
    save_spec(jobs_dir / "a" / "spec.json", JobSpec(id="a"))
    write_status(
        jobs_dir / "a" / "status.json",
        JobStatus(id="a", exit_code=0, ingest="done"),
    )
    save_spec(jobs_dir / "b" / "spec.json", JobSpec(id="b"))
    write_status(
        jobs_dir / "b" / "status.json",
        JobStatus(id="b", exit_code=1, ingest="skipped"),
    )
    assert main(["clear", "--jobs-dir", str(jobs_dir), "--inactive"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert set(payload["deleted"]) == {"a", "b"}
