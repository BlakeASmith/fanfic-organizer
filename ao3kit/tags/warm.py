"""Background tag-cache warmer.

Fills the XDG tag-cache SQLite file by resolving uncached names through
``TagResolver`` (same AO3 fetch path as ``tags enrich``). Extra sleep between
fetches keeps this process from hogging the host-wide rate limiter, so Search /
Download / Simplify can still run.

CLI::

    python -m ao3kit tags warm start --jsonl results.jsonl
    python -m ao3kit tags warm status
    python -m ao3kit tags warm log
    python -m ao3kit tags warm log --follow
    python -m ao3kit tags warm stop

``start`` detaches a daemon that re-reads its job file each cycle, so a later
``start`` can point at a new JSONL / names file without a second process.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Protocol

from ao3kit.proc import (
    DEFAULT_LOG_LINES,
    ao3kit_argv,
    clear_pid,
    follow_log,
    interruptible_sleep,
    pid_is_alive,
    read_log_tail,
    running_pid,
    spawn_daemon,
    stop_process,
    utc_now,
    write_pid,
)
from ao3kit.tags.cache import TagCache, default_tag_cache_path
from ao3kit.tags.clean import collect_unique_tag_names

StatusCallback = Callable[[str], None]
SleepFn = Callable[[float], None]
StopFn = Callable[[], bool]

DEFAULT_WARM_INTERVAL = 10.0
DEFAULT_POLL_INTERVAL = 60.0
DEFAULT_IDLE_EXIT_POLLS = 3
MAX_FETCH_ATTEMPTS = 3
EXTRA_NAME_KEYS = ("relationships", "characters")


class _Resolver(Protocol):
    cache: TagCache
    persist: bool

    def resolve_one(self, name: str) -> Any: ...

    def close(self) -> None: ...


def default_warm_paths(cache_path: Path | None = None) -> dict[str, Path]:
    """Pid / status / log / job files live beside the tag cache."""
    base = (cache_path or default_tag_cache_path()).expanduser().resolve().parent
    return {
        "pid": base / "tag_warm.pid",
        "status": base / "tag_warm.status.json",
        "log": base / "tag_warm.log",
        "job": base / "tag_warm.job.json",
    }


def _utc_now() -> str:
    return utc_now()


def load_names_file(path: Path) -> list[str]:
    """One tag name per line. Blank lines and ``#`` comments are skipped."""
    names: list[str] = []
    seen: set[str] = set()
    if not path.is_file():
        return names
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line in seen:
            continue
        seen.add(line)
        names.append(line)
    return names


