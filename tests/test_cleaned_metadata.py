"""Tests for Calibre cleaned-metadata payload helpers."""

from __future__ import annotations

import importlib.util
from pathlib import Path

PLUGIN_CLEANED = Path(__file__).resolve().parents[1] / "calibre-plugin" / "cleaned.py"
PLUGIN_COLUMNS = Path(__file__).resolve().parents[1] / "calibre-plugin" / "columns.py"


def load_cleaned():
    spec = importlib.util.spec_from_file_location("ao3_cleaned", PLUGIN_CLEANED)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_columns():
    spec = importlib.util.spec_from_file_location("ao3_columns", PLUGIN_COLUMNS)
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


def test_format_remapping_summary_unique_across_works():
    mod = load_cleaned()
    records = [
        {
            "cleaned": {
                "tags": [
                    {
                        "original": "Kisses",
                        "mapped": "Kissing",
                        "status": "synonym",
                        "mapping_action": "default",
                    }
                ]
            }
        },
        {
            "cleaned": {
                "tags": [
                    {
                        "original": "Kisses",
                        "mapped": "Kissing",
                        "status": "synonym",
                        "mapping_action": "default",
                    }
                ]
            }
        },
    ]
    text = mod.format_remapping_summary(records)
    assert "Tag remappings (1 unique):" in text
    assert "Kisses → Kissing  [AO3 synonym]  (2 works)" in text
    assert mod.format_remapping_summary([]) == (
        "Tag remappings: none (all tags already canonical)"
    )


def test_cleaned_collection_names():
    mod = load_cleaned()
    record = {
        "cleaned": {"simplified": ["X"], "collections": {"River Song": ["a"], "DW": []}}
    }
    assert mod.cleaned_collection_names(record) == ["River Song", "DW"]


def test_work_id_from_url_and_book_match():
    mod = load_cleaned()
    assert mod.work_id_from_url("https://archiveofourown.org/works/79168296") == "79168296"
    assert (
        mod.work_id_from_url(
            "https://www.archiveofourown.org/works/79168296/chapters/1"
        )
        == "79168296"
    )
    assert mod.book_matches_work(
        {"url": "https://archiveofourown.org/works/9"},
        work_id="9",
    )
    assert mod.book_matches_work(
        {"ao3": "9"},
        url="https://archiveofourown.org/works/9",
    )
    assert not mod.book_matches_work({"url": "https://example.com/1"}, work_id="9")


def test_existing_book_id_from_identifiers_matches_ao3_and_url():
    mod = load_cleaned()
    books = [
        (1, {"ao3": "10", "url": "https://archiveofourown.org/works/10"}),
        (2, {"url": "https://www.archiveofourown.org/works/20"}),
        (3, {"isbn": "x"}),
    ]
    assert (
        mod.existing_book_id_from_identifiers(
            books, {"work_id": "10"}
        )
        == 1
    )
    assert (
        mod.existing_book_id_from_identifiers(
            books, {"url": "https://archiveofourown.org/works/20"}
        )
        == 2
    )
    assert (
        mod.existing_book_id_from_identifiers(books, {"work_id": "99"}) is None
    )


def test_existing_book_id_skips_empty_record():
    mod = load_cleaned()
    books = [(1, {"ao3": "10"})]
    assert mod.existing_book_id_from_identifiers(books, {}) is None


