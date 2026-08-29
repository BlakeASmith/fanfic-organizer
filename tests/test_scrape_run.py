from __future__ import annotations

import importlib.util
import json
from pathlib import Path

from ao3kit.scrape import SORT_OPTIONS, SearchCriteria


PLUGIN_SCRAPE_RUN = Path(__file__).resolve().parents[1] / "calibre-plugin" / "scrape_run.py"


def load_scrape_run():
    spec = importlib.util.spec_from_file_location("ao3_scrape_run", PLUGIN_SCRAPE_RUN)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_sort_options_match_library():
    mod = load_scrape_run()
    assert list(mod.SORT_OPTIONS) == list(SORT_OPTIONS)


def test_criteria_from_options_round_trips_to_search_criteria():
    mod = load_scrape_run()
    options = {
        "tag_id": "Doctor Who (2005)",
        "query": "amy/rory",
        "sort_column": "hits",
        "complete": "true",
        "language_id": "en",
        "words_from": "1000",
        "relationship_ids": "1110, 2220",
        "other_tag_names": "Fluff",
        "creators": "nianeyna",
    }
    data = mod.criteria_from_options(options)
    criteria = SearchCriteria.from_dict(data)
    assert criteria.tag_id == "Doctor Who (2005)"
    assert criteria.query == "amy/rory"
    assert criteria.sort_column == "hits"
    assert criteria.complete is True
    assert criteria.words_from == 1000
    assert criteria.relationship_ids == [1110, 2220]
    assert criteria.other_tag_names == "Fluff"
    assert criteria.creators == "nianeyna"


def test_uses_url_until_form_is_source_of_truth():
    mod = load_scrape_run()
    options = {"url": "https://archiveofourown.org/works?tag_id=X", "use_form_criteria": False}
    assert mod.uses_url_search(options) is True
    options["use_form_criteria"] = True
    assert mod.uses_url_search(options) is False
    assert mod.uses_url_search({"url": "", "use_form_criteria": False}) is False


def test_build_scrape_argv_from_url():
    mod = load_scrape_run()
    argv = mod.build_scrape_argv(
        {
            "url": "https://archiveofourown.org/works?tag_id=X",
            "use_form_criteria": False,
            "max_results": "25",
            "min_kudos": "50",
            "complete_only": True,
        },
        output="/tmp/results.jsonl",
    )
    assert argv[:4] == ["scrape", "-o", "/tmp/results.jsonl", "--verbose"]
    assert "--url" in argv
    assert argv[argv.index("--url") + 1].endswith("tag_id=X")
    assert argv[argv.index("--max-results") + 1] == "25"
    assert argv[argv.index("--min-kudos") + 1] == "50"
    assert "--complete-only" in argv
    assert "--delay" not in argv
    assert "--criteria-file" not in argv
    assert "--start-page" not in argv


def test_build_scrape_argv_from_criteria_file(tmp_path: Path):
    mod = load_scrape_run()
    criteria_file = tmp_path / "criteria.json"
    options = {
        "url": "https://archiveofourown.org/works?tag_id=Ignored",
        "use_form_criteria": True,
        "tag_id": "Doctor Who (2005)",
        "start_page": "3",
        "username": "emily",
        "password": "secret",
    }
    argv = mod.build_scrape_argv(
        options,
        output=str(tmp_path / "results.jsonl"),
        criteria_file=str(criteria_file),
    )
    assert "--url" not in argv
    assert argv[argv.index("--criteria-file") + 1] == str(criteria_file)
    assert argv[argv.index("--start-page") + 1] == "3"
    assert argv[argv.index("--username") + 1] == "emily"
    assert argv[argv.index("--password") + 1] == "secret"


def test_prepare_scrape_command_writes_criteria(tmp_path: Path):
    mod = load_scrape_run()
    argv, jsonl = mod.prepare_scrape_command(
        {"tag_id": "Naruto", "use_form_criteria": True, "max_results": "10"},
        tmp_path,
    )
    assert jsonl == tmp_path / "results.jsonl"
    criteria_path = tmp_path / "criteria.json"
    assert criteria_path.is_file()
    assert "--criteria-file" in argv
    assert "--download" not in argv
    assert "Naruto" in criteria_path.read_text(encoding="utf-8")


def test_prepare_scrape_command_download_uses_one_scrape_run(tmp_path: Path):
    mod = load_scrape_run()
    argv, jsonl = mod.prepare_scrape_command(
        {
            "tag_id": "Naruto",
            "use_form_criteria": True,
            "download_epubs": True,
        },
        tmp_path,
    )
    dest = tmp_path / "bundle"
    assert jsonl == dest / "results.jsonl"
    assert dest.is_dir()
    assert "--download" in argv
    assert argv[argv.index("--epub-dir") + 1] == str(dest)
    assert "--no-zip" in argv
    assert "--no-simplify" in argv
    assert argv[0] == "scrape"
    assert "download" not in argv[1:]


