#!/usr/bin/env python3
"""Compatibility shim — tests and new code should import ao3kit.scrape.

Prefer: ``from ao3kit.scrape import ...`` / ``python -m ao3kit scrape``.
"""

from ao3kit import scrape as _scrape
import sys

sys.modules[__name__] = _scrape

if __name__ == "__main__":
    raise SystemExit(_scrape.main())
