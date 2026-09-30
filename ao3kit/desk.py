"""Local web page for AO3 search jobs and job status.

``python -m ao3kit desk`` serves one page: start a search (optional EPUB
download), watch background jobs, and open the Calibre content server.
It does not write the Calibre library. Finished JSONL and EPUBs stay in the
job's ``work/`` directory.
"""

from __future__ import annotations

import argparse
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from ao3kit.jobs import (
    JobSpec,
    JobStatus,
    WARM_JOB_ID,
    find_job,
    infer_result_spec,
    job_is_retryable,
    job_paths,
    list_jobs,
    live_job_status,
    new_job_id,
    read_pid,
    retry_job,
    start_job,
    utc_now,
    write_status,
)
from ao3kit.proc import pid_is_alive, read_log_tail, stop_process

DEFAULT_PORT = 8091
DEFAULT_CALIBRE_PORT = 8081
MAX_RESULTS = 500
_LOG_LINES = 80

_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Fanfic desk</title>
  <style>
    :root {
      color-scheme: light dark;
      --bg: #fafaf9; --card: #fff; --text: #292524; --muted: #78716c;
      --border: #e7e5e4; --accent: #4f5e55;
    }
    @media (prefers-color-scheme: dark) {
      :root {
        --bg: #1c1917; --card: #292524; --text: #fafaf9; --muted: #a8a29e;
        --border: #44403c; --accent: #a7b8ae;
      }
    }
    * { box-sizing: border-box; }
    body {
      margin: 0; font: 16px/1.4 system-ui, sans-serif;
      background: var(--bg); color: var(--text); padding: 1.5rem 1rem 3rem;
    }
    main { max-width: 52rem; margin: 0 auto; }
    h1 { font-size: 1.4rem; margin: 0 0 0.25rem; }
    .sub { color: var(--muted); margin: 0 0 1rem; }
    a.jump {
      display: inline-block; margin-bottom: 1.25rem; color: inherit;
      border: 1px solid var(--border); background: var(--card);
      border-radius: 0.6rem; padding: 0.45rem 0.75rem; text-decoration: none;
    }
    a.jump:hover { border-color: var(--accent); }
    section {
      background: var(--card); border: 1px solid var(--border);
      border-radius: 0.75rem; padding: 1rem 1.1rem; margin-bottom: 1rem;
    }
    h2 { font-size: 1rem; margin: 0 0 0.75rem; }
    label { display: block; font-size: 0.8rem; color: var(--muted); margin-top: 0.6rem; }
    input, select, button {
      font: inherit; color: inherit; background: var(--bg);
      border: 1px solid var(--border); border-radius: 0.45rem;
      padding: 0.4rem 0.55rem;
    }
    input[type="text"], input[type="password"], input[type="number"], select { width: 100%; }
    nav.bar { display: flex; gap: 0.5rem; align-items: center; flex-wrap: wrap; margin-bottom: 1rem; }
    nav.bar a.jump { margin-bottom: 0; }
    button[aria-pressed="true"] { border-color: var(--accent); }
    .row { display: flex; gap: 1rem; align-items: center; flex-wrap: wrap; margin-top: 0.7rem; }
    .check { font-size: 0.95rem; color: var(--text); }
    button { cursor: pointer; background: var(--card); }
    button.primary { background: var(--accent); color: #fafaf9; border-color: transparent; }
    .err { color: #b91c1c; min-height: 1.2em; font-size: 0.9rem; }
    ul.jobs { list-style: none; margin: 0; padding: 0; }
    li.job {
      border-top: 1px solid var(--border); padding: 0.55rem 0;
      display: grid; grid-template-columns: 1fr auto; gap: 0.35rem 0.75rem;
    }
    li.job:first-child { border-top: 0; }
    button.linkish { border: 0; background: transparent; padding: 0; text-align: left; font-weight: 600; }
    .meta { color: var(--muted); font-size: 0.8rem; }
    .acts { display: flex; gap: 0.35rem; }
    pre.log {
      margin: 0.5rem 0 0; max-height: 16rem; overflow: auto;
      background: var(--bg); border-radius: 0.45rem; padding: 0.6rem;
      font-size: 0.75rem; white-space: pre-wrap;
    }
    .empty { color: var(--muted); }
  </style>
</head>
<body>
<main>
  <h1>Fanfic desk</h1>
  <p class="sub">Search AO3 and watch jobs. Finished files stay in the job folder. Read the library in Calibre.</p>
  <nav class="bar">
    <button type="button" id="tab-search" aria-pressed="true">Search</button>
    <button type="button" id="tab-settings" aria-pressed="false">Settings</button>
    <a class="jump" id="calibre" href="/" target="_blank" rel="noopener">Open Calibre</a>
  </nav>
  <div id="view-search">
  <section>
    <h2>Search AO3</h2>
    <form id="search">
      <label>Works, collection, user, or series URL
        <input name="url" type="text" placeholder="https://archiveofourown.org/works?…" />
      </label>
      <label>Fandom / tag
        <input name="tag_id" type="text" placeholder="Harry Potter - J. K. Rowling" />
      </label>
      <label>Search query
        <input name="query" type="text" placeholder="amy/rory" />
      </label>
      <label>Complete works
        <select name="complete">
          <option value="">Any</option>
          <option value="true">Complete only</option>
          <option value="false">In progress only</option>
        </select>
      </label>
      <label>Max results
        <input name="max_results" type="number" min="1" max="500" value="25" />
      </label>
      <div class="row">
        <label class="check"><input name="download" type="checkbox" checked /> Download EPUBs</label>
        <label class="check"><input name="include_series" type="checkbox" /> Also fetch the rest of each series</label>
      </div>
      <div class="row">
        <button class="primary" type="submit">Start search</button>
        <span class="err" id="form-err"></span>
      </div>
    </form>
  </section>
  <section>
    <h2>Jobs</h2>
    <ul class="jobs" id="jobs"><li class="empty">Loading…</li></ul>
    <pre class="log" id="log" hidden></pre>
  </section>
  </div>
  <div id="view-settings" hidden>
  <section>
    <h2>AO3 login</h2>
    <p class="meta" id="login-state">Loading…</p>
    <form id="settings">
      <label>Username
        <input name="username" type="text" autocomplete="username" />
      </label>
      <label>Password
        <input name="password" type="password" autocomplete="current-password" placeholder="Leave blank to keep the saved password" />
      </label>
      <div class="row">
        <button type="button" id="test-login">Test login</button>
        <button type="button" id="clear-login">Clear login</button>
      </div>
      <h2 style="margin-top:1.25rem">Search defaults</h2>
      <label>Max results
        <input name="max_results" type="number" min="1" max="500" value="25" />
      </label>
      <label>Minimum seconds between AO3 requests
        <input name="min_request_interval" type="number" min="0.2" max="120" step="0.1" value="1.5" />
      </label>
      <label>Extra pause while warming the tag cache (seconds)
        <input name="tag_warm_interval" type="number" min="0" max="600" step="0.5" value="10" />
      </label>
      <label>Language
        <input name="default_language_id" type="text" value="en" />
      </label>
      <div class="row">
        <label class="check"><input name="download_epubs" type="checkbox" checked /> Download EPUBs</label>
        <label class="check"><input name="include_series" type="checkbox" /> Also fetch the rest of each series</label>
        <label class="check"><input name="include_metatags" type="checkbox" checked /> Add fandom metatags</label>
        <label class="check"><input name="drop_unmarked" type="checkbox" checked /> Drop unmarked tags</label>
        <label class="check"><input name="cover_enabled" type="checkbox" checked /> Generate covers</label>
      </div>
      <div class="row">
        <button class="primary" type="submit">Save settings</button>
        <span class="err" id="settings-err"></span>
      </div>
    </form>
  </section>
  </div>
</main>
<script>
const calibrePort = __CALIBRE_PORT__;
const calibre = document.getElementById("calibre");
calibre.href = `${location.protocol}//${location.hostname}:${calibrePort}/`;
calibre.textContent = `Open Calibre :${calibrePort}`;

const jobsEl = document.getElementById("jobs");
const logEl = document.getElementById("log");
const formErr = document.getElementById("form-err");
let selected = "";

function stateLabel(job) {
  if (job.running) return "running";
  if (job.exit_code === 0) return "done";
  if (job.exit_code === 130) return "stopped";
  if (job.exit_code == null && !job.finished_at) return "queued";
  return "failed";
}

function render(jobs) {
  if (!jobs.length) {
    jobsEl.innerHTML = '<li class="empty">No jobs yet.</li>';
    return;
  }
  jobsEl.replaceChildren();
  for (const job of jobs) {
    const li = document.createElement("li");
    li.className = "job";
    const title = document.createElement("button");
    title.className = "linkish";
    title.type = "button";
    title.textContent = job.title || job.id;
    title.addEventListener("click", () => openJob(job.id));
    const meta = document.createElement("div");
    meta.className = "meta";
    const bits = [stateLabel(job)];
    if (job.result) bits.push(job.result);
    if (Array.isArray(job.progress) && job.progress.length >= 2) bits.push(`${job.progress[0]}/${job.progress[1]}`);
    if (job.message) bits.push(job.message);
    meta.textContent = bits.join(" · ");
    const acts = document.createElement("div");
    acts.className = "acts";
    if (job.stoppable) acts.append(action("Stop", () => post(`/api/jobs/${job.id}/stop`)));
    if (job.retryable) acts.append(action("Retry", () => post(`/api/jobs/${job.id}/retry`)));
    li.append(title, acts, meta);
    jobsEl.append(li);
  }
}

function action(label, fn) {
  const b = document.createElement("button");
  b.type = "button";
  b.textContent = label;
  b.addEventListener("click", fn);
  return b;
}

async function post(url, body) {
  formErr.textContent = "";
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: body ? JSON.stringify(body) : "{}",
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    formErr.textContent = data.error || `Request failed (${res.status})`;
    return null;
  }
  await refresh();
  return data;
}

async function openJob(id) {
  selected = id;
  const res = await fetch(`/api/jobs/${id}`);
  const data = await res.json();
  logEl.hidden = false;
  logEl.textContent = data.log || "(no log yet)";
}

async function refresh() {
  const res = await fetch("/api/jobs");
  const data = await res.json();
  render(data.jobs || []);
  if (selected) openJob(selected);
}

document.getElementById("search").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const fd = new FormData(ev.target);
  const body = {
    url: fd.get("url") || "",
    tag_id: fd.get("tag_id") || "",
    query: fd.get("query") || "",
    complete: fd.get("complete") || "",
    max_results: fd.get("max_results") || "",
    download: fd.get("download") === "on",
    include_series: fd.get("include_series") === "on",
  };
  const data = await post("/api/search", body);
  if (data && data.job) {
    selected = data.job.id;
    openJob(selected);
  }
});