def load_jsonl_records(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    if not path.is_file():
        return records
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line:
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            records.append(data)
    return records


def collect_warm_names(
    *,
    jsonl_paths: list[Path] | None = None,
    names_files: list[Path] | None = None,
    names: list[str] | None = None,
) -> list[str]:
    """Stable-ordered unique names from JSONL works, names files, and argv."""
    seen: set[str] = set()
    ordered: list[str] = []

    def add(name: str) -> None:
        text = str(name).strip()
        if not text or text in seen:
            return
        seen.add(text)
        ordered.append(text)

    for jsonl in jsonl_paths or []:
        records = load_jsonl_records(Path(jsonl))
        for name in collect_unique_tag_names(
            records, include_fandoms=True, extra_keys=EXTRA_NAME_KEYS
        ):
            add(name)

    for names_file in names_files or []:
        for name in load_names_file(Path(names_file)):
            add(name)

    for name in names or []:
        add(name)

    return ordered


def uncached_names(cache: TagCache, names: list[str]) -> list[str]:
    """Names that still need an AO3 fetch (TTL-aware)."""
    missing: list[str] = []
    for name in names:
        if cache.lookup(name) is None:
            missing.append(name)
    return missing


@dataclass
class WarmJob:
    """On-disk spec the daemon re-reads each cycle."""

    jsonl: list[str] = field(default_factory=list)
    names_files: list[str] = field(default_factory=list)
    names: list[str] = field(default_factory=list)
    interval: float = DEFAULT_WARM_INTERVAL
    poll_interval: float = DEFAULT_POLL_INTERVAL
    idle_exit_polls: int = DEFAULT_IDLE_EXIT_POLLS
    cache: str | None = None
    follow_canonical: bool = True
    ttl_days: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> WarmJob:
        data = data or {}
        known = {key for key in cls.__dataclass_fields__}
        return cls(**{key: value for key, value in data.items() if key in known})

    def source_paths(self) -> tuple[list[Path], list[Path]]:
        return [Path(p) for p in self.jsonl], [Path(p) for p in self.names_files]


def load_job(path: Path) -> WarmJob | None:
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    return WarmJob.from_dict(data)


def save_job(path: Path, job: WarmJob) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(job.to_dict(), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    tmp.replace(path)


@dataclass
class WarmStatus:
    running: bool = False
    pid: int | None = None
    started_at: str | None = None
    updated_at: str | None = None
    source_count: int = 0
    cached: int = 0
    uncached: int = 0
    fetched: int = 0
    errors: int = 0
    last_tag: str | None = None
    last_error: str | None = None
    interval_seconds: float = DEFAULT_WARM_INTERVAL
    poll_seconds: float = DEFAULT_POLL_INTERVAL
    idle_polls: int = 0
    message: str = ""
    cache_path: str | None = None
    log_path: str | None = None
    fetched_tags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def write_status(path: Path, status: WarmStatus) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    status.updated_at = _utc_now()
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(status.to_dict(), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    tmp.replace(path)


def read_status(path: Path) -> WarmStatus | None:
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    known = {key for key in WarmStatus.__dataclass_fields__}
    payload = {key: value for key, value in data.items() if key in known}
    tags = payload.get("fetched_tags")
    if not isinstance(tags, list):
        payload["fetched_tags"] = []
    else:
        payload["fetched_tags"] = [str(item) for item in tags if str(item).strip()]
    return WarmStatus(**payload)


def live_status(
    *,
    pid_path: Path,
    status_path: Path,
) -> WarmStatus:
    status = read_status(status_path) or WarmStatus()
    pid = running_pid(pid_path)
    status.running = pid is not None
    if pid is not None:
        status.pid = pid
    elif status.running:
        status.running = False
    return status


def format_status_text(status: WarmStatus) -> str:
    if status.running:
        head = f"Background tag cache: running (pid {status.pid})"
    else:
        head = "Background tag cache: not running"
    bits = [head]
    if status.source_count:
        bits.append(
            f"{status.cached}/{status.source_count} cached, "
            f"{status.uncached} remaining"
        )
    if status.fetched:
        bits.append(f"fetched this run: {status.fetched}")
    if status.errors:
        bits.append(f"errors: {status.errors}")
    if status.last_tag:
        bits.append(f"last tag: {status.last_tag}")
    if status.message:
        bits.append(status.message)
    if status.interval_seconds:
        bits.append(f"pace: {status.interval_seconds:g}s between fetches")
    if status.log_path:
        bits.append(f"log: {status.log_path}")
    if status.fetched_tags:
        bits.append("tags this run:")
        bits.extend(f"  {name}" for name in status.fetched_tags)
    return "\n".join(bits)


def format_stop_report(status: WarmStatus, stopped_line: str) -> str:
    """Human summary after stop, including tags cached this run."""
    lines = [stopped_line.strip() or "Stopped."]
    tags = [name for name in status.fetched_tags if str(name).strip()]
    fetched = int(status.fetched or len(tags))
    bits: list[str] = []
    if fetched:
        bits.append(f"cached {fetched} tag{'s' if fetched != 1 else ''} this run")
    else:
        bits.append("no new tags cached this run")
    if status.uncached:
        bits.append(f"{status.uncached} still remaining")
    if status.errors:
        bits.append(f"{status.errors} error{'s' if status.errors != 1 else ''}")
    lines.append("")
    lines.append("; ".join(bits).capitalize() + ".")
    if tags:
        lines.append("")
        lines.extend(tags)
    return "\n".join(lines).strip()


def stop_daemon(pid_path: Path, *, timeout: float = 10.0) -> tuple[bool, str]:
    return stop_process(pid_path, timeout=timeout, noun="Background tag cache")


def run_warm_loop(
    job: WarmJob,
    resolver: _Resolver,
    *,
    job_path: Path | None = None,
    status_path: Path | None = None,
    on_status: StatusCallback | None = None,
    should_stop: StopFn | None = None,
    sleep_fn: SleepFn | None = None,
    pid: int | None = None,
    log_path: Path | None = None,
    started_at: str | None = None,
) -> WarmStatus:
    """Fetch uncached names until caught up, then idle-poll and exit.

    JSONL / names-file sources are re-read every cycle. If ``job_path`` is
    set, the on-disk spec is reloaded too (a later ``start`` can retarget
    the running daemon). Extra ``job.interval`` sleep happens *after* each
    fetch so the host-wide limiter stays free for other ao3kit work.
    """
    stop = should_stop or (lambda: False)
    pause = sleep_fn or time.sleep
    started = started_at or _utc_now()
    status = WarmStatus(
        running=True,
        pid=pid or os.getpid(),
        started_at=started,
        interval_seconds=job.interval,
        poll_seconds=job.poll_interval,
        cache_path=job.cache,
        log_path=str(log_path) if log_path else None,
    )
    fetched = 0
    errors = 0
    failed: dict[str, int] = {}
    resolved_ok: set[str] = set()
    idle_polls = 0

    def emit(message: str) -> None:
        status.message = message
        if on_status:
            on_status(message)
        if status_path is not None:
            write_status(status_path, status)

    while not stop():
        if job_path is not None:
            loaded = load_job(job_path)
            if loaded is not None:
                job = loaded
        status.interval_seconds = job.interval
        status.poll_seconds = job.poll_interval
        status.cache_path = job.cache
        jsonl_paths, names_files = job.source_paths()
        names = collect_warm_names(
            jsonl_paths=jsonl_paths,
            names_files=names_files,
            names=job.names,
        )
        missing = uncached_names(resolver.cache, names)
        fetchable = [
            name
            for name in missing
            if failed.get(name, 0) < MAX_FETCH_ATTEMPTS and name not in resolved_ok
        ]
        status.source_count = len(names)
        status.cached = len(names) - len(missing)
        status.uncached = len(missing)
        status.fetched = fetched
        status.errors = errors
        status.idle_polls = idle_polls

        if not missing:
            idle_polls += 1
            status.idle_polls = idle_polls
            emit(
                f"Caught up ({len(names)} cached). Idle poll {idle_polls}/"
                f"{job.idle_exit_polls}."
            )
            if idle_polls >= max(1, int(job.idle_exit_polls)):
                emit("Caught up; exiting.")
                break
            interruptible_sleep(job.poll_interval, stop, pause)
            continue

        idle_polls = 0
        if not fetchable:
            errors = max(errors, len(missing))
            status.errors = errors
            emit(f"{len(missing)} uncached tag(s) failed; giving up this run.")
            break

        name = fetchable[0]
        emit(f"Caching tags [{fetched + 1}/{len(missing)}] {name}")
        try:
            resolved = resolver.resolve_one(name)
        except Exception as exc:
            failed[name] = failed.get(name, 0) + 1
            errors += 1
            status.last_tag = name
            status.last_error = str(exc)
            status.errors = errors
            emit(f"Error caching {name}: {exc}")
            interruptible_sleep(job.interval, stop, pause)
            continue

        status.last_tag = name
        status.last_error = None
        error_text = getattr(resolved, "error", None)
        result_status = getattr(resolved, "status", None)
        if result_status == "error" or error_text:
            failed[name] = failed.get(name, 0) + 1
            errors += 1
            status.last_error = str(error_text or result_status)
            status.errors = errors
            emit(
                f"Failed [{failed[name]}/{MAX_FETCH_ATTEMPTS}] {name}: "
                f"{status.last_error}"
            )
        else:
            fetched += 1
            failed.pop(name, None)
            resolved_ok.add(name)
            status.fetched = fetched
            original = getattr(resolved, "original", name)
            mapped = getattr(resolved, "resolved", name)
            line = str(original or name)
            if mapped and mapped != original:
                line = f"{original} → {mapped}"
                if on_status:
                    on_status(f"  {line}  [{result_status}]")
            status.fetched_tags.append(line)
            emit(f"Cached {line}")
            persist_status = result_status if result_status in {
                "canonical",
                "synonym",
                "unmarked",
            } else "synonym"
            if resolver.cache.lookup(name) is None and mapped:
                resolver.cache.remember_alias(
                    name,
                    str(mapped),
                    status=str(persist_status),
                    category=getattr(resolved, "category", None),
                )

        if resolver.persist:
            resolver.cache.save()
        interruptible_sleep(job.interval, stop, pause)

    status.running = False
    status.fetched = fetched
    status.errors = errors
    if status_path is not None:
        write_status(status_path, status)
    return status


def run_warm_loop_from_job_file(
    job_path: Path,
    resolver: _Resolver,
    **kwargs: Any,
) -> WarmStatus:
    """``run_warm_loop`` that re-reads ``job_path`` at the start of each cycle."""
    job = load_job(job_path) or WarmJob()
    return run_warm_loop(job, resolver, job_path=job_path, **kwargs)


def _print_json(data: Any) -> None:
    json.dump(data, sys.stdout, ensure_ascii=False, indent=2, default=str)
    sys.stdout.write("\n")


def _add_source_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--jsonl",
        action="append",
        default=[],
        help="Scrape JSONL to scan for tag names (repeatable)",
    )
    parser.add_argument(
        "--names-file",
        action="append",
        default=[],
        dest="names_files",
        help="Text file with one tag name per line (repeatable)",
    )
    parser.add_argument(
        "tags",
        nargs="*",
        help="Tag names to cache (optional if --jsonl / --names-file used)",
    )


def _add_pace_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--interval",
        type=float,
        default=None,
        help=(
            "Extra seconds to wait after each fetch so other AO3 use is "
            f"not starved (default: config tag_warm_interval or {DEFAULT_WARM_INTERVAL:g})"
        ),
    )
    parser.add_argument(
        "--poll",
        type=float,
        default=DEFAULT_POLL_INTERVAL,
        dest="poll_interval",
        help="Seconds between source re-scans when caught up "
        f"(default: {DEFAULT_POLL_INTERVAL:g})",
    )
    parser.add_argument(
        "--idle-exit",
        type=int,
        default=DEFAULT_IDLE_EXIT_POLLS,
        dest="idle_exit_polls",
        help="Exit after this many consecutive idle polls "
        f"(default: {DEFAULT_IDLE_EXIT_POLLS})",
    )


def _add_path_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--cache",
        type=Path,
        default=None,
        help="Tag cache SQLite path (default: XDG cache dir / fanfic-organizer)",
    )
    parser.add_argument("--pid-file", type=Path, default=None)
    parser.add_argument("--status-file", type=Path, default=None)
    parser.add_argument("--log-file", type=Path, default=None)
    parser.add_argument("--job-file", type=Path, default=None)
    parser.add_argument("--no-follow-canonical", action="store_true")
    parser.add_argument("--cache-ttl-days", type=float, default=None)
    parser.add_argument("--username", help="AO3 username (optional)")
    parser.add_argument("--password", help="AO3 password (optional)")


