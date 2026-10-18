#!/usr/bin/env python
"""Bootstrap launcher that also works on embedded/portable Python builds."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from buslens.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
