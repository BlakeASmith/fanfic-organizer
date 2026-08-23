"""ao3kit_argv: regular Python vs Calibre launcher."""

from __future__ import annotations

from ao3kit.proc import ao3kit_argv, looks_like_calibre_binary


def test_looks_like_calibre_binary():
    assert looks_like_calibre_binary("/Applications/calibre.app/Contents/MacOS/calibre")
    assert looks_like_calibre_binary("/Applications/calibre.app/Contents/MacOS/calibre-debug")
    assert looks_like_calibre_binary(r"C:\Calibre2\calibre-debug.exe")
    assert not looks_like_calibre_binary("/usr/bin/python3")


def test_ao3kit_argv_module_form(monkeypatch):
    monkeypatch.delenv("AO3KIT_LAUNCHER", raising=False)
    argv = ao3kit_argv(["scrape", "-o", "out.jsonl"], python="/usr/bin/python3")
    assert argv[:4] == ["/usr/bin/python3", "-u", "-m", "ao3kit"]
    assert argv[-2:] == ["-o", "out.jsonl"]


def test_ao3kit_argv_launcher_with_cpython(monkeypatch):
    monkeypatch.delenv("AO3KIT_LAUNCHER", raising=False)
    argv = ao3kit_argv(
        ["job", "run", "--dir", "/tmp/j"],
        python="/usr/bin/python3",
        launcher="/tmp/run_ao3kit.py",
    )
    assert argv == [
        "/usr/bin/python3",
        "-u",
        "/tmp/run_ao3kit.py",
        "job",
        "run",
        "--dir",
        "/tmp/j",
    ]


def test_ao3kit_argv_launcher_with_calibre_debug(monkeypatch):
    monkeypatch.setenv("AO3KIT_LAUNCHER", "/runtime/run_ao3kit.py")
    argv = ao3kit_argv(
        ["scrape", "--verbose"],
        python="/Applications/calibre.app/Contents/MacOS/calibre-debug",
    )
    assert argv == [
        "/Applications/calibre.app/Contents/MacOS/calibre-debug",
        "-e",
        "/runtime/run_ao3kit.py",
        "--",
        "scrape",
        "--verbose",
    ]
