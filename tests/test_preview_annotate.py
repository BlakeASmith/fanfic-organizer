"""Annotate preview GitHub releases once their commits land in a standard tag."""

from __future__ import annotations

import subprocess
from pathlib import Path

from calibre_dev.preview import annotate_superseded_previews, git_is_ancestor


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _init_repo(tmp_path: Path) -> tuple[Path, str, str]:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "dev@example.com")
    _git(repo, "config", "user.name", "Dev")
    _git(repo, "config", "commit.gpgsign", "false")
    (repo / "file.txt").write_text("one\n", encoding="utf-8")
    _git(repo, "add", "file.txt")
    _git(repo, "commit", "-m", "first")
    preview_sha = _git(repo, "rev-parse", "HEAD")
    _git(repo, "tag", "v0.31.0-preview.1+abc1234")
    (repo / "file.txt").write_text("two\n", encoding="utf-8")
    _git(repo, "commit", "-am", "release")
    _git(repo, "tag", "v0.31.0")
    (repo / "file.txt").write_text("three\n", encoding="utf-8")
    _git(repo, "commit", "-am", "later")
    later_preview = _git(repo, "rev-parse", "HEAD")
    _git(repo, "tag", "v0.32.0-preview.9+ffff000")
    return repo, preview_sha, later_preview


def test_git_is_ancestor(tmp_path: Path):
    repo, preview_sha, later_preview = _init_repo(tmp_path)
    release_sha = _git(repo, "rev-parse", "v0.31.0")
    assert git_is_ancestor(preview_sha, release_sha, cwd=repo)
    assert not git_is_ancestor(later_preview, release_sha, cwd=repo)


def test_annotate_superseded_previews_patches_included_only(tmp_path: Path):
    repo, preview_sha, later_preview = _init_repo(tmp_path)
    patched: list[tuple[str, dict]] = []
    releases = [
        {
            "id": 11,
            "tag_name": "v0.31.0-preview.1+abc1234",
            "prerelease": True,
            "draft": False,
            "body": "Preview notes",
        },
        {
            "id": 12,
            "tag_name": "v0.32.0-preview.9+ffff000",
            "prerelease": True,
            "draft": False,
            "body": "Future preview",
        },
        {
            "id": 13,
            "tag_name": "v0.30.0",
            "prerelease": False,
            "draft": False,
            "body": "stable",
        },
    ]

    def fake_request(method: str, url: str, payload=None):
        if method == "GET":
            return releases
        patched.append((url, payload))
        return {"ok": True}

    updated = annotate_superseded_previews(
        "0.31.0",
        repo="BlakeASmith/fanfic-organizer",
        cwd=repo,
        request=fake_request,
    )
    assert updated == ["v0.31.0-preview.1+abc1234"]
    assert len(patched) == 1
    assert patched[0][0].endswith("/releases/11")
    assert "v0.31.0" in patched[0][1]["body"]
    assert patched[0][1]["body"].startswith("> ⚠️")
    assert preview_sha
    assert later_preview
