from __future__ import annotations

import sys
from pathlib import Path

WEBAPP_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = WEBAPP_ROOT.parent
BACKEND_ROOT = WEBAPP_ROOT / "backend"

for path in (PROJECT_ROOT, BACKEND_ROOT):
    path_str = str(path)
    if path_str not in sys.path:
        sys.path.insert(0, path_str)

from main import app  # noqa: E402
