"""GitHub Actions metadata helpers."""

from __future__ import annotations

from pathlib import Path

from calibre_dev.changelog import format_version, next_0x_version, read_plugin_version
from calibre_dev.ci import main as ci_main
from calibre_dev.versioning import format_preview_version, plugin_zip_name


def test_preview_meta_writes_github_output_and_notes(
    tmp_path: Path, monkeypatch, capsys
):
    output = tmp_path / "github-output"
    notes = tmp_path / "notes.md"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    assert (
        ci_main(
            [
                "preview-meta",
                "--build",
                "452",
                "--sha",
                "7a4f9b2deadbeef",
                "--notes-file",
                str(notes),
            ]
        )
        == 0
    )
    expected = format_preview_version(
        next_0x_version(read_plugin_version()), 452, "7a4f9b2deadbeef"
    )
    text = output.read_text(encoding="utf-8")
    assert f"version={expected}" in text
    assert f"tag=v{expected}" in text
    assert f"zip_name={plugin_zip_name(expected)}" in text
    body = notes.read_text(encoding="utf-8")
    assert "automated **preview**" in body
    assert capsys.readouterr().out == ""


def test_pr_meta_writes_tag_zip_and_notes(tmp_path: Path, monkeypatch):
    output = tmp_path / "github-output"
    notes = tmp_path / "pr-notes.md"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    assert (
        ci_main(
            [
                "pr-meta",
                "--pr",
                "12",
                "--sha",
                "abcdef1",
                "--pr-url",
                "https://github.com/BlakeASmith/fanfic-organizer/pull/12",
                "--notes-file",
                str(notes),
            ]
        )
        == 0
    )
    text = output.read_text(encoding="utf-8")
    base = format_version(next_0x_version(read_plugin_version()))
    version = f"{base}-pr.12+abcdef1"
    assert f"version={version}" in text
    assert f"tag=v{version}" in text
    assert f"zip_name={plugin_zip_name(version)}" in text
    body = notes.read_text(encoding="utf-8")
    assert "PR #12" in body
    assert "PR pre-release" in body
