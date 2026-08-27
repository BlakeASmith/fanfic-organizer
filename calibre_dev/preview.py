"""Annotate preview GitHub releases once their commits land in a standard tag."""

from __future__ import annotations

import json
import os
import subprocess
import urllib.error
import urllib.request
from typing import Any, Callable
from urllib.parse import quote

from calibre_dev.release_urls import GITHUB_REPO
from calibre_dev.versioning import (
    included_in_release_notice,
    is_preview_tag,
    prepend_included_notice,
    release_tag_name,
)

GitHubRequest = Callable[[str, str, dict[str, Any] | None], Any]


def git_commit_for_tag(tag: str, *, cwd: str | os.PathLike[str]) -> str | None:
    proc = subprocess.run(
        ["git", "rev-parse", f"{tag}^{{commit}}"],
        cwd=cwd,
        check=False,
        capture_output=True,
        text=True,
    )
    sha = (proc.stdout or "").strip()
    if proc.returncode != 0 or not sha:
        return None
    return sha


def git_is_ancestor(
    ancestor: str,
    descendant: str,
    *,
    cwd: str | os.PathLike[str],
) -> bool:
    proc = subprocess.run(
        ["git", "merge-base", "--is-ancestor", ancestor, descendant],
        cwd=cwd,
        check=False,
        capture_output=True,
        text=True,
    )
    return proc.returncode == 0


def github_api_request(
    method: str,
    url: str,
    payload: dict[str, Any] | None = None,
    *,
    token: str | None = None,
    opener: Callable[..., Any] | None = None,
) -> Any:
    auth = (token or os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or "").strip()
    if not auth:
        raise RuntimeError("GITHUB_TOKEN is required to update preview releases")
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        method=method.upper(),
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {auth}",
            "User-Agent": "fanfic-organizer-release",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    if data is not None:
        request.add_header("Content-Type", "application/json")
    urlopen = opener or urllib.request.urlopen
    try:
        with urlopen(request, timeout=60) as response:
            raw = response.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace").strip()
        raise RuntimeError(
            f"GitHub {method} {url} failed ({exc.code}): {detail or exc.reason}"
        ) from exc
    if not raw:
        return None
    return json.loads(raw.decode("utf-8"))


def list_releases(
    repo: str,
    *,
    request: GitHubRequest | None = None,
) -> list[dict[str, Any]]:
    caller = request or (
        lambda method, url, payload=None: github_api_request(method, url, payload)
    )
    releases: list[dict[str, Any]] = []
    page = 1
    while True:
        url = (
            f"https://api.github.com/repos/{repo}/releases"
            f"?per_page=100&page={page}"
        )
        payload = caller("GET", url, None)
        if not isinstance(payload, list) or not payload:
            break
        releases.extend(item for item in payload if isinstance(item, dict))
        if len(payload) < 100:
            break
        page += 1
    return releases


def annotate_superseded_previews(
    version: str,
    *,
    repo: str = GITHUB_REPO,
    cwd: str | os.PathLike[str] | None = None,
    release_sha: str | None = None,
    token: str | None = None,
    request: GitHubRequest | None = None,
) -> list[str]:
    """Prepend a standard-release notice to preview releases whose commits are included."""
    tag = release_tag_name(version)
    root = os.fspath(cwd) if cwd is not None else os.getcwd()
    head = release_sha or git_commit_for_tag(tag, cwd=root)
    if not head:
        raise RuntimeError(f"could not resolve commit for {tag}")
    caller = request or (
        lambda method, url, payload=None: github_api_request(
            method, url, payload, token=token
        )
    )
    notice = included_in_release_notice(version, repo)
    updated: list[str] = []
    for release in list_releases(repo, request=caller):
        if release.get("draft") or not release.get("prerelease"):
            continue
        preview_tag = str(release.get("tag_name") or "").strip()
        if not is_preview_tag(preview_tag):
            continue
        preview_sha = git_commit_for_tag(preview_tag, cwd=root)
        if not preview_sha or not git_is_ancestor(preview_sha, head, cwd=root):
            continue
        body = str(release.get("body") or "")
        rewritten = prepend_included_notice(body, notice)
        if rewritten == body:
            continue
        release_id = release.get("id")
        if not release_id:
            continue
        encoded_repo = "/".join(quote(part, safe=".-") for part in repo.split("/"))
        caller(
            "PATCH",
            f"https://api.github.com/repos/{encoded_repo}/releases/{int(release_id)}",
            {"body": rewritten},
        )
        updated.append(preview_tag)
    return updated


__all__ = [
    "annotate_superseded_previews",
    "git_commit_for_tag",
    "git_is_ancestor",
    "github_api_request",
    "list_releases",
]
