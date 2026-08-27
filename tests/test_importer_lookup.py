"""In-memory AO3 identifier lookup and library UI refresh helpers."""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[1] / "calibre-plugin"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_importer():
    cleaned = _load("ao3_cleaned_for_importer", PLUGIN / "cleaned.py")
    columns = _load("ao3_columns_for_importer", PLUGIN / "columns.py")
    jsonl = _load("ao3_jsonl_for_importer", PLUGIN / "jsonl_loader.py")

    metadata = types.ModuleType("calibre.ebooks.metadata")

    class MetaInformation:
        def __init__(self, title, authors):
            self.title = title
            self.authors = authors
            self.tags = []
            self._ids = {}

        def set_identifiers(self, ids):
            self._ids = dict(ids)

        def get_identifiers(self):
            return dict(self._ids)

    metadata.MetaInformation = MetaInformation
    sys.modules.setdefault("calibre", types.ModuleType("calibre"))
    sys.modules.setdefault("calibre.ebooks", types.ModuleType("calibre.ebooks"))
    sys.modules["calibre.ebooks.metadata"] = metadata
    sys.modules.setdefault("calibre_plugins", types.ModuleType("calibre_plugins"))
    fanfic = types.ModuleType("calibre_plugins.fanfic_organizer")
    sys.modules["calibre_plugins.fanfic_organizer"] = fanfic
    sys.modules["calibre_plugins.fanfic_organizer.cleaned"] = cleaned
    sys.modules["calibre_plugins.fanfic_organizer.columns"] = columns
    sys.modules["calibre_plugins.fanfic_organizer.jsonl_loader"] = jsonl
    return _load("ao3_importer", PLUGIN / "importer.py")


class FakeApi:
    def __init__(self, mapping):
        self._mapping = mapping
        self.field_for_calls = 0

    def all_book_ids(self):
        return list(self._mapping)

    def all_field_for(self, field, book_ids):
        assert field == "identifiers"
        self.field_for_calls += 1
        return {book_id: self._mapping[book_id] for book_id in book_ids}


class FakeDb:
    def __init__(self, mapping, *, use_api=True):
        self._mapping = mapping
        self.search_calls = []
        if use_api:
            self.new_api = FakeApi(mapping)

    def all_ids(self):
        return list(self._mapping)

    def get_identifiers(self, book_id, index_is_id=True):
        return dict(self._mapping[book_id])

    def search_getting_ids(self, query, _restriction, use_virtual_library=False):
        self.search_calls.append(query)
        raise AssertionError(f"search_getting_ids should not run: {query}")


class FakeIndex:
    def __init__(self, valid=True):
        self._valid = valid

    def isValid(self):
        return self._valid

    def row(self):
        return 3


class FakeModel:
    def __init__(self):
        self.books_added_n = None
        self.refresh_ids_args = None
        self.refresh_called = False

    def books_added(self, n):
        self.books_added_n = n

    def refresh_ids(self, ids, current_row=-1):
        self.refresh_ids_args = (list(ids), current_row)

    def refresh(self):
        self.refresh_called = True


class FakeTags:
    def __init__(self):
        self.recount_calls = 0

    def recount(self):
        self.recount_calls += 1


class FakeView:
    def __init__(self, model):
        self._model = model

    def model(self):
        return self._model

    def currentIndex(self):
        return FakeIndex()


class FakeGui:
    def __init__(self, model, tags=None):
        self.library_view = FakeView(model)
        self.tags_view = tags


def test_find_existing_uses_new_api_not_search():
    mod = load_importer()
    db = FakeDb(
        {
            7: {"ao3": "55", "url": "https://archiveofourown.org/works/55"},
            8: {"ao3": "66"},
        }
    )
    assert mod.find_existing_book(db, {"work_id": "66"}) == 8
    assert db.search_calls == []
    assert db.new_api.field_for_calls == 1


def test_find_existing_legacy_identifiers_without_api():
    mod = load_importer()
    db = FakeDb({4: {"url": "https://www.archiveofourown.org/works/44"}}, use_api=False)
    assert mod.find_existing_book(db, {"work_id": "44"}) == 4
    assert db.search_calls == []


def test_find_existing_catalog_avoids_rescan_and_records_adds():
    mod = load_importer()
    db = FakeDb({1: {"ao3": "1"}})
    catalog = mod.iter_identifier_maps(db)
    assert mod.find_existing_book(db, {"work_id": "1"}, catalog=catalog) == 1
    db.new_api.field_for_calls = 0
    catalog.append((9, {"ao3": "99"}))
    assert mod.find_existing_book(db, {"work_id": "99"}, catalog=catalog) == 9
    assert db.new_api.field_for_calls == 0


def test_refresh_inserts_new_rows_without_second_recount():
    mod = load_importer()
    model = FakeModel()
    tags = FakeTags()
    mod.refresh_library_ui(FakeGui(model, tags), [11, 12], added_count=2)
    assert model.books_added_n == 2
    assert model.refresh_ids_args == ([11, 12], 3)
    assert tags.recount_calls == 0
    assert not model.refresh_called


def test_refresh_updates_recount_existing_books():
    mod = load_importer()
    model = FakeModel()
    tags = FakeTags()
    mod.refresh_library_ui(FakeGui(model, tags), [5])
    assert model.books_added_n is None
    assert model.refresh_ids_args == ([5], 3)
    assert tags.recount_calls == 1