def test_calibre_fields_split_relationships_and_completed():
    mod = load_cleaned()
    record = {
        "work_id": "9",
        "url": "https://archiveofourown.org/works/9",
        "tags": ["Kisses", "Jegulus", "Regulus Black/James Potter"],
        "fandoms": ["Harry Potter - J. K. Rowling"],
        "cleaned": {
            "simplified": ["Kissing", "Jegulus", "Regulus Black/James Potter"],
            "collections": {"Jegulus": ["Jegulus"]},
            "fandoms": ["Harry Potter - J. K. Rowling"],
            "tags": [
                {
                    "original": "Kisses",
                    "mapped": "Kissing",
                    "category": "Additional Tags",
                    "dropped": False,
                },
                {
                    "original": "Jegulus",
                    "mapped": "Jegulus",
                    "category": "Additional Tags",
                    "dropped": False,
                },
                {
                    "original": "Regulus Black/James Potter",
                    "mapped": "Regulus Black/James Potter",
                    "category": "Relationship",
                    "dropped": False,
                },
            ],
            "source": "rules",
        },
        "metadata": {"words": 12000, "chapters": {"is_complete": True}},
    }
    fields = mod.calibre_fields_for_record(record)
    assert fields["fandoms"] == ["Harry Potter - J. K. Rowling"]
    assert fields["relationships"] == ["Regulus Black/James Potter"]
    assert fields["collections"] == ["Jegulus"]
    assert fields["tags"] == ["Kissing", "Jegulus", "Completed"]
    assert fields["original_tags"] == [
        "Kisses",
        "Jegulus",
        "Regulus Black/James Potter",
    ]
    assert fields["wordcount"] == 12000
    assert fields["identifiers"]["ao3"] == "9"
    assert fields["identifiers"]["url"] == "https://archiveofourown.org/works/9"

    split_tags = mod.tags_for_calibre_library(
        record,
        has_fandom_column=True,
        has_relationships_column=True,
        has_collections_column=True,
    )
    assert split_tags == ["Kissing", "Jegulus", "Completed"]
    assert "Harry Potter - J. K. Rowling" not in split_tags
    assert "Regulus Black/James Potter" not in split_tags

    bundled = mod.calibre_tags_for_record(record)
    assert bundled[0] == "Harry Potter - J. K. Rowling"
    assert "Kissing" in bundled
    assert "Completed" in bundled
    assert "Kisses" not in bundled


def test_calibre_fields_put_fandom_metatags_in_fandom_column():
    mod = load_cleaned()
    record = {
        "work_id": "1",
        "tags": ["Fluff"],
        "fandoms": ["Spider-Man - All Media Types"],
        "cleaned": {
            "simplified": ["Fluff"],
            "fandoms": ["Spider-Man - All Media Types", "Marvel"],
            "tags": [
                {
                    "original": "Fluff",
                    "mapped": "Fluff",
                    "category": "Additional Tags",
                    "dropped": False,
                }
            ],
            "source": "rules",
        },
    }
    fields = mod.calibre_fields_for_record(record)
    assert fields["fandoms"] == ["Spider-Man - All Media Types", "Marvel"]
    assert fields["tags"] == ["Fluff"]
    split_tags = mod.tags_for_calibre_library(record, has_fandom_column=True)
    assert split_tags == ["Fluff"]
    assert "Marvel" not in split_tags


def test_calibre_fields_heuristic_without_category_detail():
    mod = load_cleaned()
    record = {
        "work_id": "1",
        "tags": ["Fluff", "Frank Langdon/Mel King"],
        "fandoms": ["The Pitt (TV)"],
        "relationships": ["Frank Langdon/Mel King"],
        "cleaned": {
            "simplified": ["Fluff", "Frank Langdon/Mel King"],
            "fandoms": ["The Pitt (TV)"],
            "collections": {"The Pitt (Frank/Mel)": ["Frank Langdon/Mel King"]},
            "source": "raw",
        },
    }
    fields = mod.calibre_fields_for_record(record)
    assert fields["fandoms"] == ["The Pitt (TV)"]
    assert fields["relationships"] == ["Frank Langdon/Mel King"]
    assert fields["collections"] == ["The Pitt (Frank/Mel)"]
    assert fields["tags"] == ["Fluff"]
    assert fields["original_tags"] == ["Fluff", "Frank Langdon/Mel King"]


def test_calibre_fields_do_not_treat_slash_freeforms_as_relationships():
    mod = load_cleaned()
    record = {
        "work_id": "1",
        "tags": [
            "Hurt/Comfort",
            "Angst",
            "James 'Bucky' Barnes/Steve Rogers",
        ],
        "fandoms": ["Marvel Cinematic Universe"],
        "relationships": ["James 'Bucky' Barnes/Steve Rogers"],
        "cleaned": {
            "simplified": ["Hurt/Comfort", "Angst"],
            "fandoms": ["Marvel Cinematic Universe"],
            "relationships": ["James 'Bucky' Barnes/Steve Rogers"],
            "tags": [
                {
                    "original": "Hurt/Comfort",
                    "mapped": "Hurt/Comfort",
                    "category": "Additional Tags",
                    "dropped": False,
                },
                {
                    "original": "Angst",
                    "mapped": "Angst",
                    "category": "Additional Tags",
                    "dropped": False,
                },
                {
                    "original": "James 'Bucky' Barnes/Steve Rogers",
                    "mapped": "James 'Bucky' Barnes/Steve Rogers",
                    "category": "Relationship",
                    "dropped": False,
                },
            ],
            "source": "rules",
        },
    }
    fields = mod.calibre_fields_for_record(record)
    assert fields["relationships"] == ["James 'Bucky' Barnes/Steve Rogers"]
    assert fields["tags"] == ["Hurt/Comfort", "Angst"]


