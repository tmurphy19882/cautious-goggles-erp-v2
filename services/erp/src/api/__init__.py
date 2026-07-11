"""API package: routers and the FastAPI app factory.

Modules:

- `health`: `/health` (liveness) and `/ready` (readiness: DB + outbox + Kafka).
- `main`: `create_app_v2()` — the FastAPI app factory. Wires
  observability, idempotency, error handlers, and per-module routers.
"""
