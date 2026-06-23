"""Repo-root convenience launcher: ``python run.py``.

Equivalent to ``python -m alonarg`` but runnable directly from the repo root.
"""
from __future__ import annotations

import os
import sys

# Ensure the repo root is importable when run as a plain script.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from alonarg.__main__ import main  # noqa: E402

if __name__ == "__main__":
    main()