def test_calibre_fields_prefer_cleaned_relationships():
    mod = load_cleaned()
    record = {
        "work_id": "1",
        "tags": ["Drarry", "Fluff"],
        "fandoms": ["Harry Potter - J. K. Rowling"],
        "relationships": ["Drarry"],
        "cleaned": {
            "simplified": ["Fluff"],
            "fandoms": ["Harry Potter - J. K. Rowling"],
            "relationships": ["Harry Potter/Draco Malfoy"],
            "tags": [
                {
                    "original": "Drarry",
                    "mapped": "Harry Potter/Draco Malfoy",
                    "category": "Relationship",
                    "dropped": False,
                },
                {
                    "original": "Fluff",
                    "mapped": "Fluff",
                    "category": "Additional Tags",
                    "dropped": False,
                },
            ],
            "source": "rules",
        },
    }
    fields = mod.calibre_fields_for_record(record)
    assert fields["relationships"] == ["Harry Potter/Draco Malfoy"]
    assert fields["tags"] == ["Fluff"]
    assert "Drarry" not in fields["tags"]


def test_record_from_library_fields_reconstructs_fff_book():
    mod = load_cleaned()
    record = mod.record_from_library_fields(
        title="You're a Good Man, Frank Langdon",
        authors=["avocadomoon"],
        identifiers={"url": "https://archiveofourown.org/works/79168296"},
        tags=["FanFiction", "Completed", "The Pitt (TV)", "Pining", "Fluff"],
        fandoms=["The Pitt (TV)"],
        relationships=['Melissa "Mel" King/Frank Langdon'],
        wordcount=21548,
        is_complete=True,
    )
    assert record is not None
    assert record["work_id"] == "79168296"
    assert "FanFiction" not in record["tags"]
    assert "Completed" not in record["tags"]
    assert "The Pitt (TV)" not in record["tags"]
    assert "Pining" in record["tags"]
    assert 'Melissa "Mel" King/Frank Langdon' in record["tags"]
    assert record["fandoms"] == ["The Pitt (TV)"]
    assert record["metadata"]["words"] == 21548


def test_original_tag_names_prefers_cleaned_original():
    mod = load_cleaned()
    record = {
        "tags": ["Kissing"],
        "cleaned": {"original": ["Kisses", "Jegulus"], "simplified": ["Kissing"]},
    }
    assert mod.original_tag_names(record) == ["Kisses", "Jegulus"]


def test_record_from_library_fields_prefers_original_tags_over_cleaned_tags():
    mod = load_cleaned()
    record = mod.record_from_library_fields(
        identifiers={"url": "https://archiveofourown.org/works/9"},
        tags=["Kissing", "Completed"],
        original_tags=["Kisses", "Jegulus"],
        relationships=["Regulus Black/James Potter"],
        fandoms=["Harry Potter - J. K. Rowling"],
    )
    assert record is not None
    assert record["tags"] == ["Kisses", "Jegulus"]
    assert "Regulus Black/James Potter" not in record["tags"]
    assert "Kissing" not in record["tags"]
    assert "Completed" not in record["tags"]
    assert record["fandoms"] == ["Harry Potter - J. K. Rowling"]
    assert record["relationships"] == ["Regulus Black/James Potter"]


def test_record_from_library_fields_prefers_raw_json():
    mod = load_cleaned()
    record = mod.record_from_library_fields(
        identifiers={"url": "https://archiveofourown.org/works/9"},
        raw_record={
            "work_id": "9",
            "tags": ["Kisses"],
            "cleaned": {"simplified": ["Kissing"]},
        },
    )
    assert record is not None
    assert record["work_id"] == "9"
    assert record["tags"] == ["Kisses"]
    assert "cleaned" not in record


def test_record_from_library_fields_uses_comments_for_summary():
    mod = load_cleaned()
    record = mod.record_from_library_fields(
        title="A Work",
        identifiers={"url": "https://archiveofourown.org/works/9"},
        comments="<p>They were roommates.</p>",
    )
    assert record is not None
    assert record["summary"] == "They were roommates."


