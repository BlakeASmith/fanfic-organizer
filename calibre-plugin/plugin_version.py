# -*- coding: utf-8 -*-
"""Calibre-compatible version tuple that Preferences → Plugins can display as SemVer.

Calibre renders ``plugin.version`` with ``'.'.join(map(str, plugin.version))``.
A 3-int tuple can only show ``X.Y.Z``. This tuple subclass still *compares* as
``(major, minor, patch)`` (so Calibre's numeric checks keep working) but
iterates as a single display string such as ``0.31.0-preview.12+7a4f9b2``.
"""

from __future__ import annotations


class CalibreVersion(tuple):
    """3-int version whose iteration yields ``display`` for the Plugins list."""

    display = ""

    def __new__(cls, parts, display=""):
        nums = tuple(int(part) for part in parts)
        if len(nums) != 3:
            raise ValueError("Calibre plugin version must be 3 integers")
        obj = tuple.__new__(cls, nums)
        obj.display = str(display or "%s.%s.%s" % nums)
        return obj

    def __iter__(self):
        yield self.display

    def __str__(self):
        return self.display

    def __repr__(self):
        return "CalibreVersion(%s, %r)" % (tuple.__repr__(self), self.display)


def plugin_display_string(version=None, display=None):
    """Human-readable plugin version (preview/PR strings when present)."""
    if display:
        return str(display)
    if version is None:
        return "0.0.0"
    shown = getattr(version, "display", None)
    if shown:
        return str(shown)
    return ".".join(str(part) for part in version)
