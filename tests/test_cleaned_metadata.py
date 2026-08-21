"""Tests for Calibre cleaned-metadata payload helpers."""

from __future__ import annotations

import importlib.util
from pathlib import Path

PLUGIN_CLEANED = Path(__file__).resolve().parents[1] / "calibre-plugin" / "cleaned.py"


def load_cleaned():
    spec = importlib.util.spec_from_file_location("ao3_cleaned", PLUGIN_CLEANED)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_build_cleaned_payload_from_explicit_cleaned():
    mod = load_cleaned()
    record = {
        "work_id": "1",
        "title": "T",
        "tags": ["raw"],
        "cleaned": {
            "simplified": ["Kissing"],
            "collections": {"Fluff": ["raw"]},
        },
    }
    payload = mod.build_cleaned_payload(record)
    assert payload["simplified"] == ["Kissing"]
    assert payload["collections"]["Fluff"] == ["raw"]
    assert payload["work_id"] == "1"


def test_build_cleaned_payload_from_rules_shaped_tags():
    mod = load_cleaned()
    record = {
        "work_id": "2",
        "title": "T",
        "tags": {
            "simplified": ["River Song", "Fluff"],
            "collections": {"River Song": ["Melody Pond"]},
            "dropped": ["Melody Pond"],
            "original": ["Melody Pond", "Fluff"],
        },
        "fandoms": ["Doctor Who (2005)"],
    }
    payload = mod.build_cleaned_payload(record)
    assert payload["source"] == "rules"
    assert payload["simplified"] == ["River Song", "Fluff"]
    assert payload["fandoms"] == ["Doctor Who (2005)"]


def test_build_cleaned_payload_falls_back_to_raw_tags():
    mod = load_cleaned()
    record = {
        "work_id": "3",
        "tags": ["A", "B"],
        "fandoms": ["F"],
    }
    payload = mod.build_cleaned_payload(record)
    assert payload["source"] == "raw"
    assert payload["simplified"] == ["A", "B"]
    assert mod.cleaned_tag_names(record) == ["A", "B"]


def test_cleaned_collection_names():
    mod = load_cleaned()
    record = {
        "cleaned": {"simplified": ["X"], "collections": {"River Song": ["a"], "DW": []}}
    }
    assert mod.cleaned_collection_names(record) == ["River Song", "DW"]