def _paths_from_args(args: argparse.Namespace) -> dict[str, Path]:
    cache = Path(args.cache) if args.cache else default_tag_cache_path()
    defaults = default_warm_paths(cache)
    return {
        "cache": cache,
        "pid": Path(args.pid_file) if args.pid_file else defaults["pid"],
        "status": Path(args.status_file) if args.status_file else defaults["status"],
        "log": Path(args.log_file) if args.log_file else defaults["log"],
        "job": Path(args.job_file) if args.job_file else defaults["job"],
    }


def _interval_from_args(args: argparse.Namespace) -> float:
    if getattr(args, "interval", None) is not None:
        return max(0.0, float(args.interval))
    try:
        from ao3kit.config import load_user_config

        return max(0.0, float(load_user_config().settings.tag_warm_interval))
    except Exception:
        return DEFAULT_WARM_INTERVAL


def _ttl_from_args(args: argparse.Namespace) -> float | None:
    if getattr(args, "cache_ttl_days", None) is not None:
        return float(args.cache_ttl_days)
    try:
        from ao3kit.config import load_user_config

        return float(load_user_config().settings.tag_cache_ttl_days)
    except Exception:
        from ao3kit.tags.cache import DEFAULT_TAG_CACHE_TTL_DAYS

        return DEFAULT_TAG_CACHE_TTL_DAYS


