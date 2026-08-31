# -*- coding: utf-8 -*-
"""Generic URL / saved-HTML source package for the Calibre plugin."""

from __future__ import annotations

try:
    from calibre_plugins.fanfic_organizer.sources.web.source import WebSource
except ImportError:
    from sources.web.source import WebSource

__all__ = ['WebSource']