def test_build_download_argv_skips_zip_and_auto_simplify():
    mod = load_scrape_run()
    argv = mod.build_download_argv("/tmp/results.jsonl", "/tmp/bundle", {})
    assert argv[:3] == ["download", "-i", "/tmp/results.jsonl"]
    assert "--no-zip" in argv
    assert "--no-simplify" in argv
    assert "--verbose" in argv
    assert "--delay" not in argv


def test_build_download_argv_cover_flag():
    mod = load_scrape_run()
    with_cover = mod.build_download_argv(
        "/tmp/results.jsonl", "/tmp/bundle", {"cover": True}
    )
    without = mod.build_download_argv(
        "/tmp/results.jsonl", "/tmp/bundle", {"cover": False}
    )
    default = mod.build_download_argv("/tmp/results.jsonl", "/tmp/bundle", {})
    assert "--cover" in with_cover
    assert "--no-cover" in without
    assert "--cover" not in default
    assert "--no-cover" not in default


def test_build_cover_argv():
    mod = load_scrape_run()
    argv = mod.build_cover_argv(
        "/tmp/in.jsonl",
        "/tmp/bundle",
        "/tmp/covers",
        {"username": "emily", "password": "secret"},
    )
    assert argv[:3] == ["cover", "--jsonl", "/tmp/in.jsonl"]
    assert argv[argv.index("--dir") + 1] == "/tmp/bundle"
    assert argv[argv.index("--png-dir") + 1] == "/tmp/covers"
    assert "--replace" in argv
    assert "--verbose" in argv
    assert "--username" not in argv
    assert "--password" not in argv


def test_prepare_download_command_writes_jsonl(tmp_path: Path):
    mod = load_scrape_run()
    records = [
        {
            "work_id": "22",
            "url": "https://archiveofourown.org/works/22",
            "title": "Needs file",
        }
    ]
    argv, jsonl, dest = mod.prepare_download_command(
        records,
        tmp_path,
        {"username": "emily", "password": "secret"},
    )
    assert dest == tmp_path / "bundle"
    assert jsonl == dest / "results.jsonl"
    assert jsonl.is_file()
    assert '"work_id": "22"' in jsonl.read_text(encoding="utf-8")
    assert argv[0] == "download"
    assert argv[argv.index("-i") + 1] == str(jsonl)
    assert argv[argv.index("-d") + 1] == str(dest)
    assert "--no-zip" in argv
    assert "--no-simplify" in argv
    assert "--delay" not in argv
    assert argv[argv.index("--username") + 1] == "emily"


def test_write_criteria_file_includes_list_path(tmp_path):
    mod = load_scrape_run()
    path = tmp_path / "criteria.json"
    mod.write_criteria_file(
        path,
        {
            "list_path": "/collections/anonymous/works",
            "tag_id": "",
            "sort_column": "hits",
        },
    )
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["list_path"] == "/collections/anonymous/works"
    assert payload["sort_column"] == "hits"


def test_scrape_search_is_usable():
    mod = load_scrape_run()
    assert mod.scrape_search_is_usable({"url": "https://archiveofourown.org/works"})
    assert mod.scrape_search_is_usable({"tag_id": "Naruto"})
    assert mod.scrape_search_is_usable({"query": "slow burn"})
    assert not mod.scrape_search_is_usable({})


def test_describe_scrape_is_human_readable():
    mod = load_scrape_run()
    text = mod.describe_scrape(
        {
            "tag_id": "Doctor Who (2005)",
            "max_results": "1",
            "min_score": "40",
            "download_epubs": True,
            "sort_column": "kudos_count",
        }
    )
    assert "Searching AO3 and downloading: Doctor Who (2005)" in text
    assert "up to 1 work" in text
    assert "min score 40" in text
    assert "sorted by Kudos" in text
    assert "One run: search, then native EPUBs." in text
    assert "other works in the same series" not in text
    assert "python" not in text.lower()
    assert "criteria-file" not in text
    assert "/var/" not in text


def test_describe_scrape_mentions_series_expand():
    mod = load_scrape_run()
    text = mod.describe_scrape(
        {
            "tag_id": "Doctor Who (2005)",
            "include_series": True,
        }
    )
    assert "Will also import other works in the same series." in text


def test_merge_plugin_settings_fills_login():
    mod = load_scrape_run()
    merged = mod.merge_plugin_settings(
        {"tag_id": "Naruto"},
        {
            "ao3_username": "emily",
            "ao3_password": "secret",
        },
    )
    assert merged["tag_id"] == "Naruto"
    assert merged["username"] == "emily"
    assert merged["password"] == "secret"
    assert "delay" not in merged


def test_merge_plugin_settings_does_not_override_form_values():
    mod = load_scrape_run()
    merged = mod.merge_plugin_settings(
        {"username": "form-user", "password": "form-pw"},
        {
            "ao3_username": "emily",
            "ao3_password": "secret",
        },
    )
    assert merged["username"] == "form-user"
    assert merged["password"] == "form-pw"