def job_from_args(args: argparse.Namespace, paths: dict[str, Path]) -> WarmJob:
    jsonl = [str(Path(p).expanduser().resolve()) for p in (args.jsonl or [])]
    names_files = [
        str(Path(p).expanduser().resolve()) for p in (args.names_files or [])
    ]
    return WarmJob(
        jsonl=jsonl,
        names_files=names_files,
        names=[str(t).strip() for t in (args.tags or []) if str(t).strip()],
        interval=_interval_from_args(args),
        poll_interval=max(0.0, float(args.poll_interval)),
        idle_exit_polls=max(1, int(args.idle_exit_polls)),
        cache=str(paths["cache"]),
        follow_canonical=not bool(getattr(args, "no_follow_canonical", False)),
        ttl_days=_ttl_from_args(args),
    )


def _make_resolver(job: WarmJob, args: argparse.Namespace, on_status: StatusCallback | None):
    from ao3kit.tags.metadata import TagResolver

    cache_path = Path(job.cache) if job.cache else default_tag_cache_path()
    return TagResolver(
        username=getattr(args, "username", None),
        password=getattr(args, "password", None),
        delay=0,
        on_status=on_status,
        cache_path=cache_path,
        follow_canonical=job.follow_canonical,
        persist=True,
        ttl_days=job.ttl_days,
    )


