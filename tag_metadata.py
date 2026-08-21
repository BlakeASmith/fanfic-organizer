#!/usr/bin/env python3
"""Compatibility shim — prefer: from ao3kit.tags import ... / python -m ao3kit tags"""

from ao3kit.tags import metadata as _metadata
import sys

sys.modules[__name__] = _metadata

if __name__ == "__main__":
    raise SystemExit(_metadata.main())