def test_record_from_library_fields_prefers_summary_column_over_comments():
    mod = load_cleaned()
    record = mod.record_from_library_fields(
        title="A Work",
        identifiers={"url": "https://archiveofourown.org/works/9"},
        summary="Column synopsis.",
        comments="Comments synopsis.",
    )
    assert record is not None
    assert record["summary"] == "Column synopsis."


def test_calibre_fields_for_record_includes_summary():
    mod = load_cleaned()
    fields = mod.calibre_fields_for_record(
        {
            "work_id": "9",
            "title": "A Work",
            "summary": "They were roommates.",
        }
    )
    assert fields["summary"] == "They were roommates."


def test_layout_column_specs_match_fanfic_library():
    cols = load_columns()
    by_role = {spec["role"]: spec for spec in cols.LAYOUT_COLUMN_SPECS}
    assert by_role["fandom"]["label"] == "fandom"
    assert by_role["fandom"]["name"] == "Fandom"
    assert by_role["fandom"]["is_multiple"] is True
    assert by_role["relationships"]["label"] == "relationships"
    assert by_role["collections"]["label"] == "collections"
    assert by_role["originaltags"]["label"] == "originaltags"
    assert by_role["originaltags"]["name"] == "Original Tags"
    assert by_role["originaltags"]["is_multiple"] is True
    assert by_role["summary"]["label"] == "summary"
    assert by_role["summary"]["name"] == "Summary"
    assert by_role["summary"]["datatype"] == cols.COMMENTS_DATATYPE
    assert by_role["wordcount"]["label"] == "wordcount"
    assert by_role["wordcount"]["name"] == "word count"
    assert by_role["wordcount"]["datatype"] == "int"
    assert by_role["wordcount"]["is_multiple"] is False


class _FakeBackend:
    def __init__(self, labels):
        self.custom_column_label_map = {label: {'label': label} for label in labels}


class _FakeDB:
    def __init__(self, live=(), library_path='/tmp/lib'):
        self.backend = _FakeBackend(live)
        self.field_metadata = {f'#{label}': {'label': label} for label in live}
        self.library_path = library_path
        self.created = []

    def create_custom_column(self, label, name, datatype, is_multiple=False, editable=True, display=None):
        self.created.append(label)


class _FakeGUI:
    def __init__(self, db):
        self.current_db = db
        self.moved = []

    def library_moved(self, path):
        self.moved.append(path)
        for label in self.current_db.created:
            self.current_db.backend.custom_column_label_map[label] = {'label': label}


def test_custom_label_is_live_uses_backend_map_not_sql():
    cols = load_columns()
    db = _FakeDB(live=('fandom',))
    assert cols.custom_label_is_live(db, 'fandom') is True
    assert cols.custom_label_is_live(db, '#relationships') is False


def test_ensure_layout_columns_returns_pending_when_not_live():
    cols = load_columns()
    db = _FakeDB(live=())
    pending = cols.ensure_layout_columns(db)
    assert pending == [
        'fandom',
        'relationships',
        'collections',
        'originaltags',
        'summary',
        'wordcount',
    ]
    assert db.created == pending
    # Still not live until the library is reopened — same as Calibre.
    assert cols.custom_label_is_live(db, 'fandom') is False


def test_apply_layout_columns_reopens_library_when_columns_were_created():
    cols = load_columns()
    db = _FakeDB(live=())
    gui = _FakeGUI(db)
    cols.apply_layout_columns(gui)
    assert gui.moved == ['/tmp/lib']
    assert cols.custom_label_is_live(db, 'fandom') is True


def test_apply_layout_columns_skips_reopen_when_already_live():
    cols = load_columns()
    db = _FakeDB(live=('fandom', 'relationships', 'collections', 'originaltags', 'summary', 'wordcount'))
    gui = _FakeGUI(db)
    cols.apply_layout_columns(gui)
    assert gui.moved == []
    assert db.created == []


def test_calibre_fields_include_series():
    mod = load_cleaned()
    record = {
        "work_id": "90876776",
        "url": "https://archiveofourown.org/works/90876776",
        "title": "Time Storm",
        "tags": ["Fluff"],
        "series": [
            {
                "series_id": "6133236",
                "name": "Doctor Who:Predators of time and space",
                "url": "https://archiveofourown.org/series/6133236",
                "position": 2,
            }
        ],
    }
    fields = mod.calibre_fields_for_record(record)
    assert fields["series"] == "Doctor Who:Predators of time and space"
    assert fields["series_index"] == 2.0
    assert fields["series_id"] == "6133236"
    assert fields["identifiers"]["ao3series"] == "6133236"