def _run_argv(paths: dict[str, Path], args: argparse.Namespace) -> list[str]:
    argv = ao3kit_argv(
        [
            "tags",
            "warm",
            "run",
            "--job-file",
            str(paths["job"]),
            "--pid-file",
            str(paths["pid"]),
            "--status-file",
            str(paths["status"]),
            "--log-file",
            str(paths["log"]),
            "--cache",
            str(paths["cache"]),
        ]
    )
    username = getattr(args, "username", None)
    password = getattr(args, "password", None)
    if username and password:
        argv.extend(["--username", username, "--password", password])
    return argv


def cmd_status(args: argparse.Namespace) -> int:
    paths = _paths_from_args(args)
    status = live_status(pid_path=paths["pid"], status_path=paths["status"])
    if not status.log_path:
        status.log_path = str(paths["log"])
    _print_json(status.to_dict())
    print(format_status_text(status), file=sys.stderr)
    return 0


def cmd_log(args: argparse.Namespace) -> int:
    paths = _paths_from_args(args)
    log_path = paths["log"]
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


def cmd_stop(args: argparse.Namespace) -> int:
    paths = _paths_from_args(args)
    prior = read_status(paths["status"])
    stopped, message = stop_daemon(paths["pid"])
    status = live_status(pid_path=paths["pid"], status_path=paths["status"])
    if prior is not None:
        if len(prior.fetched_tags) > len(status.fetched_tags):
            status.fetched_tags = list(prior.fetched_tags)
        status.fetched = max(int(status.fetched or 0), int(prior.fetched or 0))
        status.errors = max(int(status.errors or 0), int(prior.errors or 0))
        if not status.source_count:
            status.source_count = prior.source_count
            status.cached = prior.cached
            status.uncached = prior.uncached
        if not status.log_path:
            status.log_path = prior.log_path
    status.running = running_pid(paths["pid"]) is not None
    if not status.running:
        status.pid = None
    status.message = format_stop_report(status, message)
    write_status(paths["status"], status)
    _print_json(status.to_dict())
    print(status.message, file=sys.stderr)
    return 0 if (stopped or not status.running) else 1


def cmd_start(args: argparse.Namespace) -> int:
    paths = _paths_from_args(args)
    job = job_from_args(args, paths)
    names = collect_warm_names(
        jsonl_paths=job.source_paths()[0],
        names_files=job.source_paths()[1],
        names=job.names,
    )
    if not names and not job.jsonl and not job.names_files:
        print(
            "Provide --jsonl, --names-file, and/or tag names.",
            file=sys.stderr,
        )
        return 2

    save_job(paths["job"], job)

    cache = TagCache.load(paths["cache"], ttl_days=job.ttl_days)
    try:
        missing = uncached_names(cache, names)
        cached = len(names) - len(missing)
    finally:
        cache.close()

    live = running_pid(paths["pid"])
    if live is not None:
        status = live_status(pid_path=paths["pid"], status_path=paths["status"])
        status.message = (
            f"Already running (pid {live}). Updated sources: "
            f"{len(names)} names, {len(missing)} uncached."
        )
        status.source_count = len(names)
        status.cached = cached
        status.uncached = len(missing)
        status.log_path = str(paths["log"])
        write_status(paths["status"], status)
        _print_json(status.to_dict())
        print(status.message, file=sys.stderr)
        return 0

    if not missing and not job.jsonl:
        status = WarmStatus(
            running=False,
            source_count=len(names),
            cached=cached,
            uncached=0,
            interval_seconds=job.interval,
            poll_seconds=job.poll_interval,
            cache_path=str(paths["cache"]),
            message="All source tags are already cached.",
            log_path=str(paths["log"]),
        )
        write_status(paths["status"], status)
        _print_json(status.to_dict())
        print(status.message, file=sys.stderr)
        return 0

    if getattr(args, "foreground", False):
        return cmd_run(args, job=job, paths=paths)

    run_argv = _run_argv(paths, args)
    pid, error = spawn_daemon(
        run_argv,
        log_path=paths["log"],
        pid_path=paths["pid"],
        cwd=Path.cwd(),
        env={**os.environ, "PYTHONUNBUFFERED": "1"},
    )
    if error:
        print(error, file=sys.stderr)
        return 1
    status = live_status(pid_path=paths["pid"], status_path=paths["status"])
    if not status.running:
        status.running = True
        status.pid = pid
        status.started_at = _utc_now()
        status.source_count = len(names)
        status.cached = cached
        status.uncached = len(missing)
        status.interval_seconds = job.interval
        status.poll_seconds = job.poll_interval
        status.cache_path = str(paths["cache"])
        status.log_path = str(paths["log"])
        status.message = (
            f"Started background tag cache (pid {pid}). "
            f"{len(missing)}/{len(names)} tags need AO3."
        )
        write_status(paths["status"], status)
    else:
        status.source_count = status.source_count or len(names)
        status.uncached = status.uncached or len(missing)
        status.cached = status.cached or cached
        if not status.message:
            status.message = (
                f"Started background tag cache (pid {status.pid}). "
                f"{len(missing)}/{len(names)} tags need AO3."
            )
    _print_json(status.to_dict())
    print(status.message or format_status_text(status), file=sys.stderr)
    return 0


