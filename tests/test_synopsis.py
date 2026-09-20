"""Synopsis repair for Kobo / device Comments."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

from ao3kit.synopsis import (
    classify_synopsis,
    count_synopsis_states,
    enrich_record_synopsis,
    repair_jsonl_records,
    repair_record_synopsis,
)

PLUGIN = Path(__file__).resolve().parents[1] / "calibre-plugin"


def load_cover_summary():
    spec = importlib.util.spec_from_file_location(
        "ao3_cover_summary_synopsis", PLUGIN / "cover_summary.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_classify_synopsis_matches_cover_summary():
    mod = load_cover_summary()
    blob = '{"work_id": "9", "tags": ["Fluff"]}'
    assert classify_synopsis(blob, "Blurb.", work_id="9") == mod.classify_synopsis(
        blob, "Blurb.", work_id="9"
    )
    assert classify_synopsis(blob, "", work_id="9") == "needs_fetch"
    assert classify_synopsis("User synopsis.", "Blurb.", work_id="9") == "ok"


def test_enrich_record_synopsis_fills_from_summary_column():
    row = enrich_record_synopsis(
        {
            "comments": '{"work_id": "1"}',
            "summary": "Cover blurb.",
        }
    )
    assert row["summary"] == "Cover blurb."


def test_repair_record_synopsis_from_summary_column():
    patch = repair_record_synopsis(
        {
            "work_id": "1",
            "comments": '{"work_id": "1", "tags": ["a"]}',
            "summary": "They were roommates.",
        }
    )
    assert patch == {
        "comments": "They were roommates.",
    }


def test_repair_jsonl_records(tmp_path: Path):
    path = tmp_path / "works.jsonl"
    rows = [
        {
            "work_id": "1",
            "comments": '{"work_id": "1"}',
            "summary": "Blurb one.",
        },
        {"work_id": "2", "comments": "Already set.", "summary": "Ignored."},
    ]
    path.write_text(
        "\n".join(json.dumps(row) for row in rows) + "\n",
        encoding="utf-8",
    )
    repaired, n = repair_jsonl_records(rows)
    assert n == 1
    assert repaired[0]["comments"] == "Blurb one."
    counts = count_synopsis_states(repaired)
    assert counts["ok"] >= 1
