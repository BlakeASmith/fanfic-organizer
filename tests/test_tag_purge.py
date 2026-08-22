"""Tests for Calibre Tag Purge helpers."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

PLUGIN_TAG_PURGE = Path(__file__).resolve().parents[1] / "calibre-plugin" / "tag_purge.py"


def load_tag_purge():
    spec = importlib.util.spec_from_file_location("ao3_tag_purge", PLUGIN_TAG_PURGE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def snap(mod, book_id, tags, **kwargs):
    return mod.BookTagSnapshot(
        book_id=book_id,
        tags=tuple(tags),
        fandoms=tuple(kwargs.get("fandoms", ())),
        collections=tuple(kwargs.get("collections", ())),
        authors=tuple(kwargs.get("authors", ())),
        relationships=tuple(kwargs.get("relationships", ())),
        title=kwargs.get("title", ""),
    )


def test_parse_name_list_splits_and_strips():
    mod = load_tag_purge()
    assert mod.parse_name_list("Fluff, Angst, ") == ("Fluff", "Angst")
    assert mod.parse_name_list("") == ()
    assert mod.parse_name_list(None) == ()


def test_rare_tags_max_one_is_alphabetical():
    mod = load_tag_purge()
    books = [
        snap(mod, 1, ["Fluff", "Zebra"]),
        snap(mod, 2, ["Fluff", "angst"]),
        snap(mod, 3, ["Fluff"]),
    ]
    planned = mod.plan_tag_purge(books, max_works=1)
    assert planned == [("angst", 1), ("Zebra", 1)]


def test_rare_tags_max_two_includes_one_and_two():
    mod = load_tag_purge()
    books = [
        snap(mod, 1, ["Shared", "Only"]),
        snap(mod, 2, ["Shared"]),
        snap(mod, 3, ["Common"]),
        snap(mod, 4, ["Common"]),
        snap(mod, 5, ["Common"]),
    ]
    names = [name for name, _count in mod.plan_tag_purge(books, max_works=2)]
    assert names == ["Only", "Shared"]
    assert "Common" not in names


def test_duplicate_tag_on_one_book_counts_once():
    mod = load_tag_purge()
    books = [snap(mod, 1, ["Fluff", "Fluff"]), snap(mod, 2, ["Other"])]
    counts = mod.count_tags(books)
    assert counts["Fluff"] == 1


def test_fandom_filter_seeds_names_but_counts_stay_global():
    mod = load_tag_purge()
    books = [
        snap(mod, 1, ["Rare HP"], fandoms=["Harry Potter - J. K. Rowling"]),
        snap(mod, 2, ["Rare DW"], fandoms=["Doctor Who (2005)"]),
        snap(mod, 3, ["Both"], fandoms=["Harry Potter - J. K. Rowling"]),
        snap(mod, 4, ["Both"], fandoms=["Doctor Who (2005)"]),
    ]
    filters = mod.TagPurgeFilters(fandoms=("Harry Potter",))
    planned = mod.plan_tag_purge(books, max_works=1, filters=filters)
    assert planned == [("Rare HP", 1)]
    planned2 = mod.plan_tag_purge(books, max_works=2, filters=filters)
    assert planned2 == [("Both", 2), ("Rare HP", 1)]


def test_tag_filter_seeds_from_matching_books_with_global_counts():
    mod = load_tag_purge()
    books = [
        snap(mod, 1, ["Fluff", "Rare"]),
        snap(mod, 2, ["Angst", "Rare"]),
        snap(mod, 3, ["Fluff"]),
    ]
    filters = mod.TagPurgeFilters(tags=("fluff",))
    planned = mod.plan_tag_purge(books, max_works=1, filters=filters)
    assert planned == []
    planned2 = mod.plan_tag_purge(books, max_works=2, filters=filters)
    assert planned2 == [("Fluff", 2), ("Rare", 2)]


def test_filters_use_fuzzy_match():
    mod = load_tag_purge()
    books = [
        snap(
            mod,
            1,
            ["Slow Burn"],
            fandoms=["Harry Potter - J. K. Rowling"],
            relationships=["Amy Pond/Rory Williams"],
        ),
        snap(
            mod,
            2,
            ["Crack"],
            fandoms=["Naruto"],
            relationships=["Tenth Doctor/Rose Tyler"],
        ),
    ]
    assert mod.fuzzy_name_match("Harry Potter - J. K. Rowling", "hary potter")
    assert mod.fuzzy_name_match("Amy Pond/Rory Williams", "amy rory")
    assert mod.fuzzy_name_match("Slow Burn", "slow brn")
    assert not mod.fuzzy_name_match("Naruto", "harry potter")

    by_fandom = mod.matching_books(
        books, mod.TagPurgeFilters(fandoms=("hary potter",))
    )
    assert [book.book_id for book in by_fandom] == [1]
    by_ship = mod.matching_books(
        books, mod.TagPurgeFilters(relationships=("amy rory",))
    )
    assert [book.book_id for book in by_ship] == [1]
    by_tag = mod.matching_books(
        books, mod.TagPurgeFilters(tags=("slow brn",))
    )
    assert [book.book_id for book in by_tag] == [1]


def test_comma_separated_fandom_is_or():
    mod = load_tag_purge()
    books = [
        snap(mod, 1, ["A"], fandoms=["Doctor Who (2005)"]),
        snap(mod, 2, ["B"], fandoms=["Torchwood"]),
        snap(mod, 3, ["C"], fandoms=["Naruto"]),
    ]
    filters = mod.TagPurgeFilters(fandoms=("Doctor Who", "Torchwood"))
    names = [name for name, _ in mod.plan_tag_purge(books, max_works=1, filters=filters)]
    assert names == ["A", "B"]


def test_purge_updates_entire_library():
    mod = load_tag_purge()
    books = [
        snap(
            mod,
            1,
            ["Fluff", "Rare"],
            fandoms=["Harry Potter"],
            collections=["Keep me"],
        ),
        snap(mod, 2, ["Fluff"], fandoms=["Harry Potter"]),
        snap(mod, 3, ["Rare"], fandoms=["Doctor Who (2005)"]),
    ]
    updates = mod.purge_updates(books, ["Rare"])
    assert updates == [(1, ["Fluff"]), (3, [])]


def test_purge_leaves_unrelated_tags_in_place():
    mod = load_tag_purge()
    books = [snap(mod, 1, ["Keep", "Drop", "Also keep"])]
    assert mod.purge_updates(books, ["Drop"]) == [(1, ["Keep", "Also keep"])]


def test_empty_purge_names_is_noop():
    mod = load_tag_purge()
    books = [snap(mod, 1, ["Fluff"])]
    assert mod.purge_updates(books, []) == []


def test_max_works_below_one_is_empty():
    mod = load_tag_purge()
    books = [snap(mod, 1, ["Only"])]
    assert mod.plan_tag_purge(books, max_works=0) == []


class _FakeAPI:
    def __init__(self, books: dict[int, dict]):
        self.books = books

    def all_book_ids(self):
        return set(self.books)

    def search(self, query: str):
        query = (query or "").casefold()
        if not query:
            return set(self.books)
        if "fluff" in query:
            return {
                book_id
                for book_id, book in self.books.items()
                if "Fluff" in book.get("tags", ())
            }
        return set()

    def all_field_for(self, lookup: str, ids):
        key = {
            "tags": "tags",
            "#fandom": "fandoms",
            "#collections": "collections",
            "authors": "authors",
            "#relationships": "relationships",
            "title": "title",
        }.get(lookup)
        if key is None:
            raise KeyError(lookup)
        out = {}
        for book_id in ids:
            book = self.books[book_id]
            if key == "title":
                out[book_id] = book.get("title") or f"Book {book_id}"
            else:
                out[book_id] = tuple(book.get(key) or ())
        return out


class _FakeDB:
    def __init__(self, books: dict[int, dict], *, with_search=True):
        self.new_api = _FakeAPI(books)
        self._ids = list(books)
        self._books = books
        if with_search:
            self.search_getting_ids = self._search_getting_ids

    def _search_getting_ids(self, query, _restriction, use_virtual_library=True):
        del use_virtual_library
        if not (query or "").strip():
            return list(self._ids)
        return list(self.new_api.search(query))

    def all_ids(self):
        return list(self._ids)


def test_relationships_and_fandom_filters_are_anded():
    mod = load_tag_purge()
    books = [
        snap(
            mod,
            1,
            ["Keep", "Rare"],
            fandoms=["Doctor Who (2005)"],
            relationships=["Amy Pond/Rory Williams"],
        ),
        snap(
            mod,
            2,
            ["Rare"],
            fandoms=["Doctor Who (2005)"],
            relationships=["Tenth Doctor/Rose Tyler"],
        ),
        snap(
            mod,
            3,
            ["Rare"],
            fandoms=["Torchwood"],
            relationships=["Amy Pond/Rory Williams"],
        ),
    ]
    filters = mod.TagPurgeFilters(
        fandoms=("Doctor Who",),
        relationships=("Amy Pond/Rory",),
    )
    matched = mod.matching_books(books, filters)
    assert [book.book_id for book in matched] == [1]
    planned = mod.plan_tag_purge(books, max_works=1, filters=filters)
    assert planned == [("Keep", 1)]


def test_load_snapshots_via_old_api():
    mod = load_tag_purge()

    class Meta:
        def __init__(self):
            self.tags = ["Fluff", "Rare"]
            self.authors = ["A. Author"]
            self.title = "Work"

    class OldDB:
        def all_ids(self):
            return [7]

        def get_metadata(self, book_id, index_is_id=True):
            del book_id, index_is_id
            return Meta()

        def get_custom(self, book_id, label=None, index_is_id=True):
            del book_id, index_is_id
            if label == "fandom":
                return ["Doctor Who (2005)"]
            return []

    snapshots = mod.load_snapshots(OldDB())
    assert len(snapshots) == 1
    assert snapshots[0].book_id == 7
    assert snapshots[0].tags == ("Fluff", "Rare")
    assert snapshots[0].fandoms == ("Doctor Who (2005)",)
    assert snapshots[0].authors == ("A. Author",)
    mod = load_tag_purge()
    db = _FakeDB(
        {
            1: {
                "tags": ["Fluff", "Rare"],
                "fandoms": ["Doctor Who (2005)"],
                "authors": ["A. Author"],
            },
            2: {"tags": ["Fluff"]},
        }
    )
    snapshots = mod.load_snapshots(db)
    by_id = {item.book_id: item for item in snapshots}
    assert by_id[1].tags == ("Fluff", "Rare")
    assert by_id[1].fandoms == ("Doctor Who (2005)",)
    assert by_id[1].authors == ("A. Author",)
    planned = mod.plan_tag_purge(snapshots, max_works=1)
    assert planned == [("Rare", 1)]


def test_resolve_scope_ids_uses_calibre_search():
    mod = load_tag_purge()
    db = _FakeDB(
        {
            1: {"tags": ["Fluff", "Rare"]},
            2: {"tags": ["Angst"]},
        }
    )
    assert sorted(mod.resolve_scope_ids(db, "")) == [1, 2]
    assert mod.resolve_scope_ids(db, 'tags:"Fluff"') == [1]


def test_scope_book_ids_limits_to_selection():
    mod = load_tag_purge()
    db = _FakeDB(
        {
            1: {"tags": ["Fluff", "Only on 1"]},
            2: {"tags": ["Fluff"]},
            3: {"tags": ["Angst", "Only on 3"]},
        }
    )
    assert mod.scope_book_ids(db, selected_ids=[1, 3], selected_only=True) == [1, 3]
    assert sorted(mod.scope_book_ids(db, selected_ids=[1, 3], selected_only=False)) == [
        1,
        2,
        3,
    ]
    assert mod.scope_book_ids(
        db, 'tags:"Fluff"', selected_ids=[1, 3], selected_only=True
    ) == [1]

    snapshots = mod.load_snapshots(db, [1, 3])
    names = [name for name, _ in mod.plan_tag_purge(snapshots, max_works=1)]
    assert names == ["Angst", "Fluff", "Only on 1", "Only on 3"]


def test_source_subset_uses_global_counts_and_hides_common_tags():
    mod = load_tag_purge()
    library = [
        snap(mod, 1, ["Rare", "Common"]),
        snap(mod, 2, ["Common"]),
        snap(mod, 3, ["Common"]),
        snap(mod, 4, ["OtherRare"]),
    ]
    planned = mod.plan_tag_purge(library, max_works=1, source=[library[0]])
    assert planned == [("Rare", 1)]
    planned2 = mod.plan_tag_purge(library, max_works=3, source=[library[0]])
    assert planned2 == [("Common", 3), ("Rare", 1)]


def test_filter_tags_by_name_is_fuzzy():
    mod = load_tag_purge()
    planned = [("Slow Burn", 1), ("Fluff", 2), ("Angst", 1)]
    assert [name for name, _ in mod.filter_tags_by_name(planned, "slow brn")] == [
        "Slow Burn"
    ]
    assert [name for name, _ in mod.filter_tags_by_name(planned, "fluf, angst")] == [
        "Fluff",
        "Angst",
    ]
    assert mod.filter_tags_by_name(planned, "") == planned


def test_graph_scope_uses_multi_selection():
    mod = load_tag_purge()
    ids, kind = mod.graph_scope_ids(selected=[4, 9, 1], library_ids=[1, 2, 3, 4, 9])
    assert kind == "selected"
    assert ids == [4, 9, 1]


def test_graph_scope_uses_library_when_one_or_none_selected():
    mod = load_tag_purge()
    library = [1, 2, 3]
    ids, kind = mod.graph_scope_ids(selected=[2], library_ids=library)
    assert kind == "library"
    assert ids == library
    ids, kind = mod.graph_scope_ids(selected=[], library_ids=library)
    assert kind == "library"
    assert ids == library


def test_initial_scope_prefers_multi_selection():
    mod = load_tag_purge()
    ids, kind = mod.initial_scope_ids(
        selected=[1, 2],
        shown=[1, 2, 3, 4],
        library_count=100,
    )
    assert kind == "selected"
    assert ids == [1, 2]


def test_initial_scope_uses_shown_when_view_is_filtered():
    mod = load_tag_purge()
    shown = list(range(1, 21))
    ids, kind = mod.initial_scope_ids(
        selected=[7],
        shown=shown,
        library_count=500,
    )
    assert kind == "shown"
    assert ids == shown


def test_initial_scope_uses_single_selection_when_unfiltered():
    mod = load_tag_purge()
    shown = list(range(1, 501))
    ids, kind = mod.initial_scope_ids(
        selected=[7],
        shown=shown,
        library_count=500,
    )
    assert kind == "selected"
    assert ids == [7]


def test_initial_scope_uses_shown_when_nothing_highlighted():
    mod = load_tag_purge()
    ids, kind = mod.initial_scope_ids(
        selected=[],
        shown=[1, 2, 3],
        library_count=50,
    )
    assert kind == "shown"
    assert ids == [1, 2, 3]


def test_selected_ids_from_view_prefers_get_selected_ids():
    mod = load_tag_purge()

    class View:
        def get_selected_ids(self):
            return [10, 11]

    assert mod.selected_ids_from_view(View()) == [10, 11]


def test_shown_ids_from_gui_uses_model_rows():
    mod = load_tag_purge()

    class Model:
        def count(self):
            return 3

        def id(self, row):
            return 100 + row

    class View:
        def model(self):
            return Model()

    class GUI:
        library_view = View()

    assert mod.shown_ids_from_gui(GUI()) == [100, 101, 102]
