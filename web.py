#!/usr/bin/env python3
"""Deprecated compatibility shim — prefer ``python -m ao3kit`` or the Calibre plugin."""

from ao3kit import webapp as _webapp
import sys

sys.modules[__name__] = _webapp