def test_parse_ao3_date_accepts_iso_and_blurb_forms():
    mod = load_cleaned()
    from datetime import date

    assert mod.parse_ao3_date("2026-08-21") == date(2026, 8, 21)
    assert mod.parse_ao3_date("21 Aug 2026") == date(2026, 8, 21)
    assert mod.parse_ao3_date("01 Jan 2020") == date(2020, 1, 1)
    assert mod.parse_ao3_date("") is None
    assert mod.parse_ao3_date(None) is None
    assert mod.parse_ao3_date("not a date") is None


def test_calibre_fields_include_publisher_and_published():
    mod = load_cleaned()
    from datetime import date

    fields = mod.calibre_fields_for_record(
        {
            "work_id": "1",
            "title": "A Work",
            "date": "2026-08-21",
            "tags": ["Fluff"],
        }
    )
    assert fields["publisher"] == "Archive of Our Own"
    assert fields["published"] == date(2026, 8, 21)

    blurbs = mod.calibre_fields_for_record(
        {
            "work_id": "2",
            "title": "Blurb Work",
            "date": "21 Aug 2026",
        }
    )
    assert blurbs["published"] == date(2026, 8, 21)

    missing = mod.calibre_fields_for_record({"work_id": "3", "title": "No Date"})
    assert missing["publisher"] == "Archive of Our Own"
    assert missing["published"] is None


def test_record_from_library_restores_series_identifier():
    mod = load_cleaned()
    record = mod.record_from_library_fields(
        title="Time Storm",
        identifiers={
            "ao3": "90876776",
            "url": "https://archiveofourown.org/works/90876776",
            "ao3series": "6133236",
        },
        series_name="Doctor Who:Predators of time and space",
        series_index=2,
    )
    assert record is not None
    assert record["series"][0]["series_id"] == "6133236"
    assert record["series"][0]["position"] == 2
    assert record["series"][0]["name"] == "Doctor Who:Predators of time and space"


def test_series_writeback_from_record():
    mod = load_cleaned()
    record = {
        "work_id": "90876776",
        "url": "https://archiveofourown.org/works/90876776",
        "title": "Time Storm",
        "series": [
            {
                "series_id": "6133236",
                "name": "Doctor Who:Predators of time and space",
                "url": "https://archiveofourown.org/series/6133236",
                "position": 2,
            }
        ],
    }
    patch = mod.series_writeback_from_record(record)
    assert patch["in_series"] is True
    assert patch["series"] == "Doctor Who:Predators of time and space"
    assert patch["series_index"] == 2.0
    assert patch["identifiers"]["ao3series"] == "6133236"


def test_series_writeback_standalone_does_not_claim_series():
    mod = load_cleaned()
    patch = mod.series_writeback_from_record(
        {
            "work_id": "100",
            "url": "https://archiveofourown.org/works/100",
            "title": "Standalone",
        }
    )
    assert patch["in_series"] is False
    assert patch["series"] is None
    assert "ao3series" not in patch["identifiers"]


def test_collections_writeback_replaces_computed_set():
    mod = load_cleaned()
    assert mod.collections_writeback(["River Song"], []) == []
    assert mod.collections_writeback([], ["River Song"]) == ["River Song"]
    assert mod.collections_writeback(["River Song"], ["River Song"]) is None
    assert mod.collections_writeback(["A", "B"], ["B"]) == ["B"]
    assert mod.collections_writeback(["River Song"], ["river song"]) is None


def test_collect_collection_lines_and_summary():
    mod = load_cleaned()
    records = [
        {
            "cleaned": {
                "collections": {"River Song": ["Melody Pond", "River Song - Freeform"]}
            }
        },
        {"cleaned": {"collections": {"River Song": ["Melody Pond"]}}},
        {"cleaned": {"collections": {}}},
    ]
    lines = mod.collect_collection_lines(records)
    assert "Melody Pond → River Song  (2 works)" in lines
    assert "River Song - Freeform → River Song" in lines
    text = mod.format_collection_summary(records)
    assert "Collection assignments (2 unique)" in text
    assert mod.format_collection_summary([]) == "Collection assignments: none"
