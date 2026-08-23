"""Compatibility shim — tests and new code should import ao3kit.rate.

Prefer: ``from ao3kit.rate import ...``.
"""

from ao3kit import rate as _rate
import sys

sys.modules[__name__] = _rate
