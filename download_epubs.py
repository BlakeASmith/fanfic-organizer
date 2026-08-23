#!/usr/bin/env python3
"""Compatibility shim — tests and new code should import ao3kit.epubs.

Prefer: ``from ao3kit.epubs import ...`` / ``python -m ao3kit download``.
"""

from ao3kit import epubs as _epubs
import sys

sys.modules[__name__] = _epubs

if __name__ == "__main__":
    raise SystemExit(_epubs.main())
