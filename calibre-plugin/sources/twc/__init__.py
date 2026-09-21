# -*- coding: utf-8 -*-
"""TWC (Transformative Works and Cultures) journal import source."""

try:
    from calibre_plugins.fanfic_organizer.sources.twc.source import TwcSource
except ImportError:
    from sources.twc.source import TwcSource

__all__ = ["TwcSource"]