function showView(name) {
  const settings = name === "settings";
  document.getElementById("view-search").hidden = settings;
  document.getElementById("view-settings").hidden = !settings;
  document.getElementById("tab-search").setAttribute("aria-pressed", settings ? "false" : "true");
  document.getElementById("tab-settings").setAttribute("aria-pressed", settings ? "true" : "false");
  if (settings) loadSettings();
  if (settings) location.hash = "settings";
  else if (location.hash === "#settings") history.replaceState(null, "", location.pathname);
}

document.getElementById("tab-search").addEventListener("click", () => showView("search"));
document.getElementById("tab-settings").addEventListener("click", () => showView("settings"));

function checked(form, name) {
  return form.elements[name].checked;
}

async function loadSettings() {
  const res = await fetch("/api/settings");
  const data = await res.json();
  const form = document.getElementById("settings");
  form.username.value = data.username || "";
  form.password.value = "";
  form.max_results.value = data.max_results || 25;
  form.min_request_interval.value = data.min_request_interval;
  form.tag_warm_interval.value = data.tag_warm_interval;
  form.default_language_id.value = data.default_language_id || "en";
  form.download_epubs.checked = !!data.download_epubs;
  form.include_series.checked = !!data.include_series;
  form.include_metatags.checked = !!data.include_metatags;
  form.drop_unmarked.checked = !!data.drop_unmarked;
  form.cover_enabled.checked = !!data.cover_enabled;
  const state = document.getElementById("login-state");
  state.textContent = data.password_set
    ? `Saved login for ${data.username}. Searches use it until you clear it.`
    : "No AO3 login saved. Restricted works stay anonymous.";
  if (!defaultsApplied) {
    const search = document.getElementById("search");
    search.download.checked = !!data.download_epubs;
    search.include_series.checked = !!data.include_series;
    if (data.max_results) search.max_results.value = data.max_results;
    defaultsApplied = true;
  }
}