def cmd_run(
    args: argparse.Namespace,
    *,
    job: WarmJob | None = None,
    paths: dict[str, Path] | None = None,
) -> int:
    paths = paths or _paths_from_args(args)
    if job is None:
        loaded = load_job(paths["job"]) if paths["job"].is_file() else None
        job = loaded or job_from_args(args, paths)
        if loaded is None:
            save_job(paths["job"], job)

    write_pid(paths["pid"], os.getpid())
    stop = {"flag": False}

    def _handle(signum: int, frame: Any) -> None:
        stop["flag"] = True

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            signal.signal(sig, _handle)
        except (OSError, ValueError):
            pass

    on_status = lambda msg: print(msg, file=sys.stderr, flush=True)
    resolver = _make_resolver(job, args, on_status)
    try:
        status = run_warm_loop_from_job_file(
            paths["job"],
            resolver,
            status_path=paths["status"],
            on_status=on_status,
            should_stop=lambda: stop["flag"],
            pid=os.getpid(),
            log_path=paths["log"],
        )
    finally:
        resolver.close()
        clear_pid(paths["pid"])

    _print_json(status.to_dict())
    print(format_status_text(status), file=sys.stderr)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Background-fetch uncached AO3 tag mappings into the SQLite cache. "
            "Runs slowly so Search / Download / Simplify are not starved."
        )
    )
    sub = parser.add_subparsers(dest="warm_command", required=True)

    start_p = sub.add_parser(
        "start",
        help="Start a detached warmer (or update sources if already running)",
    )
    _add_source_args(start_p)
    _add_pace_args(start_p)
    _add_path_args(start_p)
    start_p.add_argument(
        "--foreground",
        action="store_true",
        help="Run in this process instead of detaching",
    )

    run_p = sub.add_parser(
        "run",
        help="Run the warmer in this process (used by the daemon child)",
    )
    _add_source_args(run_p)
    _add_pace_args(run_p)
    _add_path_args(run_p)

    status_p = sub.add_parser("status", help="Show daemon status (JSON on stdout)")
    _add_path_args(status_p)

    log_p = sub.add_parser(
        "log",
        help="Print the warmer log (last lines, or --follow)",
    )
    _add_path_args(log_p)
    log_p.add_argument(
        "--lines",
        type=int,
        default=DEFAULT_LOG_LINES,
        help=f"Last N lines (default {DEFAULT_LOG_LINES}; 0 = all, size-capped)",
    )
    log_p.add_argument(
        "-f",
        "--follow",
        action="store_true",
        help="Keep printing new log lines until Ctrl-C",
    )

    stop_p = sub.add_parser("stop", help="Stop the background warmer")
    _add_path_args(stop_p)

    args = parser.parse_args(argv)
    command = args.warm_command
    if command == "start":
        return cmd_start(args)
    if command == "run":
        return cmd_run(args)
    if command == "status":
        return cmd_status(args)
    if command == "log":
        return cmd_log(args)
    if command == "stop":
        return cmd_stop(args)
    parser.error(f"Unknown command: {command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
