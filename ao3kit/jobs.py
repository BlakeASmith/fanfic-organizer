"""Detachable background jobs for any ``python -m ao3kit`` command.

Every long-running command can run as a child process with a log file.
Attach (``job log --follow`` / the plugin log window) is just a tail;
detaching does not stop the work.

Layout (under ``$XDG_STATE_HOME/fanfic-organizer/jobs/<id>/``, override with ``AO3KIT_JOBS_DIR``)::

    spec.json      argv steps, title, result spec, plugin ingest hints
    status.json    pid, running, last log line, exit code, result
    job.pid
    job.log
    work/          JSONL, EPUBs, criteria files

CLI::

    python -m ao3kit job start --title "Search" --kind scrape -- scrape -o out.jsonl --verbose
    python -m ao3kit job start --dir $XDG_STATE_HOME/fanfic-organizer/jobs/<id>
    python -m ao3kit job list
    python -m ao3kit job status [id]
    python -m ao3kit job log <id> [--follow]
    python -m ao3kit job stop <id>
    python -m ao3kit job retry <id>
    python -m ao3kit job delete <id>
    python -m ao3kit job clear --finished
    python -m ao3kit job attach <id>

``tags warm`` stays a named singleton (id ``warm``) and appears in ``job list``.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ao3kit.proc import (
    ao3kit_argv,
    DEFAULT_LOG_LINES,
    atomic_write_json,
    clear_pid,
    follow_log,
    last_log_line,
    pid_is_alive,
    read_json,
    read_log_tail,
    read_pid,
    running_pid,
    spawn_daemon,
    stop_process,
    terminate_process,
    utc_now,
    write_pid,
)

WARM_JOB_ID = "warm"
_PROGRESS_RE = re.compile(r"\[(\d+)/(\d+)\]")
_UNIQUE_TAGS_RE = re.compile(
    r"(\d+) unique tags across batch \((\d+) already cached, (\d+) need AO3"
)
_SECRET_FLAGS = frozenset({"--password", "-p"})


def redact_argv(argv: list[str]) -> list[str]:
    """Replace password values so they never appear in job logs."""
    redacted: list[str] = []
    hide_next = False
    for part in argv:
        if hide_next:
            redacted.append("***")
            hide_next = False
            continue
        redacted.append(str(part))
        if str(part) in _SECRET_FLAGS:
            hide_next = True
    return redacted


def default_jobs_dir() -> Path:
    env = os.environ.get("AO3KIT_JOBS_DIR", "").strip()
    if env:
        return Path(env).expanduser().resolve()
    from ao3kit.paths import jobs_dir

    return jobs_dir()


def new_job_id(kind: str = "job") -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    slug = re.sub(r"[^a-z0-9]+", "-", (kind or "job").lower()).strip("-")[:24]
    slug = slug or "job"
    return f"{stamp}-{slug}-{uuid.uuid4().hex[:6]}"


def job_dir_for(job_id: str, jobs_dir: Path | None = None) -> Path:
    return (jobs_dir or default_jobs_dir()) / job_id


def job_paths(job_dir: Path) -> dict[str, Path]:
    return {
        "dir": job_dir,
        "spec": job_dir / "spec.json",
        "status": job_dir / "status.json",
        "pid": job_dir / "job.pid",
        "log": job_dir / "job.log",
        "work": job_dir / "work",
    }


@dataclass
class JobSpec:
    id: str
    title: str = ""
    kind: str = "job"
    steps: list[list[str]] = field(default_factory=list)
    created_at: str | None = None
    cwd: str | None = None
    plugin: dict[str, Any] = field(default_factory=dict)
    result: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> JobSpec:
        data = dict(data or {})
        steps = data.get("steps") or []
        cleaned: list[list[str]] = []
        if isinstance(steps, list):
            for step in steps:
                if isinstance(step, list) and step:
                    cleaned.append([str(part) for part in step])
        plugin = data.get("plugin")
        result = data.get("result")
        return cls(
            id=str(data.get("id") or ""),
            title=str(data.get("title") or ""),
            kind=str(data.get("kind") or "job"),
            steps=cleaned,
            created_at=data.get("created_at"),
            cwd=data.get("cwd"),
            plugin=plugin if isinstance(plugin, dict) else {},
            result=result if isinstance(result, dict) else {},
        )


@dataclass
class JobStatus:
    id: str = ""
    title: str = ""
    kind: str = "job"
    running: bool = False
    pid: int | None = None
    started_at: str | None = None
    updated_at: str | None = None
    finished_at: str | None = None
    exit_code: int | None = None
    step_index: int = 0
    step_count: int = 0
    message: str = ""
    progress: list[int] | None = None
    log_path: str | None = None
    ingest: str = "none"
    ingest_error: str | None = None
    notified: bool = False
    result: str = ""
    result_value: Any = None
    retry_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> JobStatus:
        data = dict(data or {})
        known = set(cls.__dataclass_fields__)
        payload = {key: value for key, value in data.items() if key in known}
        progress = payload.get("progress")
        if isinstance(progress, (list, tuple)) and len(progress) >= 2:
            try:
                payload["progress"] = [int(progress[0]), int(progress[1])]
            except (TypeError, ValueError):
                payload["progress"] = None
        else:
            payload["progress"] = None
        try:
            payload["retry_count"] = int(payload.get("retry_count") or 0)
        except (TypeError, ValueError):
            payload["retry_count"] = 0
        return cls(**payload)


def load_spec(path: Path) -> JobSpec | None:
    data = read_json(path)
    if data is None:
        return None
    spec = JobSpec.from_dict(data)
    return spec if spec.id else None


def save_spec(path: Path, spec: JobSpec) -> None:
    atomic_write_json(path, spec.to_dict())


def write_status(path: Path, status: JobStatus) -> None:
    status.updated_at = utc_now()
    atomic_write_json(path, status.to_dict())


def read_status(path: Path) -> JobStatus | None:
    data = read_json(path)
    if data is None:
        return None
    return JobStatus.from_dict(data)



_RESULT_OUTPUT_FLAGS = ("-o", "--jsonl", "--output")


def _flag_value(argv: list[str], flag: str) -> str | None:
    try:
        index = argv.index(flag)
    except ValueError:
        return None
    if index + 1 >= len(argv):
        return None
    value = str(argv[index + 1])
    if value.startswith("-"):
        return None
    return value


def infer_result_spec(steps: list[list[str]]) -> dict[str, Any]:
    """Default result: JSONL count from ``-o`` / ``--jsonl``, else last log line."""
    for step in reversed(steps or []):
        if not step:
            continue
        if step[0] == "download":
            dest = _flag_value(step, "-d") or _flag_value(step, "--dir")
            inp = _flag_value(step, "-i") or _flag_value(step, "--input")
            path = str(Path(dest) / "results.jsonl") if dest else inp
            if path:
                return {
                    "source": "jsonl_count",
                    "path": path,
                    "field": "epub_file",
                    "label": "EPUB",
                }
        for flag in _RESULT_OUTPUT_FLAGS:
            path = _flag_value(step, flag)
            if path:
                return {"source": "jsonl_count", "path": path, "label": "work"}
    return {"source": "last_log"}


def normalize_result_spec(data: Any) -> dict[str, Any]:
    if not isinstance(data, dict) or not data:
        return {"source": "last_log"}
    source = str(data.get("source") or "last_log").strip() or "last_log"
    spec = {"source": source}
    for key in ("path", "field", "pattern", "label", "template"):
        value = data.get(key)
        if value not in (None, ""):
            spec[key] = str(value)
    return spec


def jsonl_count_result(
    path: str | Path, *, label: str = "work", field: str = ""
) -> dict[str, str]:
    spec = {"source": "jsonl_count", "path": str(path), "label": label}
    if field:
        spec["field"] = field
    return spec


def _plural_count(count: int, label: str) -> str:
    label = (label or "item").strip() or "item"
    if count == 1:
        return f"1 {label}"
    if label.endswith(("s", "S")):
        return f"{count} {label}"
    return f"{count} {label}s"


def _resolve_result_path(job_dir: Path, path: str) -> Path:
    raw = Path(path)
    if raw.is_absolute():
        return raw
    return Path(job_dir) / raw


def count_jsonl_records(path: Path, *, field: str = "") -> int:
    if not path.is_file():
        return 0
    count = 0
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            text = line.strip()
            if not text:
                continue
            if not field:
                count += 1
                continue
            try:
                record = json.loads(text)
            except json.JSONDecodeError:
                continue
            if isinstance(record, dict) and record.get(field):
                count += 1
    return count


def _format_count(count: int, spec: dict[str, Any]) -> str:
    template = str(spec.get("template") or "")
    label = str(spec.get("label") or "item")
    if template:
        try:
            return template.format(count=count, label=label)
        except (KeyError, IndexError, ValueError):
            pass
    return _plural_count(count, label)


def _result_last_log(log_path: Path | None) -> tuple[str, Any]:
    if log_path is None or not Path(log_path).is_file():
        return "", None
    text = read_log_tail(Path(log_path), lines=80)
    for line in reversed(text.splitlines()):
        stripped = line.strip()
        if not stripped or stripped.startswith("---") or stripped[:1] in "{[":
            continue
        return stripped[:200], stripped
    return "", None


def evaluate_job_result(
    spec: dict[str, Any] | None,
    job_dir: Path,
    *,
    log_path: Path | None = None,
    exit_code: int | None = None,
) -> tuple[str, Any]:
    """Compute the return value this job declared in ``spec.result``.

    Sources:
      ``jsonl_count`` — count JSONL rows (optional ``field`` must be truthy)
      ``log_match`` — last regex match in the log (``pattern``)
      ``json_field`` — dotted field from a JSON file
      ``last_log`` — last useful log line (default)
      ``none`` — empty
    """
    if exit_code == 130:
        return "Stopped", None
    spec = normalize_result_spec(spec)
    source = spec["source"]
    if source == "none":
        return "", None
    job_dir = Path(job_dir)
    resolved_log = Path(log_path) if log_path is not None else job_dir / "job.log"
    if source == "jsonl_count":
        path_str = spec.get("path") or ""
        if not path_str:
            return "", None
        path = _resolve_result_path(job_dir, path_str)
        if not path.is_file():
            return "", None
        count = count_jsonl_records(path, field=str(spec.get("field") or ""))
        return _format_count(count, spec), count
    if source == "json_field":
        path_str = spec.get("path") or ""
        if not path_str:
            return "", None
        path = _resolve_result_path(job_dir, path_str)
        data = read_json(path) or {}
        value: Any = data
        for part in str(spec.get("field") or "").split("."):
            if not part:
                continue
            if not isinstance(value, dict):
                value = None
                break
            value = value.get(part)
        if value is None:
            return "", None
        if isinstance(value, (dict, list)):
            text = json.dumps(value, ensure_ascii=False)
        else:
            text = str(value)
        template = str(spec.get("template") or "")
        if template:
            try:
                return template.format(value=value, count=value)[:200], value
            except (KeyError, IndexError, ValueError, TypeError):
                pass
        return text[:200], value
    if source == "log_match":
        pattern = str(spec.get("pattern") or "")
        if not pattern:
            return _result_last_log(resolved_log)
        try:
            compiled = re.compile(pattern)
        except re.error:
            return _result_last_log(resolved_log)
        text = read_log_tail(resolved_log, lines=400) if resolved_log.is_file() else ""
        match = None
        for found in compiled.finditer(text):
            match = found
        if match is None:
            return "", None
        raw = match.group(1) if match.lastindex else match.group(0)
        template = str(spec.get("template") or "")
        if template:
            try:
                return template.format(value=raw)[:200], raw
            except (KeyError, IndexError, ValueError, TypeError):
                pass
        return str(raw)[:200], raw
    return _result_last_log(resolved_log)


def apply_job_result(status: JobStatus, spec: JobSpec, job_dir: Path) -> None:
    """Fill ``status.result`` from the job's result spec.

    Calibre ingest may overwrite ``result`` after the process exits; leave that
    in place once ``ingest`` is ``done``.
    """
    if status.ingest == "done" and status.result:
        return
    display, value = evaluate_job_result(
        spec.result,
        job_dir,
        log_path=Path(status.log_path) if status.log_path else Path(job_dir) / "job.log",
        exit_code=status.exit_code,
    )
    if display:
        status.result = display
        status.result_value = value
        return
    if status.exit_code not in (0, None, 130) and not status.result:
        status.result = (status.message or f"Failed (exit {status.exit_code})")[:200]


def live_job_status(job_dir: Path) -> JobStatus:
    paths = job_paths(job_dir)
    status = read_status(paths["status"]) or JobStatus(id=job_dir.name)
    spec = load_spec(paths["spec"])
    if spec is not None:
        status.id = status.id or spec.id
        status.title = status.title or spec.title
        status.kind = status.kind or spec.kind
        if not status.step_count:
            status.step_count = len(spec.steps)
    pid = running_pid(paths["pid"])
    status.running = pid is not None
    if pid is not None:
        status.pid = pid
    elif status.running:
        status.running = False
    if not status.log_path:
        status.log_path = str(paths["log"])
    if not status.id:
        status.id = job_dir.name
    if spec is not None:
        apply_job_result(status, spec, job_dir)
    elif not status.result:
        display, value = evaluate_job_result(
            {"source": "last_log"},
            job_dir,
            log_path=paths["log"],
            exit_code=status.exit_code,
        )
        if display:
            status.result = display
            status.result_value = value
    return status


def progress_from_line(message: str) -> list[int] | None:
    unique = _UNIQUE_TAGS_RE.search(message or "")
    if unique:
        need = int(unique.group(3))
        return [0, need if need else int(unique.group(1))]
    match = _PROGRESS_RE.search(message or "")
    if match:
        return [int(match.group(1)), int(match.group(2))]
    return None


def split_steps(tokens: list[str]) -> list[list[str]]:
    """Split argv on ``--and`` into ao3kit command steps."""
    steps: list[list[str]] = []
    current: list[str] = []
    for token in tokens:
        if token == "--and":
            if current:
                steps.append(current)
                current = []
            continue
        current.append(token)
    if current:
        steps.append(current)
    return steps



def _print_json(data: Any) -> None:
    json.dump(data, sys.stdout, ensure_ascii=False, indent=2, default=str)
    sys.stdout.write("\n")


def _warm_paths() -> dict[str, Path]:
    from ao3kit.tags.warm import default_warm_paths

    return default_warm_paths()


def warm_as_job() -> JobStatus | None:
    """Present the tag-cache daemon as job id ``warm`` when its files exist."""
    try:
        from ao3kit.tags.warm import live_status as warm_live
    except Exception:
        return None
    paths = _warm_paths()
    if not any(paths[key].exists() for key in ("status", "pid", "log")):
        return None
    warm = warm_live(pid_path=paths["pid"], status_path=paths["status"])
    progress = progress_from_line(warm.message or "")
    ingest = "none"
    return JobStatus(
        id=WARM_JOB_ID,
        title="Background tag cache",
        kind="tags.warm",
        running=bool(warm.running),
        pid=warm.pid,
        started_at=warm.started_at,
        updated_at=warm.updated_at,
        message=warm.message
        or (
            format_warm_list_line(warm)
            if warm.source_count
            else "Background tag cache"
        ),
        progress=progress,
        log_path=warm.log_path or str(paths["log"]),
        ingest=ingest,
        step_index=int(warm.fetched or 0),
        step_count=int(warm.uncached or 0) + int(warm.fetched or 0),
        result=format_warm_list_line(warm),
        result_value={"cached": int(warm.cached or 0), "source": int(warm.source_count or 0)},
    )


def format_warm_list_line(warm: Any) -> str:
    cached = getattr(warm, "cached", 0)
    source = getattr(warm, "source_count", 0)
    if source:
        return f"{cached}/{source} cached"
    return "Background tag cache"


def list_jobs(jobs_dir: Path | None = None) -> list[JobStatus]:
    root = jobs_dir or default_jobs_dir()
    jobs: list[JobStatus] = []
    if root.is_dir():
        for child in sorted(root.iterdir(), reverse=True):
            if not child.is_dir() or child.name == WARM_JOB_ID:
                continue
            if not (child / "spec.json").is_file() and not (
                child / "status.json"
            ).is_file():
                continue
            jobs.append(live_job_status(child))
    if root.resolve() == default_jobs_dir().resolve():
        warm = warm_as_job()
        if warm is not None:
            jobs.append(warm)
    jobs.sort(key=lambda item: item.started_at or item.updated_at or "", reverse=True)
    running = [item for item in jobs if item.running]
    rest = [item for item in jobs if not item.running]
    return running + rest


def find_job(job_id: str, jobs_dir: Path | None = None) -> Path | None:
    if job_id == WARM_JOB_ID:
        return None
    path = job_dir_for(job_id, jobs_dir)
    if (path / "spec.json").is_file() or (path / "status.json").is_file():
        return path
    return None


def job_is_retryable(status: JobStatus | dict[str, Any] | None) -> bool:
    """True when a finished failed/stopped job can be run again from its spec."""
    if isinstance(status, JobStatus):
        data = status.to_dict()
    else:
        data = dict(status or {})
    if str(data.get("id") or "") == WARM_JOB_ID:
        return False
    if data.get("running"):
        return False
    if str(data.get("ingest") or "") == "pending":
        return False
    ingest = str(data.get("ingest") or "")
    if ingest in ("cancelled", "failed", "skipped"):
        return True
    return data.get("exit_code") not in (None, 0)


def job_is_deletable(status: JobStatus | dict[str, Any] | None) -> bool:
    """True when a job directory can be removed (not running, not ingesting)."""
    if isinstance(status, JobStatus):
        data = status.to_dict()
    else:
        data = dict(status or {})
    if str(data.get("id") or "") == WARM_JOB_ID:
        return False
    if data.get("running"):
        return False
    return str(data.get("ingest") or "") != "pending"


def job_clear_bucket(status: JobStatus | dict[str, Any] | None) -> str | None:
    """``finished``, ``failed``, or ``stopped`` when deletable; otherwise ``None``."""
    if not job_is_deletable(status):
        return None
    if isinstance(status, JobStatus):
        data = status.to_dict()
    else:
        data = dict(status or {})
    ingest = str(data.get("ingest") or "")
    if ingest == "cancelled":
        return "stopped"
    if ingest in ("failed", "skipped") or data.get("exit_code") not in (None, 0):
        return "failed"
    return "finished"


def delete_job(job_id: str, *, jobs_dir: Path | None = None) -> tuple[bool, str | None]:
    """Remove a finished job directory. The Calibre library is not touched."""
    if job_id == WARM_JOB_ID:
        return False, "The tag-cache warmer cannot be deleted; use tags warm stop."
    path = find_job(job_id, jobs_dir)
    if path is None:
        return False, f"Unknown job {job_id!r}"
    live = live_job_status(path)
    if live.running:
        return False, f"Job {job_id} is still running; stop it first."
    if str(live.ingest or "") == "pending":
        return False, f"Job {job_id} is still writing into Calibre."
    try:
        shutil.rmtree(path)
    except OSError as exc:
        return False, f"Could not delete job {job_id}: {exc}"
    return True, None


def delete_jobs(
    job_ids: list[str], *, jobs_dir: Path | None = None
) -> dict[str, Any]:
    deleted: list[str] = []
    errors: list[dict[str, str]] = []
    for raw in job_ids:
        job_id = str(raw or "").strip()
        if not job_id:
            continue
        ok, error = delete_job(job_id, jobs_dir=jobs_dir)
        if ok:
            deleted.append(job_id)
        else:
            errors.append({"id": job_id, "error": error or "Could not delete."})
    return {"deleted": deleted, "errors": errors}


def clear_jobs(
    jobs_dir: Path | None = None,
    *,
    finished: bool = False,
    failed: bool = False,
    stopped: bool = False,
) -> dict[str, Any]:
    wanted: set[str] = set()
    if finished:
        wanted.add("finished")
    if failed:
        wanted.add("failed")
    if stopped:
        wanted.add("stopped")
    deleted: list[str] = []
    errors: list[dict[str, str]] = []
    if not wanted:
        return {"deleted": deleted, "errors": errors}
    for status in list_jobs(jobs_dir):
        bucket = job_clear_bucket(status)
        if bucket is None or bucket not in wanted:
            continue
        ok, error = delete_job(status.id, jobs_dir=jobs_dir)
        if ok:
            deleted.append(status.id)
        else:
            errors.append({"id": status.id, "error": error or "Could not delete."})
    return {"deleted": deleted, "errors": errors}


def cmd_delete(args: argparse.Namespace) -> int:
    jobs_dir = (
        Path(args.jobs_dir).expanduser().resolve() if args.jobs_dir else None
    )
    ids = [str(item).strip() for item in (args.job_ids or []) if str(item).strip()]
    if not ids:
        print("Provide at least one job id.", file=sys.stderr)
        return 2
    result = delete_jobs(ids, jobs_dir=jobs_dir)
    _print_json(result)
    for row in result["errors"]:
        print(row["error"], file=sys.stderr)
    if result["deleted"]:
        print(
            f"Deleted {len(result['deleted'])} job(s).",
            file=sys.stderr,
        )
    if not result["deleted"] and result["errors"]:
        first = result["errors"][0]["error"].lower()
        if "unknown" in first or "warmer" in first:
            return 2
        return 1
    return 1 if result["errors"] else 0


def cmd_clear(args: argparse.Namespace) -> int:
    jobs_dir = (
        Path(args.jobs_dir).expanduser().resolve() if args.jobs_dir else None
    )
    finished = bool(args.finished or args.inactive)
    failed = bool(args.failed or args.inactive)
    stopped = bool(args.stopped or args.inactive)
    if not (finished or failed or stopped):
        print(
            "Specify --finished, --failed, --stopped, or --inactive.",
            file=sys.stderr,
        )
        return 2
    result = clear_jobs(
        jobs_dir,
        finished=finished,
        failed=failed,
        stopped=stopped,
    )
    _print_json(result)
    for row in result["errors"]:
        print(row["error"], file=sys.stderr)
    print(
        f"Deleted {len(result['deleted'])} job(s).",
        file=sys.stderr,
    )
    return 1 if result["errors"] else 0


def format_status_text(status: JobStatus) -> str:
    bits = [status.title or status.id or "job"]
    if status.running:
        bits.append(f"running (pid {status.pid})" if status.pid else "running")
    elif status.exit_code is not None:
        bits.append(f"exit {status.exit_code}")
    else:
        bits.append("not running")
    if status.result:
        bits.append(status.result)
    elif status.message:
        bits.append(status.message)
    return " — ".join(bits)


def _plugin_ingest_state(spec: JobSpec, exit_code: int | None) -> str:
    action = str((spec.plugin or {}).get("action") or "").strip()
    if not action or action == "none":
        return "none"
    if exit_code == 130:
        return "cancelled"
    if exit_code not in (0, None):
        # Attach whatever EPUBs landed before the download step failed.
        if action == "attach_epubs":
            return "pending"
        return "skipped"
    return "pending"


def run_job_dir(job_dir: Path) -> int:
    paths = job_paths(job_dir)
    spec = load_spec(paths["spec"])
    if spec is None:
        print(f"No job spec at {paths['spec']}", file=sys.stderr)
        return 2
    paths["work"].mkdir(parents=True, exist_ok=True)
    write_pid(paths["pid"], os.getpid())
    stop = {"flag": False}
    child: dict[str, subprocess.Popen | None] = {"proc": None}

    def _handle(signum: int, frame: Any) -> None:
        stop["flag"] = True
        proc = child["proc"]
        if proc is not None:
            terminate_process(proc)

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            signal.signal(sig, _handle)
        except (OSError, ValueError):
            pass

    started = utc_now()
    previous = read_status(paths["status"])
    retry_count = int(previous.retry_count or 0) if previous is not None else 0
    status = JobStatus(
        id=spec.id,
        title=spec.title,
        kind=spec.kind,
        running=True,
        pid=os.getpid(),
        started_at=started,
        step_count=len(spec.steps),
        log_path=str(paths["log"]),
        ingest=_plugin_ingest_state(spec, None),
        message=f"Starting {spec.title or spec.kind}…",
        retry_count=retry_count,
    )
    write_status(paths["status"], status)

    def refresh_from_log(message: str | None = None) -> None:
        line = message or last_log_line(paths["log"]) or status.message
        status.message = line
        parsed = progress_from_line(line)
        if parsed is not None:
            status.progress = parsed
        write_status(paths["status"], status)

    exit_code = 0
    try:
        if not spec.steps:
            paths["log"].parent.mkdir(parents=True, exist_ok=True)
            with paths["log"].open("a", encoding="utf-8") as handle:
                handle.write("No AO3 steps; ready for Calibre ingest.\n")
            refresh_from_log("No AO3 steps; ready for Calibre ingest.")
        for index, step in enumerate(spec.steps, start=1):
            if stop["flag"]:
                exit_code = 130
                break
            status.step_index = index
            shown = redact_argv(list(step))
            banner = f"Step {index}/{len(spec.steps)}: {' '.join(shown[:8])}"
            paths["log"].parent.mkdir(parents=True, exist_ok=True)
            with paths["log"].open("a", encoding="utf-8") as handle:
                handle.write(f"\n--- {banner} ---\n")
            refresh_from_log(banner)
            argv = ao3kit_argv(step)
            log_handle = open(paths["log"], "a", encoding="utf-8")
            popen_kwargs: dict[str, Any] = {
                "stdin": subprocess.DEVNULL,
                "stdout": log_handle,
                "stderr": subprocess.STDOUT,
                "cwd": spec.cwd or str(Path.cwd()),
                "env": {**os.environ, "PYTHONUNBUFFERED": "1"},
            }
            if os.name != "nt":
                popen_kwargs["start_new_session"] = True
            try:
                proc = subprocess.Popen(argv, **popen_kwargs)
            except OSError as exc:
                log_handle.close()
                exit_code = 1
                refresh_from_log(f"Failed to start step: {exc}")
                break
            child["proc"] = proc
            try:
                while proc.poll() is None:
                    if stop["flag"]:
                        terminate_process(proc)
                        break
                    time.sleep(0.4)
                    refresh_from_log()
                code = proc.wait()
            finally:
                child["proc"] = None
                log_handle.close()
            if stop["flag"]:
                exit_code = 130
                refresh_from_log("Stopped.")
                break
            if code:
                exit_code = int(code)
                refresh_from_log(f"Step failed (exit {code}).")
                break
            refresh_from_log()
    finally:
        status.running = False
        status.pid = None
        status.finished_at = utc_now()
        status.exit_code = exit_code
        status.ingest = _plugin_ingest_state(spec, exit_code)
        if stop["flag"] and exit_code == 130:
            status.ingest = "cancelled"
            if not status.message:
                status.message = "Stopped."
        elif exit_code == 0 and not status.message:
            status.message = "Finished."
        apply_job_result(status, spec, job_dir)
        write_status(paths["status"], status)
        clear_pid(paths["pid"])
    return 0 if exit_code == 0 else exit_code


def cmd_run(args: argparse.Namespace) -> int:
    job_dir = Path(args.dir).expanduser().resolve()
    code = run_job_dir(job_dir)
    status = live_job_status(job_dir)
    _print_json(status.to_dict())
    print(format_status_text(status), file=sys.stderr)
    return code


def _run_argv(job_dir: Path) -> list[str]:
    return ao3kit_argv(["job", "run", "--dir", str(job_dir)])


def start_job(
    spec: JobSpec,
    *,
    jobs_dir: Path | None = None,
    foreground: bool = False,
    cwd: Path | None = None,
) -> tuple[JobStatus, str | None]:
    """Write spec and spawn ``job run``. Returns ``(status, error)``."""
    root = jobs_dir or default_jobs_dir()
    if not spec.id:
        spec.id = new_job_id(spec.kind)
    if not spec.created_at:
        spec.created_at = utc_now()
    job_dir = root / spec.id
    paths = job_paths(job_dir)
    paths["work"].mkdir(parents=True, exist_ok=True)
    if spec.cwd is None and cwd is not None:
        spec.cwd = str(cwd)
    save_spec(paths["spec"], spec)
    if foreground:
        run_job_dir(job_dir)
        return live_job_status(job_dir), None
    pid, error = spawn_daemon(
        _run_argv(job_dir),
        log_path=paths["log"],
        pid_path=paths["pid"],
        cwd=Path(spec.cwd) if spec.cwd else Path.cwd(),
        env={**os.environ, "PYTHONUNBUFFERED": "1"},
    )
    if error:
        return live_job_status(job_dir), error
    status = live_job_status(job_dir)
    if not status.running:
        status.running = True
        status.pid = pid
        status.started_at = status.started_at or utc_now()
        status.log_path = str(paths["log"])
        status.message = status.message or f"Started {spec.title or spec.id} (pid {pid})."
        status.ingest = _plugin_ingest_state(spec, None)
        status.notified = False
        status.ingest_error = None
        status.finished_at = None
        status.exit_code = None
        write_status(paths["status"], status)
    return status, None


def reset_job_for_retry(job_dir: Path, spec: JobSpec) -> JobStatus:
    """Clear finish/ingest flags and append a retry banner. Keeps spec, work/, and log."""
    paths = job_paths(job_dir)
    previous = read_status(paths["status"]) or JobStatus(id=spec.id or job_dir.name)
    attempt = int(previous.retry_count or 0) + 1
    status = JobStatus(
        id=spec.id or job_dir.name,
        title=spec.title or previous.title,
        kind=spec.kind or previous.kind,
        running=False,
        retry_count=attempt,
        log_path=str(paths["log"]),
        ingest="none",
        notified=False,
        ingest_error=None,
        message=f"Retry {attempt}…",
        result="",
        result_value=None,
        exit_code=None,
        finished_at=None,
        started_at=None,
        step_index=0,
        step_count=len(spec.steps),
        progress=None,
    )
    write_status(paths["status"], status)
    paths["log"].parent.mkdir(parents=True, exist_ok=True)
    with paths["log"].open("a", encoding="utf-8") as handle:
        handle.write(f"\n--- retry {attempt} {utc_now()} ---\n")
    return status


def retry_job(
    job_id: str,
    *,
    jobs_dir: Path | None = None,
    foreground: bool = False,
) -> tuple[JobStatus, str | None]:
    """Re-run a finished job from its spec. Work files and the log are kept."""
    if job_id == WARM_JOB_ID:
        return (
            JobStatus(id=WARM_JOB_ID, title="Background tag cache", kind="tags.warm"),
            "The tag-cache warmer is a singleton; use tags warm start, not job retry.",
        )
    path = find_job(job_id, jobs_dir)
    if path is None:
        return JobStatus(id=job_id), f"Unknown job {job_id!r}"
    live = live_job_status(path)
    if live.running:
        return live, f"Job {job_id} is already running."
    if str(live.ingest or "") == "pending":
        return live, (
            f"Job {job_id} is still writing into Calibre; wait until that finishes."
        )
    spec = load_spec(path / "spec.json")
    if spec is None:
        return live, f"No job spec at {path / 'spec.json'}"
    spec.id = path.name
    reset_job_for_retry(path, spec)
    return start_job(
        spec,
        jobs_dir=path.parent,
        foreground=foreground,
        cwd=Path(spec.cwd) if spec.cwd else Path.cwd(),
    )


def cmd_retry(args: argparse.Namespace) -> int:
    jobs_dir = (
        Path(args.jobs_dir).expanduser().resolve() if args.jobs_dir else None
    )
    status, error = retry_job(
        str(args.job_id).strip(),
        jobs_dir=jobs_dir,
        foreground=bool(getattr(args, "foreground", False)),
    )
    if error:
        print(error, file=sys.stderr)
        _print_json(status.to_dict())
        lowered = error.lower()
        if "unknown" in lowered or "warmer" in lowered or "no job spec" in lowered:
            return 2
        return 1
    _print_json(status.to_dict())
    print(format_status_text(status), file=sys.stderr)
    return 0


def cmd_start(args: argparse.Namespace) -> int:
    jobs_dir = Path(args.jobs_dir).expanduser().resolve() if args.jobs_dir else default_jobs_dir()
    if args.dir:
        job_dir = Path(args.dir).expanduser().resolve()
        spec = load_spec(job_dir / "spec.json")
        if spec is None:
            print(f"No job spec at {job_dir / 'spec.json'}", file=sys.stderr)
            return 2
        spec.id = job_dir.name
        live = running_pid(job_dir / "job.pid")
        if live is not None:
            status = live_job_status(job_dir)
            status.message = f"Already running (pid {live})."
            _print_json(status.to_dict())
            print(status.message, file=sys.stderr)
            return 0
        status, error = start_job(
            spec,
            jobs_dir=job_dir.parent,
            foreground=bool(args.foreground),
            cwd=Path(spec.cwd) if spec.cwd else Path.cwd(),
        )
        if error:
            print(error, file=sys.stderr)
            return 1
        _print_json(status.to_dict())
        print(format_status_text(status), file=sys.stderr)
        return 0

    steps = split_steps(list(args.command or []))
    if not steps:
        print("Provide an ao3kit command after -- (example: job start -- scrape -o out.jsonl)", file=sys.stderr)
        return 2
    kind = str(args.kind or (steps[0][0] if steps and steps[0] else "job"))
    result = _parse_plugin_json(getattr(args, "result_json", None))
    if not result:
        result = infer_result_spec(steps)
    spec = JobSpec(
        id=str(args.id or new_job_id(kind)),
        title=str(args.title or " ".join(steps[0][:4])),
        kind=kind,
        steps=steps,
        created_at=utc_now(),
        cwd=str(Path.cwd()),
        plugin=_parse_plugin_json(args.plugin_json),
        result=result,
    )
    status, error = start_job(
        spec,
        jobs_dir=jobs_dir,
        foreground=bool(args.foreground),
    )
    if error:
        print(error, file=sys.stderr)
        return 1
    _print_json(status.to_dict())
    print(format_status_text(status), file=sys.stderr)
    return 0


def _parse_plugin_json(raw: str | None) -> dict[str, Any]:
    text = (raw or "").strip()
    if not text:
        return {}
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def cmd_list(args: argparse.Namespace) -> int:
    jobs_dir = Path(args.jobs_dir).expanduser().resolve() if args.jobs_dir else None
    jobs = list_jobs(jobs_dir)
    _print_json([item.to_dict() for item in jobs])
    if not jobs:
        print("No jobs.", file=sys.stderr)
        return 0
    for item in jobs:
        print(f"{item.id}\t{format_status_text(item)}", file=sys.stderr)
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    job_id = str(args.job_id or "").strip()
    jobs_dir = Path(args.jobs_dir).expanduser().resolve() if args.jobs_dir else None
    if not job_id:
        return cmd_list(args)
    if job_id == WARM_JOB_ID:
        status = warm_as_job() or JobStatus(id=WARM_JOB_ID, title="Background tag cache", kind="tags.warm")
        _print_json(status.to_dict())
        print(format_status_text(status), file=sys.stderr)
        return 0
    path = find_job(job_id, jobs_dir)
    if path is None:
        print(f"Unknown job {job_id!r}", file=sys.stderr)
        return 2
    status = live_job_status(path)
    _print_json(status.to_dict())
    print(format_status_text(status), file=sys.stderr)
    return 0


def cmd_log(args: argparse.Namespace) -> int:
    job_id = str(args.job_id).strip()
    jobs_dir = Path(args.jobs_dir).expanduser().resolve() if args.jobs_dir else None
    log_path = _log_path_for(job_id, jobs_dir)
    if log_path is None:
        print(f"Unknown job {job_id!r}", file=sys.stderr)
        return 2
    line_count = int(getattr(args, "lines", DEFAULT_LOG_LINES))
    if getattr(args, "follow", False):
        try:
            return follow_log(log_path, lines=line_count)
        except KeyboardInterrupt:
            return 0
    if not log_path.is_file():
        print(f"No log yet ({log_path})", file=sys.stderr)
        return 0
    sys.stdout.write(read_log_tail(log_path, lines=line_count))
    return 0


def _log_path_for(job_id: str, jobs_dir: Path | None) -> Path | None:
    if job_id == WARM_JOB_ID:
        return _warm_paths()["log"]
    path = find_job(job_id, jobs_dir)
    if path is None:
        return None
    return job_paths(path)["log"]


def cmd_stop(args: argparse.Namespace) -> int:
    job_id = str(args.job_id).strip()
    jobs_dir = Path(args.jobs_dir).expanduser().resolve() if args.jobs_dir else None
    if job_id == WARM_JOB_ID:
        from ao3kit.tags.warm import cmd_stop as warm_stop

        warm_args = argparse.Namespace(
            cache=None,
            pid_file=None,
            status_file=None,
            log_file=None,
            job_file=None,
        )
        return warm_stop(warm_args)
    path = find_job(job_id, jobs_dir)
    if path is None:
        print(f"Unknown job {job_id!r}", file=sys.stderr)
        return 2
    paths = job_paths(path)
    pid = read_pid(paths["pid"])
    was_alive = pid is not None and pid_is_alive(pid)
    stopped, message = stop_process(paths["pid"], noun=f"job {job_id}")
    status = live_job_status(path)
    status.message = message
    if was_alive and not status.running:
        status.pid = None
        status.finished_at = status.finished_at or utc_now()
        if status.exit_code is None:
            status.exit_code = 130
        if status.ingest == "pending":
            status.ingest = "cancelled"
        status.result = "Stopped"
        status.result_value = None
    write_status(paths["status"], status)
    _print_json(status.to_dict())
    print(message, file=sys.stderr)
    return 0 if (stopped or not status.running) else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run any ao3kit command as a detachable background job.",
    )
    sub = parser.add_subparsers(dest="job_cmd", required=True)

    def add_jobs_dir(target: argparse.ArgumentParser) -> None:
        target.add_argument(
            "--jobs-dir",
            default=None,
            help="Job store directory (default: XDG state dir / jobs, or AO3KIT_JOBS_DIR)",
        )

    start_p = sub.add_parser("start", help="Start a detached job")
    add_jobs_dir(start_p)
    start_p.add_argument("--title", default="")
    start_p.add_argument("--kind", default="")
    start_p.add_argument("--id", default="")
    start_p.add_argument(
        "--dir",
        default="",
        help="Existing job directory with spec.json (plugin path)",
    )
    start_p.add_argument(
        "--plugin-json",
        default="",
        help="Opaque JSON for Calibre ingest (written onto spec.plugin)",
    )
    start_p.add_argument(
        "--result-json",
        default="",
        help='How to compute the job result, e.g. {"source":"jsonl_count","path":"out.jsonl"}',
    )
    start_p.add_argument(
        "--foreground",
        action="store_true",
        help="Run in this process (tests / debugging)",
    )
    start_p.add_argument(
        "argv",
        nargs=argparse.REMAINDER,
        help="ao3kit argv after -- ; separate steps with --and",
    )

    run_p = sub.add_parser("run", help="Job worker (spawned by start)")
    run_p.add_argument("--dir", required=True, help="Job directory")
    add_jobs_dir(run_p)

    list_p = sub.add_parser("list", help="List jobs (JSON on stdout)")
    add_jobs_dir(list_p)
    status_p = sub.add_parser("status", help="Show one job or list all")
    add_jobs_dir(status_p)
    status_p.add_argument("job_id", nargs="?", default="")
    log_p = sub.add_parser("log", help="Show or follow a job log")
    add_jobs_dir(log_p)
    log_p.add_argument("job_id")
    log_p.add_argument("--lines", type=int, default=DEFAULT_LOG_LINES)
    log_p.add_argument("--follow", action="store_true")
    attach_p = sub.add_parser("attach", help="Follow a job log (alias of log --follow)")
    add_jobs_dir(attach_p)
    attach_p.add_argument("job_id")
    attach_p.add_argument("--lines", type=int, default=DEFAULT_LOG_LINES)
    stop_p = sub.add_parser("stop", help="Stop a running job")
    add_jobs_dir(stop_p)
    stop_p.add_argument("job_id")
    retry_p = sub.add_parser(
        "retry",
        help="Re-run a finished job from its spec (keeps work/ cache and log)",
    )
    add_jobs_dir(retry_p)
    retry_p.add_argument("job_id")
    retry_p.add_argument(
        "--foreground",
        action="store_true",
        help="Run in this process (tests / debugging)",
    )
    delete_p = sub.add_parser(
        "delete",
        help="Delete finished job directories (library is unchanged)",
    )
    add_jobs_dir(delete_p)
    delete_p.add_argument("job_ids", nargs="+", help="Job id(s) to delete")
    clear_p = sub.add_parser(
        "clear",
        help="Delete finished/failed/stopped jobs from the store",
    )
    add_jobs_dir(clear_p)
    clear_p.add_argument(
        "--finished",
        action="store_true",
        help="Remove successful completed jobs",
    )
    clear_p.add_argument(
        "--failed",
        action="store_true",
        help="Remove failed jobs",
    )
    clear_p.add_argument(
        "--stopped",
        action="store_true",
        help="Remove cancelled/stopped jobs",
    )
    clear_p.add_argument(
        "--inactive",
        action="store_true",
        help="Remove all finished, failed, and stopped jobs",
    )

    args = parser.parse_args(list(sys.argv[1:] if argv is None else argv))
    command = args.job_cmd
    if command == "start":
        remainder = list(getattr(args, "argv", []) or [])
        if remainder[:1] == ["--"]:
            remainder = remainder[1:]
        args.command = remainder
        return cmd_start(args)
    if command == "run":
        return cmd_run(args)
    if command == "list":
        return cmd_list(args)
    if command == "status":
        return cmd_status(args)
    if command == "log":
        return cmd_log(args)
    if command == "attach":
        args.follow = True
        return cmd_log(args)
    if command == "stop":
        return cmd_stop(args)
    if command == "retry":
        return cmd_retry(args)
    if command == "delete":
        return cmd_delete(args)
    if command == "clear":
        return cmd_clear(args)
    parser.error(f"Unknown command: {command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