def test_build_scrape_argv_uses_merged_settings(tmp_path: Path):
    mod = load_scrape_run()
    options = mod.merge_plugin_settings(
        {"tag_id": "Naruto", "use_form_criteria": True},
        {"ao3_username": "emily", "ao3_password": "secret"},
    )
    argv = mod.build_scrape_argv(
        options,
        output=str(tmp_path / "results.jsonl"),
        criteria_file=str(tmp_path / "criteria.json"),
    )
    assert argv[argv.index("--username") + 1] == "emily"
    assert argv[argv.index("--password") + 1] == "secret"
    assert "--delay" not in argv


def test_build_enrich_argv_includes_login():
    mod = load_scrape_run()
    argv = mod.build_enrich_argv(
        "/tmp/in.jsonl",
        "/tmp/out.jsonl",
        {"username": "emily", "password": "secret"},
    )
    assert argv[:3] == ["tags", "enrich", "--jsonl"]
    assert argv[argv.index("--username") + 1] == "emily"
    assert argv[argv.index("--password") + 1] == "secret"
    assert "--delay" not in argv
    assert "--verbose" in argv
    assert "--drop-unmarked" in argv


def test_build_enrich_argv_can_keep_noncanonical():
    mod = load_scrape_run()
    argv = mod.build_enrich_argv(
        "/tmp/in.jsonl",
        "/tmp/out.jsonl",
        {"drop_unmarked": False},
    )
    assert "--no-drop-unmarked" in argv
    assert "--drop-unmarked" not in argv


def test_build_collections_argv_skips_login():
    mod = load_scrape_run()
    argv = mod.build_collections_argv(
        "/tmp/in.jsonl",
        "/tmp/out.jsonl",
        {"username": "emily", "password": "secret"},
    )
    assert argv[:3] == ["tags", "collections", "--jsonl"]
    assert "--username" not in argv
    assert "--password" not in argv
    assert "--verbose" in argv


def test_build_collections_explain_argv_skips_login():
    mod = load_scrape_run()
    argv = mod.build_collections_explain_argv("/tmp/in.jsonl", "/tmp/explain.json")
    assert argv == [
        "tags",
        "collections",
        "--jsonl",
        "/tmp/in.jsonl",
        "-o",
        "/tmp/explain.json",
        "--explain",
    ]
    assert "--username" not in argv
    assert "--verbose" not in argv


def test_build_login_test_argv():
    mod = load_scrape_run()
    argv = mod.build_login_test_argv("emily", "secret")
    assert argv == ["login", "--username", "emily", "--password", "secret"]


def test_build_scrape_argv_include_series(tmp_path: Path):
    mod = load_scrape_run()
    argv = mod.build_scrape_argv(
        {
            "tag_id": "Doctor Who (2005)",
            "use_form_criteria": True,
            "include_series": True,
        },
        output=str(tmp_path / "results.jsonl"),
        criteria_file=str(tmp_path / "criteria.json"),
    )
    assert "--include-series" in argv


def test_merge_plugin_settings_fills_include_series():
    mod = load_scrape_run()
    merged = mod.merge_plugin_settings(
        {"tag_id": "Naruto"},
        {"include_series": True},
    )
    assert merged["include_series"] is True


def test_prepare_series_from_command(tmp_path: Path):
    mod = load_scrape_run()
    records = [
        {
            "work_id": "90876776",
            "url": "https://archiveofourown.org/works/90876776",
            "title": "Time Storm",
        }
    ]
    argv, jsonl, dest = mod.prepare_series_from_command(
        records,
        tmp_path,
        {"username": "emily", "password": "secret", "download_epubs": True},
    )
    assert argv[0] == "scrape"
    assert "--series-from" in argv
    assert argv[argv.index("-o") + 1] == str(jsonl)
    assert "--download" in argv
    assert argv[argv.index("--username") + 1] == "emily"
    seeds = dest / "seeds.jsonl"
    assert seeds.is_file()
    assert "90876776" in seeds.read_text(encoding="utf-8")


def test_prepare_fill_series_command(tmp_path: Path):
    mod = load_scrape_run()
    records = [
        {
            "work_id": "90876776",
            "url": "https://archiveofourown.org/works/90876776",
            "title": "Time Storm",
        }
    ]
    argv, jsonl = mod.prepare_fill_series_command(
        records,
        tmp_path,
        {"username": "emily", "password": "secret"},
    )
    assert argv[0] == "scrape"
    assert "--fill-series-from" in argv
    assert argv[argv.index("-o") + 1] == str(jsonl)
    assert "--download" not in argv
    assert argv[argv.index("--username") + 1] == "emily"
    seeds = tmp_path / "seeds.jsonl"
    assert seeds.is_file()
    assert "90876776" in seeds.read_text(encoding="utf-8")
