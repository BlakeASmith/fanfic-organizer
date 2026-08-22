from __future__ import annotations

import json
from pathlib import Path

import pytest

from ao3kit.scrape import SearchCriteria, main as scrape_main
from ao3kit.similar import (
    SimilarSelect,
    build_select,
    characters_from_relationship,
    criteria_from_selection,
    facets_from_records,
    records_from_jsonl_text,
    selection_to_fields,
    similar_payload,
)


CLANDESTINE = {
    "work_id": "50448730",
    "title": "Clandestine",
    "author": "my_castlescrumbling",
    "fandoms": ["Harry Potter - J. K. Rowling"],
    "tags": [
        "Graphic Depictions Of Violence",
        "Regulus Black & Sirius Black",
        "Regulus Black/James Potter",
        "Sirius Black/Remus Lupin",
        "Regulus Black",
        "Sirius Black",
        "James Potter",
        "Remus Lupin",
        "Marauders Era (Harry Potter)",
        "Slow Burn",
        "Fluff and Angst",
        "Alternate Universe - Everyone Lives/Nobody Dies",
        "Completed",
    ],
}

PITT = {
    "work_id": "9",
    "title": "You're a Good Man, Frank Langdon",
    "authors": ["avocadomoon"],
    "fandoms": ["The Pitt (TV)"],
    "relationships": ['Melissa "Mel" King/Frank Langdon'],
    "tags": ["Pining", "Fluff", "FanFiction", "Completed"],
}


def test_classifies_ships_characters_and_skips_warnings():
    facets = facets_from_records([CLANDESTINE])
    assert facets.fandoms == ["Harry Potter - J. K. Rowling"]
    assert facets.authors == ["my_castlescrumbling"]
    assert "Regulus Black/James Potter" in facets.relationships
    assert "Regulus Black & Sirius Black" in facets.relationships
    assert "Regulus Black" in facets.characters
    assert "James Potter" in facets.characters
    assert "Slow Burn" in facets.tags
    assert "Marauders Era (Harry Potter)" in facets.tags
    assert "Alternate Universe - Everyone Lives/Nobody Dies" in facets.tags
    assert "Graphic Depictions Of Violence" not in facets.tags
    assert "Completed" not in facets.tags
    assert "Regulus Black" not in facets.tags


def test_characters_from_platonic_group():
    names = characters_from_relationship(
        "Regulus Black & Barty Crouch Jr. & Pandora Lovegood"
    )
    assert names == ["Regulus Black", "Barty Crouch Jr.", "Pandora Lovegood"]


def test_merge_unions_and_counts():
    other = {
        "work_id": "2",
        "title": "Other",
        "author": "greyeyedmonster18",
        "fandoms": ["Harry Potter - J. K. Rowling"],
        "tags": ["Sirius Black/Remus Lupin", "Slow Burn", "Found Family"],
    }
    facets = facets_from_records([CLANDESTINE, other])
    assert facets.authors == ["my_castlescrumbling", "greyeyedmonster18"]
    assert facets.counts["fandoms"]["Harry Potter - J. K. Rowling"] == 2
    assert facets.counts["tags"]["Slow Burn"] == 2
    assert facets.counts["tags"]["Found Family"] == 1
    assert "Found Family" in facets.tags
    assert facets.titles == ["Clandestine", "Other"]


def test_default_select_is_fandoms_only():
    facets = facets_from_records([CLANDESTINE])
    select = SimilarSelect.default_for(facets)
    fields = selection_to_fields(select)
    assert fields["tag_id"] == "Harry Potter - J. K. Rowling"
    assert fields["other_tag_names"] == ""
    assert fields["creators"] == ""


def test_selection_maps_extra_fandoms_and_tags():
    select = SimilarSelect(
        fandoms=["Harry Potter - J. K. Rowling", "Doctor Who (2005)"],
        relationships=["Regulus Black/James Potter"],
        characters=["Regulus Black"],
        tags=["Slow Burn"],
        authors=["my_castlescrumbling"],
        excluded_tags=["Major Character Death"],
    )
    fields = selection_to_fields(select)
    assert fields["tag_id"] == "Harry Potter - J. K. Rowling"
    assert "Doctor Who (2005)" in fields["other_tag_names"]
    assert "Regulus Black/James Potter" in fields["other_tag_names"]
    assert "Slow Burn" in fields["other_tag_names"]
    assert fields["creators"] == "my_castlescrumbling"
    assert fields["excluded_tag_names"] == "Major Character Death"
    criteria = criteria_from_selection(select)
    assert criteria.is_usable()
    assert criteria.creators == "my_castlescrumbling"


