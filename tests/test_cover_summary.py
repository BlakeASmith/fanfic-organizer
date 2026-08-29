from __future__ import annotations

import importlib.util
from pathlib import Path

from ao3kit.covers import (
    resolve_record_summary as lib_resolve_record_summary,
    summary_text_from_comments as lib_summary_text_from_comments,
)

PLUGIN_COVER_SUMMARY = (
    Path(__file__).resolve().parents[1] / "calibre-plugin" / "cover_summary.py"
)


def load_cover_summary():
    spec = importlib.util.spec_from_file_location(
        "ao3_cover_summary", PLUGIN_COVER_SUMMARY
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_summary_text_from_comments_matches_library():
    mod = load_cover_summary()
    blob = '{"work_id": "123", "tags": ["a"]}'
    assert mod.summary_text_from_comments(blob) == lib_summary_text_from_comments(blob)
    assert (
        mod.summary_text_from_comments("Plain synopsis text.")
        == lib_summary_text_from_comments("Plain synopsis text.")
    )


def test_resolve_record_summary_matches_library():
    mod = load_cover_summary()
    record = {"summary": "Record blurb."}
    assert mod.resolve_record_summary(record) == lib_resolve_record_summary(record)
    assert (
        mod.resolve_record_summary(record, comments="Comments blurb.")
        == lib_resolve_record_summary(record, comments="Comments blurb.")
    )
    assert (
        mod.resolve_record_summary(
            {"summary": "Record blurb."},
            comments="Comments blurb.",
        )
        == lib_resolve_record_summary(
            {"summary": "Record blurb."},
            comments="Comments blurb.",
        )
    )


def test_cover_summary_module_does_not_import_ao3kit():
    text = PLUGIN_COVER_SUMMARY.read_text(encoding="utf-8")
    for line in text.splitlines():
        if line.startswith((" ", "\t")):
            continue
        stripped = line.strip()
        if stripped.startswith("from ao3kit") or stripped.startswith("import ao3kit"):
            raise AssertionError(f"cover_summary.py imports ao3kit: {stripped}")
