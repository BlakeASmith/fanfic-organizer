from __future__ import annotations

from io import BytesIO
from pathlib import Path
from zipfile import ZipFile

import pytest
import requests

from ao3kit.epubs import (
    DownloadOutcome,
    DownloadReport,
    JsonlWriter,
    classify_work_page,
    download_from_jsonl,
    download_record_epub,
    download_records,
    format_download_outcome_line,
    format_download_report_line,
    pack_import_zip,
    parse_epub_download_href,
    parse_jsonl_text,
    proceed_href,
    write_epub,
    write_manifest,
)

def test_parse_jsonl_text_skips_blank_and_requires_id():
    records = parse_jsonl_text(
        '\n{"work_id":"1","url":"https://archiveofourown.org/works/1"}\n\n'
    )
    assert len(records) == 1
    assert records[0]["work_id"] == "1"
    with pytest.raises(ValueError, match="missing work_id"):
        parse_jsonl_text('{"title":"no id"}')


def test_download_records_keeps_manifest_if_later_work_crashes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr("ao3kit.http.time.sleep", lambda _s: None)
    session = FakeSession()
    session.add(
        "https://archiveofourown.org/works/50448730?view_adult=true",
        FakeResponse(text=WORK_HTML, headers={"Content-Type": "text/html"}),
    )
    session.add(
        "https://archiveofourown.org/downloads/50448730/Clandestine.epub?updated_at=1",
        FakeResponse(content=minimal_epub_bytes()),
    )
    session.add(
        "https://archiveofourown.org/works/99?view_adult=true",
        FakeResponse(text=LOCKED_HTML, headers={"Content-Type": "text/html"}),
    )

    def crash_after_first(outcome, index: int, total: int) -> None:
        if index == 1:
            raise RuntimeError("simulated interrupt")

    with pytest.raises(RuntimeError, match="simulated interrupt"):
        download_records(
            [
                {
                    "work_id": "50448730",
                    "url": "https://archiveofourown.org/works/50448730",
                    "title": "Clandestine",
                },
                {"work_id": "99", "url": "https://archiveofourown.org/works/99"},
            ],
            tmp_path,
            session,
            request_delay=0,
            make_zip=False,
            on_outcome=crash_after_first,
        )

    manifest = (tmp_path / "results.jsonl").read_text(encoding="utf-8")
    assert "50448730" in manifest
    assert "99" in manifest
    assert (tmp_path / "epubs" / "50448730.epub").exists()


