# -*- coding: utf-8 -*-
"""Cover summary helpers for the Calibre plugin (no Calibre, no ao3kit).

Keep in sync with ``ao3kit.covers.summary_text_from_comments`` and
``ao3kit.covers.resolve_record_summary``. Used when Calibre's Python cannot
load the bundled ao3kit package.
"""

from __future__ import annotations

import html
import json
import re
from typing import Any

_SUMMARY_JSON_KEYS = frozenset(
    {"work_id", "tags", "fandoms", "cleaned"}
)


def _normalize_cover_text(text: str) -> str:
    cleaned = html.unescape(str(text or ""))
    return re.sub(r"\s+", " ", cleaned).strip()


def summary_text_from_comments(comments: Any) -> str:
    """Return AO3-style summary text from Calibre Comments (plain or HTML).

    Skips legacy JSON metadata blobs some libraries store in Comments.
    """
    text = str(comments or "").strip()
    if not text:
        return ""
    if text.startswith("{") or text.startswith("["):
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, dict) and any(
            parsed.get(key) for key in _SUMMARY_JSON_KEYS
        ):
            return ""
    plain = re.sub(r"<[^>]+>", " ", text)
    plain = re.sub(r"\s+([,.!?;:])", r"\1", plain)
    return _normalize_cover_text(plain)


def resolve_record_summary(
    record: dict[str, Any] | None,
    *,
    summary_column: Any = None,
    comments: Any = None,
) -> str:
    """Pick summary text from a work record, optional column, or Comments."""
    record = record or {}
    direct = _normalize_cover_text(str(record.get("summary") or ""))
    if direct:
        return direct
    for candidate in (summary_column, comments, record.get("comments")):
        text = summary_text_from_comments(candidate)
        if text:
            return text
    return ""
