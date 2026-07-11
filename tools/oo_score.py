"""Entry point for the OO scorer and baseline ratchet.

Run as ``python tools/oo_score.py <src> [flags]``. The implementation lives in
the ``oo_ratchet`` package beside this script; importable programmatically as
``from tools.oo_ratchet import ...``.
"""

from __future__ import annotations

import sys

from oo_ratchet.cli import main

if __name__ == "__main__":
    sys.exit(main())
