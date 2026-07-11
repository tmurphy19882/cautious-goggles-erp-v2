"""ERP v2 — `uvicorn src.app:app` entry point.

Re-exports `create_app_v2()` from `api.main` so the common command
`uvicorn src.app:app --reload --port 8001` works out of the box.
"""
from __future__ import annotations

from api.main import create_app_v2

app = create_app_v2()