def test_download_records_enriches_manifest(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("ao3kit.http.time.sleep", lambda _s: None)
    session = FakeSession()
    session.add(
        "https://archiveofourown.org/works/50448730?view_adult=true",
        FakeResponse(text=WORK_HTML, headers={"Content-Type": "text/html"}),
    )
    session.add(
        "https://archiveofourown.org/downloads/50448730/Clandestine.epub?updated_at=1",
        FakeResponse(content=minimal_epub_bytes()),
    )
    report = download_records(
        [{"work_id": "50448730", "url": "https://archiveofourown.org/works/50448730", "title": "Clandestine"}],
        tmp_path,
        session,
        request_delay=0,
        make_zip=True,
    )
    assert report.downloaded == 1
    manifest = (tmp_path / "results.jsonl").read_text(encoding="utf-8")
    assert '"epub_file": "epubs/50448730.epub"' in manifest
    with ZipFile(tmp_path / "ao3-import.zip") as zf:
        assert "results.jsonl" in zf.namelist()
        assert "epubs/50448730.epub" in zf.namelist()


WORK_HTML = """
<html><body>
<ul class="work navigation actions">
  <li class="download">
    <a href="#">Download</a>
    <ul>
      <li><a href="/downloads/50448730/Clandestine.azw3?updated_at=1">AZW3</a></li>
      <li><a href="/downloads/50448730/Clandestine.epub?updated_at=1">EPUB</a></li>
      <li><a href="/downloads/50448730/Clandestine.pdf?updated_at=1">PDF</a></li>
    </ul>
  </li>
</ul>
</body></html>
"""

ADULT_HTML = """
<html><body>
<div class="works-show region">
  <p class="caution">This work could have adult content.</p>
  <ul class="actions">
    <li><a href="/works/99?view_adult=true">Proceed</a></li>
  </ul>
</div>
</body></html>
"""

ADULT_HTML_YES_CONTINUE = """
<html><body>
<p class="caution notice">
  This work could have adult content. If you continue, you have agreed that you are willing to see such content.
</p>
<ul class="actions">
  <li><a href="/works/99?view_adult=true">Yes, Continue</a></li>
</ul>
</body></html>
"""

LOCKED_HTML = """
<html><body>
<div id="main" class="sessions-new">
  This work is only available to registered users of the Archive.
</div>
</body></html>
"""


def minimal_epub_bytes() -> bytes:
    buf = BytesIO()
    with ZipFile(buf, "w") as zf:
        zf.writestr("mimetype", "application/epub+zip")
    return buf.getvalue()


class FakeResponse:
    def __init__(self, *, text: str = "", content: bytes = b"", status_code: int = 200, headers=None):
        self.text = text
        self.content = content
        self.status_code = status_code
        self.headers = headers or {}

    def close(self) -> None:
        return

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            error = requests.HTTPError(f"{self.status_code}")
            error.response = self
            raise error

    def iter_content(self, chunk_size: int = 65536):
        yield self.content


class FakeSession:
    def __init__(self) -> None:
        self.routes: dict[str, FakeResponse | list[FakeResponse]] = {}
        self.calls: list[str] = []

    def add(self, url: str, response: FakeResponse) -> None:
        existing = self.routes.get(url)
        if existing is None:
            self.routes[url] = response
        elif isinstance(existing, list):
            existing.append(response)
        else:
            self.routes[url] = [existing, response]

    def request(self, method: str, url: str, data=None, timeout=None, stream: bool = False) -> FakeResponse:
        self.calls.append(url)
        response = self.routes.get(url)
        if response is None:
            raise AssertionError(f"unexpected {method} {url}")
        if isinstance(response, list):
            return response.pop(0)
        return response

    def get(self, url: str, timeout=None, stream: bool = False) -> FakeResponse:
        return self.request("GET", url, timeout=timeout, stream=stream)


def test_parse_epub_download_href_picks_epub_not_pdf():
    href = parse_epub_download_href(WORK_HTML)
    assert href == "/downloads/50448730/Clandestine.epub?updated_at=1"


def test_proceed_href_from_adult_gate():
    assert proceed_href(ADULT_HTML) == "/works/99?view_adult=true"
    assert proceed_href(ADULT_HTML_YES_CONTINUE) == "/works/99?view_adult=true"
    assert classify_work_page(ADULT_HTML) == "adult"
    assert classify_work_page(ADULT_HTML_YES_CONTINUE) == "adult"


def test_classify_locked_and_deleted():
    assert classify_work_page(LOCKED_HTML) == "locked"
    assert classify_work_page('<div id="main" class="error-404"></div>') == "deleted"
    assert classify_work_page("This work is part of an ongoing challenge and will be revealed soon!") == "hidden"


def test_download_record_saves_native_bytes(tmp_path: Path):
    epub = minimal_epub_bytes()
    session = FakeSession()
    session.add(
        "https://archiveofourown.org/works/50448730?view_adult=true",
        FakeResponse(text=WORK_HTML, headers={"Content-Type": "text/html"}),
    )
    session.add(
        "https://archiveofourown.org/downloads/50448730/Clandestine.epub?updated_at=1",
        FakeResponse(content=epub),
    )
    record = {
        "work_id": "50448730",
        "url": "https://archiveofourown.org/works/50448730",
        "title": "Clandestine",
    }

    outcome = download_record_epub(record, tmp_path, session)

    assert outcome.status == "downloaded"
    assert outcome.epub_file == "epubs/50448730.epub"
    saved = tmp_path / "epubs" / "50448730.epub"
    assert saved.read_bytes() == epub


def test_download_record_follows_adult_proceed(tmp_path: Path):
    """If view_adult is ignored and caution HTML is returned, follow Proceed."""
    epub = minimal_epub_bytes()
    session = FakeSession()
    session.add(
        "https://archiveofourown.org/works/99?view_adult=true",
        FakeResponse(text=ADULT_HTML, headers={"Content-Type": "text/html"}),
    )
    session.add(
        "https://archiveofourown.org/works/99?view_adult=true",
        FakeResponse(
            text=WORK_HTML.replace("50448730", "99"),
            headers={"Content-Type": "text/html"},
        ),
    )
    session.add(
        "https://archiveofourown.org/downloads/99/Clandestine.epub?updated_at=1",
        FakeResponse(content=epub),
    )
    outcome = download_record_epub(
        {"work_id": "99", "url": "https://archiveofourown.org/works/99", "title": "x"},
        tmp_path,
        session,
    )
    assert outcome.status == "downloaded"
    assert (tmp_path / "epubs" / "99.epub").exists()


def test_download_record_locked_sets_error(tmp_path: Path):
    session = FakeSession()
    session.add(
        "https://archiveofourown.org/works/1?view_adult=true",
        FakeResponse(text=LOCKED_HTML, headers={"Content-Type": "text/html"}),
    )
    outcome = download_record_epub(
        {"work_id": "1", "url": "https://archiveofourown.org/works/1"},
        tmp_path,
        session,
    )
    assert outcome.status == "failed"
    assert outcome.error == "locked"
    assert outcome.record["epub_error"] == "locked"
    assert "epub_file" not in outcome.record


def test_download_record_rejects_html_payload(tmp_path: Path):
    session = FakeSession()
    session.add(
        "https://archiveofourown.org/works/2?view_adult=true",
        FakeResponse(
            text=WORK_HTML.replace("50448730", "2"),
            headers={"Content-Type": "text/html"},
        ),
    )
    session.add(
        "https://archiveofourown.org/downloads/2/Clandestine.epub?updated_at=1",
        FakeResponse(content=b"<html>rate limited</html>"),
    )
    outcome = download_record_epub(
        {"work_id": "2", "url": "https://archiveofourown.org/works/2"},
        tmp_path,
        session,
    )
    assert outcome.status == "failed"
    assert outcome.error == "not_epub"


def test_skip_existing_does_not_hit_network(tmp_path: Path):
    dest = tmp_path / "epubs" / "50448730.epub"
    write_epub(dest, minimal_epub_bytes())
    session = FakeSession()
    outcome = download_record_epub(
        {"work_id": "50448730", "url": "https://archiveofourown.org/works/50448730"},
        tmp_path,
        session,
        skip_existing=True,
    )
    assert outcome.status == "skipped"
    assert session.calls == []


def test_pack_and_download_from_jsonl(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("ao3kit.http.time.sleep", lambda _s: None)
    jsonl = tmp_path / "in.jsonl"
    jsonl.write_text(
        '{"work_id":"50448730","url":"https://archiveofourown.org/works/50448730","title":"Clandestine"}\n',
        encoding="utf-8",
    )
    session = FakeSession()
    session.add(
        "https://archiveofourown.org/works/50448730?view_adult=true",
        FakeResponse(text=WORK_HTML, headers={"Content-Type": "text/html"}),
    )
    session.add(
        "https://archiveofourown.org/downloads/50448730/Clandestine.epub?updated_at=1",
        FakeResponse(content=minimal_epub_bytes()),
    )
    dest = tmp_path / "out"
    report = download_from_jsonl(jsonl, dest, session, request_delay=0, make_zip=True)

    assert report.downloaded == 1
    zip_path = dest / "ao3-import.zip"
    assert zip_path.exists()
    with ZipFile(zip_path) as zf:
        names = set(zf.namelist())
    assert "results.jsonl" in names
    assert "epubs/50448730.epub" in names


def test_pack_import_zip_requires_manifest(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        pack_import_zip(tmp_path)


def test_format_download_outcome_line_uses_title_not_path():
    downloaded = DownloadOutcome(
        record={
            "work_id": "65062336",
            "title": "The Regulus Star",
            "epub_file": "epubs/65062336.epub",
        },
        status="downloaded",
        epub_file="epubs/65062336.epub",
    )
    line = format_download_outcome_line(downloaded, 1, 3)
    assert line == "[1/3] Downloaded The Regulus Star"
    assert "epubs/" not in line
    assert "65062336" not in line

    failed = DownloadOutcome(
        record={"work_id": "99", "title": "Locked Work"},
        status="failed",
        error="locked",
    )
    assert format_download_outcome_line(failed, 2, 3) == (
        "[2/3] Failed Locked Work: restricted — log in to AO3 and retry"
    )

    skipped = DownloadOutcome(
        record={"work_id": "1", "title": "Already Here"},
        status="skipped",
        epub_file="epubs/1.epub",
    )
    assert format_download_outcome_line(skipped, 3, 3).endswith("(already on disk)")


def test_format_download_report_line_omits_zero_counts(tmp_path: Path):
    report = DownloadReport(
        outcomes=[
            DownloadOutcome(record={"work_id": "1"}, status="downloaded"),
        ]
    )
    assert format_download_report_line(report) == "Downloaded 1 EPUB"
    with_dest = format_download_report_line(report, tmp_path)
    assert with_dest.startswith("Downloaded 1 EPUB → ")
    assert "skipped" not in with_dest
    assert "failed" not in with_dest

    mixed = DownloadReport(
        outcomes=[
            DownloadOutcome(record={"work_id": "1"}, status="downloaded"),
            DownloadOutcome(record={"work_id": "2"}, status="downloaded"),
            DownloadOutcome(record={"work_id": "3"}, status="failed", error="http"),
        ]
    )
    assert format_download_report_line(mixed) == "Downloaded 2 EPUBs, failed 1"


def test_write_manifest_replaces_atomically(tmp_path: Path):
    path = tmp_path / "results.jsonl"
    write_manifest([{"work_id": "1", "title": "One"}], path)
    write_manifest(
        [
            {"work_id": "1", "title": "One", "epub_file": "epubs/1.epub"},
            {"work_id": "2", "title": "Two"},
        ],
        path,
    )
    lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line]
    assert len(lines) == 2
    assert "epubs/1.epub" in lines[0]
    assert not path.with_suffix(".jsonl.tmp").exists()


def test_jsonl_writer_upserts_by_work_id(tmp_path: Path):
    path = tmp_path / "results.jsonl"
    writer = JsonlWriter(path)
    assert path.is_file()
    assert path.read_text(encoding="utf-8") == ""
    writer.upsert({"work_id": "1", "title": "One"})
    writer.upsert({"work_id": "2", "title": "Two"})
    writer.upsert({"work_id": "1", "title": "One", "epub_file": "epubs/1.epub"})
    lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line]
    assert len(lines) == 2
    assert "epubs/1.epub" in lines[0]
    assert '"work_id": "2"' in lines[1]


def test_download_records_keeps_pending_works_in_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    seen: list[str] = []

    def fake_download(record, dest, session, **kwargs):
        seen.append((dest / "results.jsonl").read_text(encoding="utf-8"))
        updated = dict(record)
        updated["epub_file"] = f"epubs/{record['work_id']}.epub"
        return DownloadOutcome(
            record=updated, status="downloaded", epub_file=updated["epub_file"]
        )

    monkeypatch.setattr("ao3kit.epubs.download_record_epub", fake_download)
    monkeypatch.setattr("ao3kit.epubs.apply_request_delay", lambda *_a, **_k: None)
    download_records(
        [
            {
                "work_id": "1",
                "url": "https://archiveofourown.org/works/1",
                "title": "One",
            },
            {
                "work_id": "2",
                "url": "https://archiveofourown.org/works/2",
                "title": "Two",
            },
        ],
        tmp_path,
        object(),
        request_delay=0,
        make_zip=False,
        simplify_tags=False,
    )
    assert "One" in seen[0] and "Two" in seen[0]
    assert "epub_file" not in seen[0]
    assert "epubs/1.epub" in seen[1]
    assert "Two" in seen[1]
    final = (tmp_path / "results.jsonl").read_text(encoding="utf-8")
    assert "epubs/1.epub" in final
    assert "epubs/2.epub" in final


def test_download_cli_omits_remap_summary_without_simplify(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    jsonl = tmp_path / "in.jsonl"
    jsonl.write_text(
        '{"work_id":"1","url":"https://archiveofourown.org/works/1","title":"T"}\n',
        encoding="utf-8",
    )
    dest = tmp_path / "out"

    def fake_download(*_args, **_kwargs):
        return DownloadReport(
            outcomes=[
                DownloadOutcome(
                    record={"work_id": "1", "title": "T"},
                    status="downloaded",
                    epub_file="epubs/1.epub",
                )
            ]
        )

    monkeypatch.setattr("ao3kit.epubs.create_session", lambda *_a, **_k: object())
    monkeypatch.setattr("ao3kit.epubs.download_from_jsonl", fake_download)
    from ao3kit.epubs import main as download_main

    rc = download_main(
        ["-i", str(jsonl), "-d", str(dest), "--verbose", "--no-simplify", "--no-zip"]
    )
    assert rc == 0
    err = capsys.readouterr().err
    assert "Downloaded 1 EPUB" in err
    assert "Tag remappings" not in err
