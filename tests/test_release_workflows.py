"""Guardrails for preview / PR / release GitHub Actions workflows."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"


def _read(name: str) -> str:
    return (WORKFLOWS / name).read_text(encoding="utf-8")


def test_preview_workflow_does_not_push_and_marks_prerelease():
    text = _read("preview-release.yml")
    assert "on:\n  push:\n    branches: [main]" in text
    assert "prerelease: true" in text
    assert "make_latest: false" in text
    assert "git commit" not in text
    assert "git push" not in text
    assert "contents: write" in text
    assert "softprops/action-gh-release" in text
    assert "preview-meta" in text


def test_pr_workflow_publishes_prerelease():
    text = _read("pr-build.yml")
    assert "pull_request" in text
    assert "softprops/action-gh-release" in text
    assert "prerelease: true" in text
    assert "make_latest: false" in text
    assert "contents: write" in text
    assert "pull-requests: write" in text
    assert "pr-meta" in text
    assert "upload-artifact" not in text
    assert "CI test build" not in text
    assert "PR plugin pre-release" in text


def test_release_workflow_is_manual_with_bump_and_skips_preview_tags():
    text = _read("release.yml")
    assert "workflow_dispatch" in text
    assert "bump_type" in text
    assert "python makeplugin.py release --publish --bump" in text
    assert "!contains(github.ref_name, 'preview')" in text
    assert "annotate-previews" in text
    assert "fanfic-organizer.zip" not in text
    assert "cp " not in text
    assert 'files: ${{ steps.meta.outputs.zip_name }}' in text
    assert "name: Build plugin zip" in text
