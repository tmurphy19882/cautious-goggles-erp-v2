# scripts/ — local dev helpers

Wave-by-wave scripts land here as they're needed. Each script is self-documenting (`--help`).

Planned:

- `bootstrap_local.sh` — start infra + run migrations + seed a tenant
- `seed_demo_tenant.py` — create `admin@acme.com / admin123` with sample data
- `dump_openapi.py` — write `docs/openapi/erp.json` from the running app
- `check_event_coverage.py` — verify every domain event has an Avro schema
- `check_rbac_coverage.py` — verify every mutating route has a permission gate