def test_build_select_include_all_relationships():
    facets = facets_from_records([CLANDESTINE])
    select = build_select(
        facets,
        include_all=["relationships"],
        picks=SimilarSelect(characters=["Regulus Black"]),
    )
    assert select.fandoms == facets.fandoms
    assert select.relationships == facets.relationships
    assert select.characters == ["Regulus Black"]
    assert select.tags == []


def test_explicit_column_relationships_and_fff_injected():
    facets = facets_from_records([PITT])
    assert facets.fandoms == ["The Pitt (TV)"]
    assert facets.authors == ["avocadomoon"]
    assert facets.relationships == ['Melissa "Mel" King/Frank Langdon']
    assert "Frank Langdon" in facets.characters
    assert "Fluff" in facets.tags
    assert "FanFiction" not in facets.tags
    assert "Completed" not in facets.tags


def test_cleaned_category_detail():
    record = {
        "title": "T",
        "author": "A",
        "fandoms": ["Doctor Who (2005)"],
        "cleaned": {
            "original": ["Amy Pond", "Amy Pond/Rory Williams", "Fluff"],
            "tags": [
                {
                    "original": "Amy Pond",
                    "mapped": "Amy Pond",
                    "category": "Character",
                },
                {
                    "original": "Amy Pond/Rory Williams",
                    "mapped": "Amy Pond/Rory Williams",
                    "category": "Relationship",
                },
                {"original": "Fluff", "mapped": "Fluff", "category": "Freeform"},
            ],
        },
    }
    facets = facets_from_records([record])
    assert facets.characters == ["Amy Pond", "Rory Williams"]
    assert facets.relationships == ["Amy Pond/Rory Williams"]
    assert facets.tags == ["Fluff"]


def test_similar_payload_default_search_url():
    payload = similar_payload([CLANDESTINE])
    assert payload["criteria"]["tag_id"] == "Harry Potter - J. K. Rowling"
    assert "archiveofourown.org/works?" in payload["search_url"]
    assert "Harry" in payload["search_url"]
    assert payload["facets"]["authors"] == ["my_castlescrumbling"]


def test_records_from_jsonl_allows_missing_work_id():
    records = records_from_jsonl_text('{"title": "X", "fandoms": ["Naruto"]}\n')
    assert records[0]["fandoms"] == ["Naruto"]


def test_parse_similar_cli(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    src = tmp_path / "seed.jsonl"
    src.write_text(json.dumps(CLANDESTINE) + "\n", encoding="utf-8")
    rc = scrape_main(["--parse-similar", "--similar-from", str(src)])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["criteria"]["tag_id"] == "Harry Potter - J. K. Rowling"
    assert "Regulus Black/James Potter" in payload["facets"]["relationships"]


def test_parse_similar_include_and_work_id(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
):
    src = tmp_path / "seed.jsonl"
    src.write_text(
        json.dumps(CLANDESTINE) + "\n" + json.dumps(PITT) + "\n",
        encoding="utf-8",
    )
    rc = scrape_main(
        [
            "--parse-similar",
            "--similar-from",
            str(src),
            "--similar-work-id",
            "50448730",
            "--similar-include",
            "relationships",
            "--similar-character",
            "Regulus Black",
        ]
    )
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert len(payload["works"]) == 1
    assert payload["select"]["characters"] == ["Regulus Black"]
    assert "Regulus Black/James Potter" in payload["select"]["relationships"]
    assert "Regulus Black/James Potter" in payload["criteria"]["other_tag_names"]


def test_creators_round_trip_in_search_url():
    criteria = SearchCriteria(
        tag_id="Naruto",
        creators="kishimoto",
        other_tag_names="Fluff",
    )
    from ao3kit.scrape import build_search_url, parse_search_url

    url = build_search_url(criteria)
    parsed, _page = parse_search_url(url)
    assert parsed.creators == "kishimoto"
    assert parsed.other_tag_names == "Fluff"
    assert parsed.is_usable()


def test_plugin_similar_matches_library():
    import importlib.util
    import sys

    plugin_path = Path(__file__).resolve().parents[1] / "calibre-plugin" / "similar.py"
    spec = importlib.util.spec_from_file_location("plugin_similar", plugin_path)
    assert spec is not None and spec.loader is not None
    plugin = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = plugin
    spec.loader.exec_module(plugin)
    records = [CLANDESTINE, PITT]
    assert plugin.facets_from_records(records).to_dict() == facets_from_records(
        records
    ).to_dict()
    select = SimilarSelect(
        fandoms=["Harry Potter - J. K. Rowling"],
        relationships=["Regulus Black/James Potter"],
    )
    assert plugin.selection_to_fields(select) == selection_to_fields(select)