function settingsBody(extra) {
  const form = document.getElementById("settings");
  return Object.assign({
    username: form.username.value,
    password: form.password.value,
    max_results: form.max_results.value,
    min_request_interval: form.min_request_interval.value,
    tag_warm_interval: form.tag_warm_interval.value,
    default_language_id: form.default_language_id.value,
    download_epubs: checked(form, "download_epubs"),
    include_series: checked(form, "include_series"),
    include_metatags: checked(form, "include_metatags"),
    drop_unmarked: checked(form, "drop_unmarked"),
    cover_enabled: checked(form, "cover_enabled"),
  }, extra || {});
}

async function postSettings(url, body) {
  const err = document.getElementById("settings-err");
  err.textContent = "";
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    err.textContent = data.error || `Request failed (${res.status})`;
    return null;
  }
  err.textContent = data.message || "Saved.";
  await loadSettings();
  return data;
}

document.getElementById("settings").addEventListener("submit", (ev) => {
  ev.preventDefault();
  postSettings("/api/settings", settingsBody());
});
document.getElementById("test-login").addEventListener("click", () => {
  postSettings("/api/login", settingsBody());
});
document.getElementById("clear-login").addEventListener("click", () => {
  postSettings("/api/settings", settingsBody({ username: "", password: "", clear_password: true }));
});

