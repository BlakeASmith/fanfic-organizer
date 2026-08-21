#!/usr/bin/env python3
"""Compatibility shim — prefer: uvicorn ao3kit.webapp:app --reload"""

from ao3kit import webapp as _webapp
import sys

sys.modules[__name__] = _webapp
