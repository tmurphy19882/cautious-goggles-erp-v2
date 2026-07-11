"""Dump the current OpenAPI schema for ERP v2 to docs/openapi/erp.json.

Usage:
    erp-v2-dump-openapi                # writes docs/openapi/erp.json (relative to repo root)
    erp-v2-dump-openapi --out path.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description="Dump ERP v2 OpenAPI to a JSON file.")
    parser.add_argument(
        "--out",
        default=None,
        help="Output path. Default: ../../docs/openapi/erp.json (relative to services/erp/src)",
    )
    args = parser.parse_args()

    # Make `src` importable so `api.main` resolves.
    here = Path(__file__).resolve().parent
    src = here.parent / "src"
    sys.path.insert(0, str(src))

    from api.main import create_app_v2  # noqa: E402

    app = create_app_v2()
    schema = app.openapi()

    out = Path(args.out) if args.out else (here.parent.parent / "docs" / "openapi" / "erp.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(schema, indent=2, sort_keys=True), encoding="utf-8")
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