let defaultsApplied = false;
loadSettings();
refresh();
setInterval(refresh, 3000);
if (location.hash === "#settings") showView("settings");
</script>
</body>
</html>
"""


def _truthy(value: Any, *, default: bool = False) -> bool:
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _clean_job_id(job_id: str) -> str | None:
    text = (job_id or "").strip()
    if not text or text in {".", ".."} or "/" in text or "\\" in text:
        return None
    return text


def search_argv(
    fields: dict[str, Any],
    *,
    jsonl: Path,
    epub_dir: Path,
) -> tuple[list[str] | None, str | None]:
    """Build one ``scrape`` argv from the desk form. Does not touch the network."""
    url = str(fields.get("url") or "").strip()
    tag = str(fields.get("tag_id") or "").strip()
    query = str(fields.get("query") or "").strip()
    if url:
        if not (url.startswith("http://") or url.startswith("https://")):
            return None, "URL must start with http:// or https://."
    elif not tag and not query:
        return None, "Enter an AO3 URL, a fandom, or a search query."

    argv = ["scrape", "-o", str(jsonl), "--verbose"]
    if url:
        argv.extend(["--url", url])
    else:
        if tag:
            argv.extend(["--tag-id", tag])
        if query:
            argv.extend(["--query", query])
        complete = str(fields.get("complete") or "").strip().lower()
        if complete in {"true", "complete", "yes"}:
            argv.append("--complete")
        elif complete in {"false", "incomplete", "no"}:
            argv.append("--no-complete")
        elif complete not in {"", "any"}:
            return None, "Complete must be any, complete, or in progress."

    raw_max = fields.get("max_results")
    if raw_max not in (None, ""):
        try:
            count = int(raw_max)
        except (TypeError, ValueError):
            return None, "Max results must be a number."
        if count < 1 or count > MAX_RESULTS:
            return None, f"Max results must be between 1 and {MAX_RESULTS}."
        argv.extend(["--max-results", str(count)])

    if _truthy(fields.get("include_series")):
        argv.append("--include-series")
    if _truthy(fields.get("download"), default=True):
        argv.extend(
            ["--download", "--epub-dir", str(epub_dir), "--no-zip", "--no-simplify"]
        )
    return argv, None


def search_title(fields: dict[str, Any]) -> str:
    url = str(fields.get("url") or "").strip()
    tag = str(fields.get("tag_id") or "").strip()
    query = str(fields.get("query") or "").strip()
    if url:
        label = url
    elif tag and query:
        label = f"{tag} · {query}"
    else:
        label = tag or query or "AO3"
    verb = "Search and download" if _truthy(fields.get("download"), default=True) else "Search"
    text = f"{verb}: {label}"
    return text if len(text) <= 80 else text[:79] + "…"


def plan_search(fields: dict[str, Any], *, job_id: str, work: Path) -> tuple[JobSpec | None, str | None]:
    jsonl = work / "results.jsonl"
    argv, error = search_argv(fields, jsonl=jsonl, epub_dir=work)
    if error or argv is None:
        return None, error or "Could not build the search."
    return (
        JobSpec(
            id=job_id,
            title=search_title(fields),
            kind="scrape",
            steps=[argv],
            cwd=str(work),
            result=infer_result_spec([argv]),
        ),
        None,
    )


def start_search(
    fields: dict[str, Any],
    *,
    jobs_dir: Path,
) -> tuple[JobStatus | None, str | None]:
    job_id = new_job_id("scrape")
    work = jobs_dir / job_id / "work"
    spec, error = plan_search(fields, job_id=job_id, work=work)
    if error or spec is None:
        return None, error or "Could not build the search."
    work.mkdir(parents=True, exist_ok=True)
    status, start_error = start_job(spec, jobs_dir=jobs_dir, cwd=work)
    if start_error:
        return status, start_error
    return status, None


def job_view(status: JobStatus, *, log: str | None = None) -> dict[str, Any]:
    data = status.to_dict()
    data.pop("log_path", None)
    data.pop("pid", None)
    data["stoppable"] = bool(status.running) and status.id != WARM_JOB_ID
    data["retryable"] = job_is_retryable(status)
    if log is not None:
        data["log"] = log
    return data


def list_job_views(jobs_dir: Path | None = None, *, limit: int = 40) -> list[dict[str, Any]]:
    jobs = list_jobs(jobs_dir)
    return [job_view(item) for item in jobs[:limit]]


def job_detail(job_id: str, *, jobs_dir: Path | None = None) -> dict[str, Any] | None:
    clean = _clean_job_id(job_id)
    if clean is None:
        return None
    path = find_job(clean, jobs_dir)
    if path is None:
        return None
    status = live_job_status(path)
    log = read_log_tail(job_paths(path)["log"], lines=_LOG_LINES)
    return job_view(status, log=log)


def stop_desk_job(job_id: str, *, jobs_dir: Path | None = None) -> tuple[dict[str, Any] | None, str | None]:
    clean = _clean_job_id(job_id)
    if clean is None or clean == WARM_JOB_ID:
        return None, "Unknown job."
    path = find_job(clean, jobs_dir)
    if path is None:
        return None, "Unknown job."
    paths = job_paths(path)
    pid = read_pid(paths["pid"])
    was_alive = pid is not None and pid_is_alive(pid)
    _stopped, message = stop_process(paths["pid"], noun=f"job {clean}")
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
    return job_view(status), None


def retry_desk_job(job_id: str, *, jobs_dir: Path | None = None) -> tuple[dict[str, Any] | None, str | None]:
    clean = _clean_job_id(job_id)
    if clean is None:
        return None, "Unknown job."
    status, error = retry_job(clean, jobs_dir=jobs_dir)
    if error:
        return job_view(status) if status.id else None, error
    return job_view(status), None


def _bounded_float(value: Any, *, low: float, high: float) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number < low or number > high:
        return None
    return number


def _bounded_int(value: Any, *, low: int, high: int) -> int | None:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    if number < low or number > high:
        return None
    return number


def settings_view(home: Path | None = None) -> dict[str, Any]:
    from ao3kit.config import load_user_config
    from ao3kit.credentials import read_credentials

    cfg = load_user_config(home=home, ensure=True)
    username, password = read_credentials(cfg.home)
    settings = cfg.settings
    return {
        "username": username,
        "password_set": bool(password),
        "min_request_interval": settings.min_request_interval,
        "tag_warm_interval": settings.tag_warm_interval,
        "include_metatags": settings.include_metatags,
        "drop_unmarked": settings.drop_unmarked,
        "default_language_id": settings.default_language_id,
        "cover_enabled": settings.cover.enabled,
        "download_epubs": settings.desk.download_epubs,
        "include_series": settings.desk.include_series,
        "max_results": settings.desk.max_results,
    }


def save_settings(
    fields: dict[str, Any],
    *,
    home: Path | None = None,
) -> tuple[dict[str, Any] | None, str | None]:
    from ao3kit.config import load_user_config
    from ao3kit.credentials import clear_credentials, read_credentials, write_credentials

    interval = _bounded_float(fields.get("min_request_interval"), low=0.2, high=120)
    if interval is None:
        return None, "Minimum request interval must be between 0.2 and 120 seconds."
    warm = _bounded_float(fields.get("tag_warm_interval"), low=0, high=600)
    if warm is None:
        return None, "Tag-cache pause must be between 0 and 600 seconds."
    language = str(fields.get("default_language_id") or "").strip()
    if (
        not language
        or len(language) > 16
        or any(not (ch.isalnum() or ch in "-_") for ch in language)
    ):
        return None, "Language must be a short id such as en."
    max_results = _bounded_int(fields.get("max_results"), low=1, high=MAX_RESULTS)
    if max_results is None:
        return None, f"Max results must be between 1 and {MAX_RESULTS}."

    username = str(fields.get("username") or "").strip()
    password = fields.get("password")
    password_text = password if isinstance(password, str) else ""
    clear_login = _truthy(fields.get("clear_password")) or not username
    if not clear_login and not password_text:
        _current_user, current_password = read_credentials(home)
        if not current_password:
            return None, "Enter a password to save an AO3 login."

    cfg = load_user_config(home=home, ensure=True)
    cover = cfg.settings.cover.to_dict()
    cover["enabled"] = _truthy(fields.get("cover_enabled"), default=True)
    desk = cfg.settings.desk.to_dict()
    desk["download_epubs"] = _truthy(fields.get("download_epubs"), default=True)
    desk["include_series"] = _truthy(fields.get("include_series"))
    desk["max_results"] = max_results
    cfg.update_settings(
        min_request_interval=interval,
        tag_warm_interval=warm,
        include_metatags=_truthy(fields.get("include_metatags"), default=True),
        drop_unmarked=_truthy(fields.get("drop_unmarked"), default=True),
        default_language_id=language,
        cover=cover,
        desk=desk,
    )

    if clear_login:
        clear_credentials(cfg.home)
    elif password_text:
        write_credentials(username, password_text, home=cfg.home)
    else:
        _current_user, current_password = read_credentials(cfg.home)
        write_credentials(username, current_password, home=cfg.home)
    view = settings_view(cfg.home)
    view["message"] = "Saved."
    return view, None


def check_login(
    fields: dict[str, Any],
    *,
    home: Path | None = None,
) -> tuple[dict[str, Any] | None, str | None]:
    from ao3kit.credentials import read_credentials
    from ao3kit.http import Ao3HttpError, LoginError, verify_login

    saved_user, saved_password = read_credentials(home)
    username = str(fields.get("username") or saved_user).strip()
    password = fields.get("password")
    if not isinstance(password, str) or not password:
        password = saved_password
    try:
        verified = verify_login(username, password)
    except LoginError as exc:
        return None, str(exc)
    except Ao3HttpError as exc:
        return None, str(exc)
    return {"ok": True, "username": verified, "message": f"Logged in as {verified}."}, None


def page_html(*, calibre_port: int = DEFAULT_CALIBRE_PORT) -> str:
    return _PAGE.replace("__CALIBRE_PORT__", str(int(calibre_port)))


class DeskServer(ThreadingHTTPServer):
    jobs_dir: Path
    calibre_port: int
    config_home: Path | None

    def __init__(
        self,
        server_address: tuple[str, int],
        jobs_dir: Path,
        *,
        calibre_port: int = DEFAULT_CALIBRE_PORT,
        config_home: Path | None = None,
    ) -> None:
        self.jobs_dir = jobs_dir
        self.calibre_port = calibre_port
        self.config_home = config_home
        super().__init__(server_address, DeskHandler)


class DeskHandler(BaseHTTPRequestHandler):
    server: DeskServer

    def log_message(self, fmt: str, *args: Any) -> None:
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path in {"", "/"}:
            body = page_html(calibre_port=self.server.calibre_port).encode("utf-8")
            self._send(200, "text/html; charset=utf-8", body)
            return
        if path == "/api/jobs":
            payload = {"jobs": list_job_views(self.server.jobs_dir)}
            self._send_json(200, payload)
            return
        if path == "/api/settings":
            self._send_json(200, settings_view(self.server.config_home))
            return
        prefix = "/api/jobs/"
        if path.startswith(prefix) and path.count("/") == 3:
            detail = job_detail(path[len(prefix) :], jobs_dir=self.server.jobs_dir)
            if detail is None:
                self._send_json(404, {"error": "Unknown job."})
                return
            self._send_json(200, detail)
            return
        self._send_json(404, {"error": "Not found."})

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path == "/api/settings":
            fields, error = self._read_json()
            if error:
                self._send_json(400, {"error": error})
                return
            view, save_error = save_settings(fields, home=self.server.config_home)
            if save_error or view is None:
                self._send_json(400, {"error": save_error or "Could not save settings."})
                return
            self._send_json(200, view)
            return
        if path == "/api/login":
            fields, error = self._read_json()
            if error:
                self._send_json(400, {"error": error})
                return
            view, login_error = check_login(fields, home=self.server.config_home)
            if login_error or view is None:
                self._send_json(400, {"error": login_error or "AO3 login failed."})
                return
            self._send_json(200, view)
            return
        if path == "/api/search":
            fields, error = self._read_json()
            if error:
                self._send_json(400, {"error": error})
                return
            status, start_error = start_search(fields, jobs_dir=self.server.jobs_dir)
            if start_error or status is None:
                self._send_json(400, {"error": start_error or "Could not start the search."})
                return
            self._send_json(200, {"job": job_view(status)})
            return
        if path.startswith("/api/jobs/") and path.endswith("/stop"):
            job_id = path[len("/api/jobs/") : -len("/stop")]
            view, error = stop_desk_job(job_id, jobs_dir=self.server.jobs_dir)
            if error or view is None:
                self._send_json(404, {"error": error or "Unknown job."})
                return
            self._send_json(200, {"job": view})
            return
        if path.startswith("/api/jobs/") and path.endswith("/retry"):
            job_id = path[len("/api/jobs/") : -len("/retry")]
            view, error = retry_desk_job(job_id, jobs_dir=self.server.jobs_dir)
            if error or view is None:
                code = 404 if view is None else 400
                self._send_json(code, {"error": error or "Could not retry."})
                return
            self._send_json(200, {"job": view})
            return
        self._send_json(404, {"error": "Not found."})

    def _read_json(self) -> tuple[dict[str, Any], str | None]:
        try:
            length = int(self.headers.get("Content-Length") or "0")
        except ValueError:
            return {}, "Expected a JSON body."
        if length < 0 or length > 65_536:
            return {}, "Request is too large."
        raw = self.rfile.read(length) if length else b"{}"
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return {}, "Expected a JSON body."
        if not isinstance(data, dict):
            return {}, "Expected a JSON object."
        return data, None

    def _send(self, code: int, content_type: str, body: bytes) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, code: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload).encode("utf-8")
        self._send(code, "application/json; charset=utf-8", body)


def serve(
    *,
    host: str = "0.0.0.0",
    port: int = DEFAULT_PORT,
    jobs_dir: Path | None = None,
    calibre_port: int = DEFAULT_CALIBRE_PORT,
) -> DeskServer:
    from ao3kit.jobs import default_jobs_dir

    root = jobs_dir or default_jobs_dir()
    root.mkdir(parents=True, exist_ok=True)
    server = DeskServer((host, port), root, calibre_port=calibre_port)
    return server


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="ao3kit desk",
        description="Serve the Fanfic desk (AO3 search jobs and job status).",
    )
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument(
        "--calibre-port",
        type=int,
        default=DEFAULT_CALIBRE_PORT,
        help="Calibre content server port linked from the page (default 8081).",
    )
    parser.add_argument("--jobs-dir", default=None)
    args = parser.parse_args(list(argv or []))
    jobs_dir = Path(args.jobs_dir).expanduser().resolve() if args.jobs_dir else None
    server = serve(
        host=str(args.host),
        port=int(args.port),
        jobs_dir=jobs_dir,
        calibre_port=int(args.calibre_port),
    )
    bound = server.server_address
    print(f"Fanfic desk http://{bound[0]}:{bound[1]}/", file=sys.stderr)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("", file=sys.stderr)
    finally:
        server.server_close()
    return 0
