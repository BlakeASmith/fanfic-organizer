"""Compatibility shim — prefer: from ao3kit.http import ..."""

from ao3kit import http as _http
import sys

sys.modules[__name__] = _http
