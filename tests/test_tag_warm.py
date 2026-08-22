"""Tests for background tag-cache warming."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

from ao3kit.tags.cache import TagCache
from ao3kit.tags.clean import collect_unique_tag_names
from ao3kit.tags.metadata import ResolvedTag, TagProfile, TagRef
from ao3kit.tags.warm import (
    WarmJob,
    collect_warm_names,
    format_status_text,
    job_from_args,
    load_names_file,
    main,
    pid_is_alive,
    running_pid,
    run_warm_loop,
    save_job,
    spawn_daemon,
    stop_daemon,
    uncached_names,
    write_pid,
)

PLUGIN_TAG_WARM = Path(__file__).resolve().parents[1] / "calibre-plugin" / "tag_warm.py"
PLUGIN_SCRAPE_RUN = Path(__file__).resolve().parents[1] / "calibre-plugin" / "scrape_run.py"


def _load_plugin(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeResolver:
    def __init__(self, cache: TagCache, *, errors=None, remap=None, explode=None):
        self.cache = cache
        self.persist = True
        self.fetched: list[str] = []
        self.errors = set(errors or [])
        self.remap = dict(remap or {})
        self.explode = set(explode or [])

    def resolve_one(self, name: str) -> ResolvedTag:
        self.fetched.append(name)
        if name in self.explode:
            raise RuntimeError(f"boom:{name}")
        if name in self.errors:
            return ResolvedTag(
                original=name,
                resolved=name,
                status="error",
                error="not found",
            )
        mapped = self.remap.get(name, name)
        if mapped != name:
            profile = TagProfile(
                name=name,
                url="",
                category="Additional Tags",
                canonical=False,
                filterable=True,
                description="",
                synonym_of=TagRef(name=mapped, url="", href=""),
            )
            self.cache.remember_profile(profile)
            canon = TagProfile(
                name=mapped,
                url="",
                category="Additional Tags",
                canonical=True,
                filterable=True,
                description="",
                synonyms=[TagRef(name=name, url="", href="")],
            )
            self.cache.remember_profile(canon)
            return ResolvedTag(
                original=name,
                resolved=mapped,
                status="synonym",
                changed=True,
            )
        profile = TagProfile(
            name=name,
            url="",
            category="Additional Tags",
            canonical=True,
            filterable=True,
            description="",
        )
        self.cache.remember_profile(profile)
        return ResolvedTag(
            original=name,
            resolved=name,
            status="canonical",
            changed=False,
        )

    def close(self) -> None:
        self.cache.save()
        self.cache.close()


def _cache(tmp_path: Path) -> TagCache:
    return TagCache.load(tmp_path / "ao3_tag_cache.sqlite", ttl_days=90)


def test_collect_unique_tag_names_extra_keys():
    names = collect_unique_tag_names(
        [
            {
                "tags": ["Fluff"],
                "fandoms": ["HP"],
                "relationships": ["A/B"],
                "characters": ["Harry Potter"],
            }
        ],
        include_fandoms=True,
        extra_keys=("relationships", "characters"),
    )
    assert names == ["Fluff", "HP", "A/B", "Harry Potter"]


def test_collect_warm_names_from_jsonl_and_names_file(tmp_path: Path):
    jsonl = tmp_path / "works.jsonl"
    jsonl.write_text(
        json.dumps(
            {
                "tags": ["Fluff", "Angst"],
                "fandoms": ["HP"],
                "characters": ["Hermione Granger"],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    names_file = tmp_path / "extra.txt"
    names_file.write_text("# comment\nFluff\nSlow Burn\n\n", encoding="utf-8")
    names = collect_warm_names(
        jsonl_paths=[jsonl],
        names_files=[names_file],
        names=["Kissing"],
    )
    assert names == [
        "Fluff",
        "Angst",
        "HP",
        "Hermione Granger",
        "Slow Burn",
        "Kissing",
    ]


def test_uncached_names_skips_cached(tmp_path: Path):
    cache = _cache(tmp_path)
    resolver = FakeResolver(cache)
    resolver.resolve_one("Fluff")
    missing = uncached_names(cache, ["Fluff", "Angst"])
    assert missing == ["Angst"]
    cache.close()


def test_warm_loop_fetches_only_uncached_and_idle_exits(tmp_path: Path):
    cache = _cache(tmp_path)
    seeder = FakeResolver(cache)
    seeder.resolve_one("Fluff")
    resolver = FakeResolver(cache)
    job = WarmJob(
        names=["Fluff", "Angst", "Hurt/Comfort"],
        interval=0,
        poll_interval=0,
        idle_exit_polls=1,
        cache=str(tmp_path / "ao3_tag_cache.sqlite"),
    )
    status = run_warm_loop(job, resolver, sleep_fn=lambda _s: None)
    assert resolver.fetched == ["Angst", "Hurt/Comfort"]
    assert status.fetched == 2
    assert status.fetched_tags == ["Angst", "Hurt/Comfort"]
    assert status.uncached == 0
    assert status.running is False
    assert cache.lookup("Angst") is not None
    assert cache.lookup("Hurt/Comfort") is not None
    cache.close()


def test_warm_loop_records_synonym_remap(tmp_path: Path):
    cache = _cache(tmp_path)
    resolver = FakeResolver(cache, remap={"Kisses": "Kissing"})
    job = WarmJob(
        names=["Kisses"],
        interval=0,
        poll_interval=0,
        idle_exit_polls=1,
        cache=str(tmp_path / "ao3_tag_cache.sqlite"),
    )
    status = run_warm_loop(job, resolver, sleep_fn=lambda _s: None)
    assert status.fetched_tags == ["Kisses → Kissing"]
    cache.close()


def test_warm_loop_does_not_repeat_successful_tag(tmp_path: Path):
    cache = _cache(tmp_path)

    class GhostResolver:
        def __init__(self) -> None:
            self.cache = cache
            self.persist = True
            self.fetched: list[str] = []

        def resolve_one(self, name: str) -> ResolvedTag:
            self.fetched.append(name)
            return ResolvedTag(
                original=name,
                resolved="Slow Burn",
                status="synonym",
                changed=True,
            )

        def close(self) -> None:
            self.cache.save()

    resolver = GhostResolver()
    ticks = {"n": 0}

    def should_stop() -> bool:
        return ticks["n"] >= 20

    def sleeper(_seconds: float) -> None:
        ticks["n"] += 1

    job = WarmJob(
        names=["super slow burn though"],
        interval=0,
        poll_interval=0,
        idle_exit_polls=1,
        cache=str(tmp_path / "ao3_tag_cache.sqlite"),
    )
    status = run_warm_loop(
        job, resolver, sleep_fn=sleeper, should_stop=should_stop
    )
    assert resolver.fetched == ["super slow burn though"]
    assert status.fetched == 1
    assert cache.lookup("super slow burn though") == ("Slow Burn", "synonym")
    cache.close()


def test_warm_loop_skips_unlisted_synonym_after_canonical_follow(tmp_path: Path):
    from ao3kit.tags.metadata import TagResolver

    cache_path = tmp_path / "ao3_tag_cache.sqlite"
    resolver = TagResolver(
        session=object(),
        delay=0,
        owns_session=False,
        cache_path=cache_path,
        persist=True,
        follow_canonical=True,
        ttl_days=90,
    )
    fetches: list[str] = []

    def fake_fetch(name: str, *, followed: bool = False):
        fetches.append(name)
        if name == "super slow burn though":
            profile = TagProfile(
                name=name,
                url="",
                category="Additional Tags",
                canonical=False,
                filterable=True,
                description="",
                synonym_of=TagRef(name="Slow Burn", url="", href=""),
            )
        elif name == "Slow Burn":
            profile = TagProfile(
                name=name,
                url="",
                category="Additional Tags",
                canonical=True,
                filterable=True,
                description="",
                synonyms=[TagRef(name="Slow burn", url="", href="")],
            )
        elif name == "Fluff":
            profile = TagProfile(
                name=name,
                url="",
                category="Additional Tags",
                canonical=True,
                filterable=True,
                description="",
            )
        else:
            raise AssertionError(name)
        resolver.warm(profile)
        resolver._profiles.setdefault(name, profile)
        return profile

    resolver._fetch_profile = fake_fetch  # type: ignore[method-assign]
    ticks = {"n": 0}

    def should_stop() -> bool:
        return ticks["n"] >= 20

    def sleeper(_seconds: float) -> None:
        ticks["n"] += 1

    job = WarmJob(
        names=["super slow burn though", "Fluff"],
        interval=0,
        poll_interval=0,
        idle_exit_polls=1,
        cache=str(cache_path),
    )
    status = run_warm_loop(
        job, resolver, sleep_fn=sleeper, should_stop=should_stop
    )
    assert fetches.count("super slow burn though") == 1
    assert "Fluff" in fetches
    assert status.fetched == 2
    assert resolver.cache.lookup("super slow burn though") == (
        "Slow Burn",
        "synonym",
    )
    resolver.close()


def test_format_stop_report_lists_tags():
    from ao3kit.tags.warm import WarmStatus, format_stop_report

    text = format_stop_report(
        WarmStatus(
            fetched=2,
            uncached=4,
            errors=1,
            fetched_tags=["Fluff", "Kisses → Kissing"],
        ),
        "Stopped background tag cache (pid 72720).",
    )
    assert text.startswith("Stopped background tag cache (pid 72720).")
    assert "Cached 2 tags this run" in text
    assert "4 still remaining" in text
    assert "1 error" in text
    assert "Fluff" in text
    assert "Kisses → Kissing" in text


def test_warm_loop_skips_failed_tags_after_retries(tmp_path: Path):
    cache = _cache(tmp_path)
    resolver = FakeResolver(cache, errors={"Bad Tag"})
    job = WarmJob(
        names=["Bad Tag", "Fluff"],
        interval=0,
        poll_interval=0,
        idle_exit_polls=1,
        cache=str(tmp_path / "ao3_tag_cache.sqlite"),
    )
    status = run_warm_loop(job, resolver, sleep_fn=lambda _s: None)
    assert "Fluff" in resolver.fetched
    assert resolver.fetched.count("Bad Tag") == 3
    assert status.errors >= 3
    assert cache.lookup("Fluff") is not None
    assert cache.lookup("Bad Tag") is None
    cache.close()


def test_warm_loop_reloads_job_file(tmp_path: Path):
    cache = _cache(tmp_path)
    resolver = FakeResolver(cache)
    job_path = tmp_path / "job.json"
    save_job(
        job_path,
        WarmJob(
            names=["Alpha"],
            interval=0,
            poll_interval=0,
            idle_exit_polls=1,
            cache=str(tmp_path / "ao3_tag_cache.sqlite"),
        ),
    )
    fetches = {"n": 0}

    def resolve_and_expand(name: str) -> ResolvedTag:
        result = FakeResolver.resolve_one(resolver, name)
        fetches["n"] += 1
        if fetches["n"] == 1:
            save_job(
                job_path,
                WarmJob(
                    names=["Alpha", "Beta"],
                    interval=0,
                    poll_interval=0,
                    idle_exit_polls=1,
                    cache=str(tmp_path / "ao3_tag_cache.sqlite"),
                ),
            )
        return result

    resolver.resolve_one = resolve_and_expand  # type: ignore[method-assign]
    status = run_warm_loop(
        WarmJob(names=["Alpha"], interval=0, poll_interval=0, idle_exit_polls=1),
        resolver,
        job_path=job_path,
        sleep_fn=lambda _s: None,
    )
    assert resolver.fetched == ["Alpha", "Beta"]
    assert status.fetched == 2
    cache.close()


def test_read_log_tail_last_lines(tmp_path: Path):
    from ao3kit.tags.warm import read_log_tail

    path = tmp_path / "tag_warm.log"
    path.write_text("".join(f"line {i}\n" for i in range(10)), encoding="utf-8")
    assert read_log_tail(path, lines=3) == "line 7\nline 8\nline 9\n"
    assert "line 0" in read_log_tail(path, lines=0)
    assert read_log_tail(tmp_path / "missing.log") == ""


def test_follow_log_streams_new_bytes(tmp_path: Path):
    from ao3kit.tags.warm import follow_log

    path = tmp_path / "tag_warm.log"
    path.write_text("start\n", encoding="utf-8")
    chunks: list[str] = []
    ticks = {"n": 0}

    def stop() -> bool:
        return ticks["n"] >= 2

    def sleeper(_seconds: float) -> None:
        ticks["n"] += 1
        if ticks["n"] == 1:
            with path.open("a", encoding="utf-8") as handle:
                handle.write("next\n")

    code = follow_log(
        path,
        lines=10,
        sleep_fn=sleeper,
        should_stop=stop,
        write=chunks.append,
        poll=0,
    )
    assert code == 0
    assert "start\n" in "".join(chunks)
    assert "next\n" in "".join(chunks)


def test_cli_log_prints_tail(tmp_path: Path, capsys):
    log_path = tmp_path / "warm.log"
    log_path.write_text("alpha\nbeta\ngamma\n", encoding="utf-8")
    code = main(
        [
            "log",
            "--lines",
            "2",
            "--log-file",
            str(log_path),
            "--cache",
            str(tmp_path / "cache.sqlite"),
        ]
    )
    assert code == 0
    assert capsys.readouterr().out == "beta\ngamma\n"


def test_cli_log_missing_is_quiet(tmp_path: Path, capsys):
    code = main(
        [
            "log",
            "--log-file",
            str(tmp_path / "missing.log"),
            "--cache",
            str(tmp_path / "cache.sqlite"),
            "--pid-file",
            str(tmp_path / "warm.pid"),
        ]
    )
    assert code == 0
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "No log yet" in captured.err


def test_cli_status_when_not_running(tmp_path: Path, capsys):
    pid = tmp_path / "warm.pid"
    status = tmp_path / "warm.status.json"
    code = main(
        [
            "status",
            "--pid-file",
            str(pid),
            "--status-file",
            str(status),
            "--cache",
            str(tmp_path / "cache.sqlite"),
        ]
    )
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["running"] is False


def test_cli_start_without_sources_errors(capsys):
    code = main(["start"])
    assert code == 2
    assert "jsonl" in capsys.readouterr().err.lower()


def test_cli_start_foreground_warms_names_file(tmp_path: Path, capsys):
    names_file = tmp_path / "tags.txt"
    names_file.write_text("Slow Burn\n", encoding="utf-8")
    cache_path = tmp_path / "cache.sqlite"
    cache = TagCache.load(cache_path, ttl_days=90)
    resolver = FakeResolver(cache)

    import ao3kit.tags.warm as warm_mod

    original = warm_mod._make_resolver

    def fake_make(job, args, on_status):
        return resolver

    warm_mod._make_resolver = fake_make  # type: ignore[method-assign]
    try:
        code = main(
            [
                "start",
                "--foreground",
                "--names-file",
                str(names_file),
                "--interval",
                "0",
                "--poll",
                "0",
                "--idle-exit",
                "1",
                "--cache",
                str(cache_path),
                "--pid-file",
                str(tmp_path / "warm.pid"),
                "--status-file",
                str(tmp_path / "warm.status.json"),
                "--job-file",
                str(tmp_path / "warm.job.json"),
                "--log-file",
                str(tmp_path / "warm.log"),
            ]
        )
    finally:
        warm_mod._make_resolver = original  # type: ignore[method-assign]

    assert code == 0
    assert resolver.fetched == ["Slow Burn"]
    payload = json.loads(capsys.readouterr().out)
    assert payload["fetched"] == 1
    assert payload["running"] is False
    assert not (tmp_path / "warm.pid").exists()


def test_cli_start_all_cached_does_not_spawn(tmp_path: Path, capsys, monkeypatch):
    cache_path = tmp_path / "cache.sqlite"
    cache = TagCache.load(cache_path, ttl_days=90)
    FakeResolver(cache).resolve_one("Fluff")
    cache.close()
    names_file = tmp_path / "tags.txt"
    names_file.write_text("Fluff\n", encoding="utf-8")

    def fail_spawn(*_args, **_kwargs):
        raise AssertionError("should not spawn when everything is cached")

    monkeypatch.setattr("ao3kit.tags.warm.spawn_daemon", fail_spawn)
    code = main(
        [
            "start",
            "--names-file",
            str(names_file),
            "--cache",
            str(cache_path),
            "--pid-file",
            str(tmp_path / "warm.pid"),
            "--status-file",
            str(tmp_path / "warm.status.json"),
            "--job-file",
            str(tmp_path / "warm.job.json"),
            "--log-file",
            str(tmp_path / "warm.log"),
        ]
    )
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["running"] is False
    assert payload["uncached"] == 0
    assert "already cached" in payload["message"].lower()


def test_spawn_daemon_waits_for_pid_file(tmp_path: Path):
    pid_path = tmp_path / "warm.pid"
    log_path = tmp_path / "warm.log"

    class FakeProc:
        def poll(self):
            return None

        @property
        def pid(self):
            return 4242

    def fake_popen(argv, **kwargs):
        write_pid(pid_path, 4242)
        return FakeProc()

    pid, error = spawn_daemon(
        ["true"],
        log_path=log_path,
        pid_path=pid_path,
        popen=fake_popen,
        wait_seconds=1,
    )
    assert error is None
    assert pid == 4242


def test_stop_daemon_stale_pid(tmp_path: Path):
    pid_path = tmp_path / "warm.pid"
    write_pid(pid_path, 99999999)
    stopped, message = stop_daemon(pid_path)
    assert stopped is False
    assert "not running" in message.lower()
    assert running_pid(pid_path) is None


def test_pid_is_alive_self():
    import os

    assert pid_is_alive(os.getpid()) is True
    assert pid_is_alive(0) is False


def test_format_status_text_running():
    from ao3kit.tags.warm import WarmStatus

    text = format_status_text(
        WarmStatus(
            running=True,
            pid=12,
            source_count=10,
            cached=7,
            uncached=3,
            fetched=3,
            interval_seconds=10,
            message="Caching tags [3/3] Fluff",
        )
    )
    assert "running (pid 12)" in text
    assert "7/10 cached" in text


def test_job_from_args_resolves_paths(tmp_path: Path):
    jsonl = tmp_path / "a.jsonl"
    jsonl.write_text("{}\n", encoding="utf-8")
    args = SimpleNamespace(
        jsonl=[str(jsonl)],
        names_files=[],
        tags=["Fluff"],
        interval=12.5,
        poll_interval=30,
        idle_exit_polls=2,
        no_follow_canonical=False,
        cache_ttl_days=90,
        username=None,
        password=None,
    )
    job = job_from_args(args, {"cache": tmp_path / "cache.sqlite"})
    assert job.interval == 12.5
    assert job.names == ["Fluff"]
    assert Path(job.jsonl[0]).is_absolute()


def test_tags_main_dispatches_warm(tmp_path: Path, capsys):
    from ao3kit.tags.metadata import main as tags_main

    code = tags_main(
        [
            "warm",
            "status",
            "--pid-file",
            str(tmp_path / "warm.pid"),
            "--status-file",
            str(tmp_path / "warm.status.json"),
            "--cache",
            str(tmp_path / "cache.sqlite"),
        ]
    )
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["running"] is False


def test_plugin_unique_names_and_status_text(tmp_path: Path):
    mod = _load_plugin(PLUGIN_TAG_WARM, "ao3_tag_warm")
    names = mod.unique_tag_names_from_records(
        [
            {
                "tags": ["Fluff", "Angst"],
                "fandoms": ["HP"],
                "relationships": ["A/B"],
                "characters": ["Harry Potter"],
            },
            {"tags": ["Fluff"], "fandoms": ["HP"]},
        ]
    )
    assert names == ["Fluff", "Angst", "HP", "A/B", "Harry Potter"]
    path = tmp_path / "names.txt"
    mod.write_names_file(path, names)
    assert load_names_file(path) == names

    text = mod.format_warm_started_text(
        {
            "running": True,
            "pid": 9,
            "cached": 2,
            "uncached": 3,
            "interval_seconds": 10,
            "message": "Started.",
        },
        book_count=4,
        name_count=5,
    )
    assert "pid 9" in text
    assert "4 book" in text
    assert "does not change" not in text.lower() or "not modified" in text.lower()

    caught_up = mod.format_warm_started_text(
        {"running": False, "uncached": 0, "cached": 5},
        book_count=4,
        name_count=5,
    )
    assert "already in the cache" in caught_up

    parsed = mod.parse_warm_status_json('{"running": true, "pid": 3}')
    assert parsed == {"running": True, "pid": 3}

    log_path = tmp_path / "tag_warm.log"
    log_path.write_text("one\ntwo\nthree\n", encoding="utf-8")
    assert mod.read_log_tail(log_path, lines=2) == "two\nthree\n"
    header = mod.format_warm_log_header(
        {"running": True, "pid": 4, "source_count": 10, "cached": 7, "uncached": 3},
        log_path,
    )
    assert "Running (pid 4)" in header
    assert "7/10 cached" in header
    assert str(log_path) in header
    assert mod.warm_log_path(tmp_path) == tmp_path / ".cache" / "tag_warm.log"

    summary, details = mod.format_warm_stopped_dialog(
        {
            "message": "Stopped background tag cache (pid 72720).",
            "fetched": 2,
            "uncached": 4,
            "fetched_tags": ["Fluff", "Kisses → Kissing"],
        }
    )
    assert "pid 72720" in summary
    assert "Cached 2 tags this run" in summary
    assert "4 still remaining" in summary
    assert "Fluff" in summary
    assert "Kisses → Kissing" in details
    empty_summary, empty_details = mod.format_warm_stopped_dialog(
        {"message": "Stopped background tag cache (pid 1).", "fetched": 0}
    )
    assert "No new tags" in empty_summary
    assert empty_details == ""


def test_build_warm_argv_includes_login():
    mod = _load_plugin(PLUGIN_SCRAPE_RUN, "ao3_scrape_run_warm")
    argv = mod.build_warm_start_argv(
        "/tmp/names.txt",
        {"username": "emily", "password": "secret"},
    )
    assert argv[:3] == ["tags", "warm", "start"]
    assert argv[argv.index("--names-file") + 1] == "/tmp/names.txt"
    assert argv[argv.index("--username") + 1] == "emily"
    assert "--delay" not in argv
    assert mod.build_warm_status_argv() == ["tags", "warm", "status"]
    assert mod.build_warm_stop_argv() == ["tags", "warm", "stop"]
    assert mod.build_warm_log_argv(lines=50, follow=True) == [
        "tags",
        "warm",
        "log",
        "--lines",
        "50",
        "--follow",
    ]
