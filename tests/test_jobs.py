"""Tests for detachable ao3kit background jobs."""

from __future__ import annotations

import json
from pathlib import Path

from ao3kit.jobs import (
    JobSpec,
    JobStatus,
    default_jobs_dir,
    find_job,
    format_status_text,
    list_jobs,
    live_job_status,
    main,
    new_job_id,
    progress_from_line,
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
