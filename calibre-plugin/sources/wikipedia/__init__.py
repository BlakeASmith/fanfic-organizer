# -*- coding: utf-8 -*-
"""Wikipedia source package for the Calibre plugin."""

from __future__ import annotations

try:
    from calibre_plugins.fanfic_organizer.sources.wikipedia.source import (
        WikipediaSource,
    )
except ImportError:
    from sources.wikipedia.source import WikipediaSource

__all__ = ['WikipediaSource']
