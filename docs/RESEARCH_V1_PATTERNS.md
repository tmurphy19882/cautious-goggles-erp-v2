# Research — v1 Patterns to Adopt into ERP v2

> **Parent codebase under study:** `cautious-goggles` (v1) — see AUDIT.md for the
> full gap analysis. This document is the read-only research that drives W1+ design
> decisions.
>
> **v2 home:** `cautious-goggles-erp-v2` (`feat/erp-v2-w0-foundations` branch, W0 shipped).
>
> **Method:** every section below is sourced from a v1 file. Each finding carries:
> a 1-2 paragraph summary of how v1 does it, an explicit recommendation with a
> **Priority** (P0 / P1 / P2) and the **Wave** we should adopt it in.
>
> **Priority legend**
> - **P0** = take now (before W1 ships)
> - **P1** = take in the next 2 waves (W1 / W2)
> - **P2** = revisit later (W3+)
>
> **Wave legend**
> - W1–W10 per `docs/SPEC.md`
> - "v2 only" = v2 should invent (no useful parent equivalent exists)

---

## a) Outbox pattern (services/erp/src/outbox_poller.py + shared-kernel/outbox/)

### How v1 does it

v1 ships **two parallel implementations** of the same outbox pattern, and that is
itself a useful lesson:

1. **`services/erp/src/outbox_poller.py` (the in-tree ERP service):** writes
   `OutboxEvent` rows in the same SQLAlchemy session as the domain change, then
   polls them with `SELECT … FOR UPDATE SKIP LOCKED` (Postgres) or a plain
   `SELECT` (SQLite for tests) inside a single transaction. The poller publishes
   one event at a time via a `Producer` Protocol, awaits the broker ack, then
   stamps `published_at = CURRENT_TIMESTAMP` — both the lock and the stamp land
   in the same commit. Headers are built from the row's columns and the
   `traceparent` is lifted out of the JSONB `payload._meta.traceparent` because
   the table has no dedicated trace column. The CLI runs as a standalone process
   (`python -m services.erp.src.outbox_poller`); defaults: `OUTBOX_POLL_INTERVAL_SEC=1.0`,
   `OUTBOX_BATCH_SIZE=100`, broker `KAFKA_BOOTSTRAP_SERVERS=localhost:9092`. The
   Kafka producer is `confluent-kafka` configured with `enable.idempotence=true`,
   `acks=all`, `linger.ms=5`, and `produce().flush()` per record so the caller
   only marks a row published **after** the broker has durably accepted it.

2. **`packages/shared-kernel/src/shared_kernel/outbox/` (the platform package):**
   the same shape but cleaner — `OutboxPublisher` (write side) and
   `OutboxRelay` (poll side) with a `MessageProducer` Protocol, an
   `InMemoryProducer` test double, and the same `FOR UPDATE SKIP LOCKED`
   pattern. Default `batch_size=100`, `poll_interval_sec=0.5` (twice as fast
   as the in-tree version). The shared-kernel DDL adds a partial index
   `ix_outbox_unpublished` on `created_at WHERE published_at IS NULL` so the
   polling query is a tight range scan.

3. **DLQ / failure handling — **both v1 implementations are missing DLQ**.** The
   relay rolls back on exception and sleeps `poll_interval_sec`; a row that
   consistently fails to publish just gets re-tried forever. There is no
   `attempts` column, no `last_error`, no `dlq_at`, no `events_raw` shadow
   table. The audit flags this as **OPS-6 (P1)** and **S-18 (P1)**.

4. **Publish latency SLA — not defined.** Nothing measures p50/p95 between
   `created_at` and `published_at`. The `outbox_publish_latency_seconds`
   histogram exists in v2's `observability/metrics.py` and is explicitly
   missing from v1.

### What to adopt vs change

| # | Decision | Priority | Wave |
|---|----------|----------|------|
| 1 | **Adopt the `FOR UPDATE SKIP LOCKED` polling pattern verbatim** in W1's `outbox_poller`. v1 has production traffic on it. | P0 | W1 |
| 2 | **Adopt the outbox table schema as v1 declares it** (`id`, `tenant_id`, `aggregate_type`, `aggregate_id`, `event_type`, `topic`, `payload JSONB`, `created_at`, `published_at`). Same column order/types. v1's `outbox_events` table is the contract every consumer already reads from. | P0 | W1 (mig `0200_outbox.py`) |
| 3 | **Add the missing columns v1 lacks**: `attempts INT NOT NULL DEFAULT 0`, `last_error TEXT`, `dlq_at TIMESTAMPTZ NULL`, `first_published_at TIMESTAMPTZ NULL`. These are the only safe way to add a DLQ without a full rewrite. | P0 | W1 |
| 4 | **Adopt the shared-kernel `MessageProducer` Protocol + `InMemoryProducer` test double.** Lift it into `services/erp/src/o2c/outbox_producer.py` (or a `shared/outbox.py`) and let tests inject a fake. | P0 | W1 |
| 5 | **Adopt the partial index** `CREATE INDEX … ON outbox_events (created_at) WHERE published_at IS NULL` so the polling query is a tight range scan even with millions of historical rows. | P1 | W1 |
| 6 | **Change: add Prometheus histograms** `outbox_events_published_total{topic,result}` and `outbox_publish_latency_seconds{topic}` to W1's poller. v2's W0 already declares the metrics; v1 has neither. | P0 | W1 |
| 7 | **Change: add W3C traceparent as a top-level column** in the W1 migration (`traceparent TEXT NULL`) instead of burying it under `payload._meta.traceparent`. v1's indirection through JSONB is convenient but breaks trace correlation when downstream consumers can't see the payload. | P1 | W1 |
| 8 | **Adopt `enable.idempotence=true, acks=all, linger.ms=5`** as the default producer config. Do not roll our own. | P0 | W1 |
| 9 | **Change: add an actual DLQ** — after N=5 publish failures, move the row to `outbox_events_dlq` and emit a metric. v1 has no DLQ at all. | P1 | W1 (table) / W10 (alerting & chaos) |
| 10 | **Do not adopt v1's CLI-as-standalone-process** pattern unchanged. v1's `python -m services.erp.src.outbox_poller` works but won't survive horizontal scale. W1 should ship the poller as a *sidecar* container alongside the FastAPI app, with `app.state.outbox_poller_task` started in `lifespan`. | P1 | W1 |
| 11 | **Target publish latency SLA: p95 ≤ 2s** (with batch=100, poll=0.5s) per the v2 SPEC's O2C-1 contract. Encode as a Prometheus alert. | P1 | W1 |

### v2's W1 plan, as informed by the above

- One outbox poller per replica, started in `lifespan`, sharing the FastAPI
  app's engine and session factory.
- W1 migration `0200_outbox.py` adds `outbox_events` **with v1's columns plus
  the four v1-missing columns** (attempts, last_error, dlq_at,
  first_published_at).
- W1 migration `0201_outbox_rls.py` adds RLS for `outbox_events` (tenant_id
  match, matching the `app.tenant_id` GUC).
- The poller uses the same `FOR UPDATE SKIP LOCKED` + per-row publish + same
  transaction ack pattern.
- W1 wraps everything in OTel spans (`outbox.poll` per batch, `outbox.publish`
  per record) and increments the W0 metrics.

---

## b) Event payload shape (services/erp/src/o2c/order_flow.py + trade/events.py + shared-events/schemas/)

### How v1 does it

Every v1 event payload is a flat dict that **mixes envelope fields with
domain fields** at the top level — no nested `envelope` key. The convention is
set by the build helpers in `o2c/order_flow.py:build_order_confirmed_payload`
and re-used verbatim by `trade/events.py:build_party_*_payload`,
`build_hts_schedule_updated_payload`, `build_customs_entry_filed_payload`,
`build_ftz_inventory_admitted_payload`. Every payload carries exactly the same
six envelope fields:

```python
{
    "event_id":   str(uuid4()),                 # UUID-as-string
    "event_type": "scm.erp.order-confirmed.v1", # == topic
    "tenant_id":  str(uuid4()),
    "occurred_at": int(now_utc_ms),             # epoch-millis (long in Avro)
    "producer":   "erp@0.1.0",                  # "<service>@<semver>"
    "trace_id":   traceparent-or-event_id,      # W3C trace context trace_id
    # … domain fields follow, flat at the same level …
    "order_id": …, "customer_token": "cust:…", "lines": [...], …
}
```

The `customer_token` field is critical: v1 explicitly **tokenizes customer IDs
in the wire payload** (the form is `cust:<uuid>`) so the canonical UUID is
never leaked across service boundaries. PII never enters the event.

Decimal amounts are serialised as **strings** with a fixed format
(`format(Decimal("12.34"), "f")` → `"12.3400"`) so they survive JSON
round-trip without float drift. Avro schemas in
`packages/shared-events/schemas/erp/order-confirmed/v1.avsc` use the
`bytes` + `logicalType=decimal` + `precision=20, scale=4` pattern that matches
this string representation. v1 also encodes dates as days-since-epoch
(`int logicalType=date`), which is what `order_date` does in the OrderConfirmed
schema.

**Topic naming** is `scm.<module>.<event-name>.v<version>`. v1's
`shared-events/topics.py` registers 21 topic keys; the
`packages/shared-events/compatibility.json` file pins every one to
`BACKWARD_TRANSITIVE` so Apicurio refuses breaking changes at the registry.

**The envelope-vs-domain split is a real choice.** v1 chose flat-blend
("envelope inlined with domain fields") which makes the JSON one less
indirection but makes generic event-router code harder to write (you have to
either whitelist envelope fields by name, or build a separate typed envelope
for code paths that need it). The shared-events package's
`shared_events/envelope.py:EventEnvelope` exists precisely as the *typed*
view for code that wants one, and `EventEnvelope.to_dict()` flattens
`payload` fields into the same top-level dict.

### What to adopt vs change

| # | Decision | Priority | Wave |
|---|----------|----------|------|
| 1 | **Adopt the flat-blend envelope shape verbatim.** v1, Apicurio, registry-sync.ts, and every WMS/TMS/QMS consumer all parse it that way. | P0 | W0 (now, in docs) / W1 (in code) |
| 2 | **Adopt the six canonical envelope field names** exactly: `event_id`, `event_type`, `tenant_id`, `occurred_at`, `producer`, `trace_id`. Match case, snake_case, and types. | P0 | W0 (now) |
| 3 | **Adopt `producer = "<service>@<semver>"` format** verbatim. W1 services ship as `erp-v2@0.1.0`. | P0 | W1 |
| 4 | **Adopt `customer_token` tokenization on every customer-id-bearing event.** Never emit the raw `customer_id` UUID; always `cust:<uuid>`. PII rule from v1. | P0 | W1 |
| 5 | **Adopt the topic naming convention `scm.<module>.<event-name>.v<version>`** exactly. v1's `shared-events/topics.py:TOPICS` is the registry. | P0 | W0 |
| 6 | **Adopt the decimal-as-string + Avro `bytes/decimal(20,4)` convention.** v2 W1's Pydantic v2 schemas should use `Decimal` types, but the JSON wire format is a string. | P0 | W1 |
| 7 | **Adopt the Apicurio compatibility gate** (`registry-sync.ts` + `compatibility.json`). The `scripts/validate_manifest.py` mentioned in the v2 SPEC W10 *is* the renamed version of v1's `registry-sync.ts`. | P1 | W1 (Avro schemas) / W10 (CI gate hardens) |
| 8 | **Adopt the typed `EventEnvelope` dataclass** for v2 service code that builds events in-process (W1's O2C). The flat-blend happens at the very last step (`to_dict()`) so call sites stay type-safe. | P1 | W1 |
| 9 | **Change: in v2 SPEC, lock the producer field** to `erp-v2@<semver>` and **start semver from 2.0.0** for v2 — not `erp@0.1.0`. v2 is a new service, not an upgrade of `erp@0.1.0`. | P0 | W0 |
| 10 | **Change: add an explicit `schema_version` envelope field** for forward-compat. v1 has it implicit in the topic suffix; v2 should add it inside the payload too (default `1`). The Apicurio topic already enforces this; the field is for in-process defensive code. | P2 | W3+ (only if a real compat issue shows up) |
| 11 | **Change: encode `occurred_at` as ISO 8601 string in JSON** (e.g. `"2026-07-11T16:13:00.123Z"`), not epoch-millis. The v1 epoch-millis choice is fine for Avro but the *JSON* layer in v2 should be human-debuggable. Keep epoch-millis as the Avro wire type. | P1 | W1 |

### v2's W1 plan, as informed by the above

- W1 writes `services/erp/src/o2c/envelope.py` mirroring v1's
  `shared_events/envelope.py:EventEnvelope` (frozen dataclass with
  `to_dict()`) and a `build_order_confirmed_payload(...)` factory that
  takes an `EventEnvelope` and merges domain fields.
- W1 ports `services/erp/src/o2c/order_flow.py` to use this envelope, emits
  `producer = "erp-v2@0.1.0"`, tokenizes customer IDs, and serialises
  decimals as strings.
- W1's OpenAPI dump lists every event schema in `docs/openapi/erp.json` so
  the Apicurio gate (W10) can compare.

---

## c) In-process event bus (services/erp/src/event_bus.py + flow-sagas/event_bus.py)

### How v1 does it

v1's `services/erp/src/event_bus.py` is the *write-side* seam. It is a
3-mode producer:

1. **In-process dispatch** (default for local dev / unit tests) — appends
   `(topic, payload, headers)` to a module-level `_published` list and
   fires every subscriber registered via `subscribe(topic, handler)`.
2. **Kafka via `aiokafka`** (production) — `AIOKafkaProducer` started on
   first publish, `bootstrap_servers` from `KAFKA_BOOTSTRAP_SERVERS` env.
3. **Auto-fallback** — if `aiokafka` import/start fails, set
   `_use_kafka = False` and use in-process. This is how the v1 test suite
   runs without a broker.

The single hard-coded topic constant is `TOPIC_ORDER_CONFIRMED =
"scm.erp.order-confirmed.v1"` (line 20). The module-level state is
process-local; nothing crosses the process boundary without going through
`publish_to_kafka()`.

The `InProcessBusProducer` (line 67) is the test double for
`outbox_poller.Producer`. It `json.loads(value)` and re-dispatches via
`publish_to_kafka`, which lets the outbox-relay-style test path exercise
both the in-process bus **and** the registered stub consumers in one go.
The v1 test `tests/test_o2c_happy_path.py:48-52` depends on this seam: it
asserts that after one `poll_once`, both `get_pick_logs()` and
`get_shipment_logs()` were called.

There is **no Apicurio client in the producer path.** v1 builds the JSON
payload via `json.dumps(payload).encode("utf-8")` and ships. The Avro
schema in `packages/shared-events/schemas/erp/order-confirmed/v1.avsc` is
the *contract* but is not enforced at produce time — there is no
`validate(payload, schema)` call in v1's `publish_to_kafka`.

**Consumer-group naming:** v1 doesn't run real Kafka consumers; the in-process
stubs use module-level constants like `wms_stub.CONSUMER_GROUP = "wms-o2c"`
and `tms_stub.CONSUMER_GROUP = "tms-o2c"`. The convention is
`<service>-<saga>` (e.g. `wms-o2c`, `tms-o2c`).

### What to adopt vs change

| # | Decision | Priority | Wave |
|---|----------|----------|------|
| 1 | **Adopt the `subscribe(topic, handler)` + `publish(topic, payload, headers)` contract verbatim** for v2's W1 in-process bus. Same 3-tuple return shape. | P0 | W1 |
| 2 | **Adopt the consumer-group naming convention** `<service>-<saga>` (e.g. `wms-o2c`, `tms-o2c`, `qms-holdgate`). Encode in a helper `consumer_group("wms", "o2c")` so we never typo. | P0 | W1 |
| 3 | **Adopt the in-process-bus-first / Kafka-when-available auto-fallback** so unit tests run without a broker. v2's W1 keeps the `KAFKA_BOOTSTRAP_SERVERS` env probe. | P0 | W1 |
| 4 | **Adopt the `InProcessBusProducer` test double** for the outbox poller's `Producer` Protocol. | P0 | W1 |
| 5 | **Change: add Apicurio client at the produce-time validator** in W1. v1 has the schemas but doesn't validate; v2 should call `apicurio_registry.validate(payload, schema_id)` on every `publish_to_kafka` call. Audit OPS-7. | P1 | W1 (in-process validator) / W10 (CI gate) |
| 6 | **Change: when Kafka is in play, use `confluent-kafka` (v1's choice) not `aiokafka`.** v1's `outbox_poller.py` already uses it for at-least-once with `enable.idempotence=true, acks=all`. `aiokafka` is in `event_bus.py` for the standalone process path only. Pick one. v2 W1 should pick `confluent-kafka` for the production path. | P0 | W1 |
| 7 | **Do not adopt v1's module-level globals** (`_subscribers`, `_published`, `_kafka_producer`). v2 W1 should wrap the bus in a class so multiple app instances (tests, worker process) don't share state. | P0 | W1 |
| 8 | **WMS/TMS/QMS consume contract** — see section (h). | — | — |

### v2's W1 plan, as informed by the above

- W1 ships `services/erp/src/o2c/bus.py` — a class-based version of
  `event_bus.py` with the same `subscribe` / `publish` API, plus a
  `KAFKA_BOOTSTRAP_SERVERS` auto-fallback.
- W1 ships `services/erp/src/o2c/outbox_producer.py` with the `Producer`
  Protocol and an `InProcessBusProducer` test double.
- W1 ships `services/erp/src/o2c/consumer_groups.py` with
  `consumer_group("wms", "o2c") → "wms-o2c"`.

---

## d) Idempotency (services/erp/src/idempotency_store.py + shared-kernel/idempotency/)

### How v1 does it

v1 has the same split as outbox: a service-local
`services/erp/src/idempotency_store.py:DbIdempotencyStore` (Postgres-backed
via the `idempotency_keys` ORM model in `models.py:66-74`) and a
platform-level `packages/shared-kernel/idempotency/` with three
implementations of the same `IdempotencyStore` Protocol:

1. **`InMemoryIdempotencyStore`** — dict + lock set for tests.
2. **`RedisIdempotencyStore`** — `SET NX` for `try_acquire`, `GET` for
   `get`, `SETEX` for `save`. 24h TTL via `DEFAULT_TTL`.
3. **DB-backed `DbIdempotencyStore`** (in-tree) — same shape, no
   `try_acquire` (only `get`/`save`), Postgres-specific.

The middleware in `packages/shared-api/src/shared_api/middleware.py`
+ `idempotency.py:IdempotencyMiddleware` enforces `Idempotency-Key` on
**POST only** (the `MUTATING_METHODS = frozenset({"POST"})` constant).
v1's middleware:
- computes `key_hash = sha256(f"{tenant_id}:{key}")` from
  `hash_idempotency_key`
- computes `request_hash = sha256(body)`
- on hit with same request hash → replays cached response verbatim
- on hit with different request hash → 409 with `ErrorCode.IDEMPOTENCY_CONFLICT`
- on miss → runs handler, saves response if status < 500
- **requires the key** by default (`require_key=True`)

The table schema in `packages/shared-api/idempotency.py:IDEMPOTENCY_KEYS_MIGRATION_SQL`
includes a `UNIQUE (tenant_id, key_hash)` constraint so concurrent inserts
are safe (a second writer hits the constraint and the middleware treats
the second request as a hit). v1's W0-era table model in
`services/erp/src/models.py:IdempotencyKey` is a simpler shape — composite
PK on `(tenant_id, key_hash)`, no `response_headers` column, no
`BIGSERIAL id`.

### What v2 already has (W0)

`services/erp/src/shared/idempotency.py` is a clean v2 implementation
that already adopts v1's essentials:
- `IdempotencyMiddleware` enforcing on **POST / PUT / PATCH / DELETE**
  (one step further than v1 — see change #1 below)
- Postgres `DbIdempotencyStore` using
  `pg_insert(...).on_conflict_do_nothing(index_elements=["tenant_id", "key_hash"])`
  for the concurrent-insert safety
- `IdempotencyRecord` dataclass matching the v1 column shape
- `current_tenant_id(request)` for tenant resolution (v1 used
  `_default_tenant_id_getter` with the same priority order)
- `IdempotencyKeyRequiredError` and `IdempotencyKeyMismatchError` error
  codes that mirror v1's

W0 also declares `idempotency_hits_total{route}` in
`observability/metrics.py:Metrics` (v1 has no metrics on this path).

### What to adopt vs change

| # | Decision | Priority | Wave |
|---|----------|----------|------|
| 1 | **Already adopted: PUT/PATCH/DELETE coverage in v2 W0**. This is broader than v1 (POST-only). Keep it. | P0 | W0 (done) |
| 2 | **Adopt the v1 `Idempotency-Key` HTTP header name verbatim.** Already done in v2 W0 (`IDEMPOTENCY_HEADER = "Idempotency-Key"`). | P0 | W0 (done) |
| 3 | **Adopt v1's `request_hash != key_hash` body-mismatch 409 contract** verbatim. v2 W0 already does this. | P0 | W0 (done) |
| 4 | **Change: v1 uses `sha256(f"{tenant_id}:{key}")`; v2 W0 uses `sha256(key.encode("utf-8"))`** (no tenant prefix). v2 should adopt v1's tenant-prefixed hash so two tenants cannot collide on the same key. | P0 | W0 (small fix before W1) |
| 5 | **Adopt the `UNIQUE (tenant_id, key_hash)` constraint** in the W1 migration. v2 W0's `DbIdempotencyStore` already uses `on_conflict_do_nothing` with that index, so the constraint is the SQL reality. | P0 | W1 |
| 6 | **Do not adopt v1's `InMemoryIdempotencyStore.try_acquire`** for production — it doesn't survive multi-replica deploys. v2's `DbIdempotencyStore` is the production store; the in-memory one stays in `tests/`. | P0 | W1 |
| 7 | **Do not adopt v1's `RedisIdempotencyStore` in v2 W0** — v2's `DbIdempotencyStore` is sufficient and avoids the new Redis dependency. Add Redis store as P1 in W6 if hot-path latency becomes an issue. | P2 | W6 (only if measured) |
| 8 | **Add a `delete_expired` scheduled job** in v2 W1. v1 has no cleanup; rows stay forever. v2 W0 already declares `delete_expired` in the store but no job. | P1 | W1 |
| 9 | **Adopt v1's `IdempotencyKeyRequiredError` and `IdempotencyKeyMismatchError` shape** in v2's error envelope. Already done. | P0 | W0 (done) |
| 10 | **Add the `response_headers JSONB` column** from v1's shared-api DDL — v2 W0 only stores `response_body` + `status`, which is fine for JSON responses but loses any `Location` header a 201 returns. | P1 | W1 |

### v2's W1 plan, as informed by the above

- One-line change to `shared/idempotency.py` to tenant-prefix the key hash
  (priority P0 — do this in W0 patch before W1 starts).
- W1 migration `0202_idempotency_keys.py` adds the table with
  `UNIQUE (tenant_id, key_hash)`, `response_headers JSONB`, `expires_at
  TIMESTAMPTZ`, partial index on `expires_at`.
- W1 ships `services/erp/src/jobs/idempotency_gc.py` (a simple async
  job running every hour) that calls `IdempotencyStore.delete_expired()`.

---

## e) shared-kernel (packages/shared-kernel/)

### What's in v1's shared-kernel

| Module | Purpose | v2 equivalent |
|--------|---------|---------------|
| `tenant_context` | `contextvars` `tenant_id`/`user_id`/`trace_id` + `tenant_scope()` ctx mgr | `shared/tenant.py:current_tenant_id(request)` (request-scoped, not contextvar-scoped) |
| `middleware.tenant_context` | FastAPI `TenantContextMiddleware` — header/JWT → `request.state.tenant_id` + `SET LOCAL app.tenant_id` | `observability/middleware.py:ObservabilityMiddleware` (does the same, broader scope) |
| `outbox` | `OutboxPublisher` (write) + `OutboxRelay` (poll) | **Missing in v2 W0** (planned for W1) |
| `idempotency` | `RedisIdempotencyStore` + `InMemoryIdempotencyStore` + `IdempotencyStore` Protocol + `IdempotentConsumer` (for `processed_events` dedup) | `shared/idempotency.py` (DB-backed, mostly superset) |
| `auditing` | `HashChainWriter` + `AuditLogImmutable` ORM + `PersistentAuditLog` (per-tenant hash chain audit log) | **Missing in v2 W0** (planned for W6) |
| `rls` | `policies.sql` template — RLS DDL snippet for `app.tenant_id` GUC | `migrations/versions/0101_identity_rls.py` (inlined RLS) |
| `saga` | `SagaOrchestrator` (compensation semantics) + `SagaRepository` + `SagaInstance`/`SagaStepRecord` Pydantic models | **Missing in v2 W0** (planned for W1/W2 with Temporal) |
| `migrations` | `tenant_aware_runner.py` — canary tenant migration runner | Not in v2 |
| `export` | `engine.py` — sync/async export engine stub | Not in v2 (W5 saved views / W6 import-export) |
| `workflow` | Empty package (BPM placeholder) | Not in v2 |
| `tracing` | `setup.py:setup_tracing()` (OTLP exporter, FastAPI instrumentor) | `observability/tracing.py:init_tracing()` (cleaner version) |
| `encryption` | Empty `__init__.py` (placeholder) | Not in v2 |

### How v1 consumes it from services/erp/src/db.py

`db.py` is *not* the consumer; the shared-kernel is consumed by the v1
service's `outbox_poller.py` and `idempotency_store.py` and by the
saga code in `flow-sagas/`. Specifically:

- `outbox_poller.py:14` imports `from shared_api.idempotency import
  IdempotencyRecord, IdempotencyStore` (via the **shared-api** package,
  not shared-kernel)
- `idempotency_store.py:13-14` imports `from models import IdempotencyKey`
  and `from shared_api.idempotency import IdempotencyRecord, IdempotencyStore`
- `flow-sagas/runner.py` imports `from shared_kernel.saga.orchestrator
  import SagaOrchestrator`

In other words, **shared-kernel + shared-api are the *protocol contracts*
and the *in-memory implementations***; **the per-service `models.py` is
the *Postgres-backed implementation***. v1 ships a `IdempotencyKey` ORM
model so `DbIdempotencyStore` can read/write the `idempotency_keys` table
through SQLAlchemy.

### v2's `shared/` — overlap and gap

`services/erp/src/shared/` is a *superset* of v1's
`shared-kernel`+`shared-api`:

- `db.py` — async engine, session factory, `set_tenant_context`,
  `session_scope`. v1's `db.py` is similar but smaller. v2 wins on
  `session_scope` context manager and explicit
  `set_tenant_context(tenant_id, ...)` API.
- `errors.py` — `ErrorEnvelope` + `ApiError` hierarchy + FastAPI
  handlers. v1's `shared_api.errors` is similar; v2 ships typed
  exception classes (`IdempotencyKeyRequiredError`, etc.) which v1
  doesn't.
- `schemas.py` — Pydantic v2 base models (`AppModel` with
  `model_config = ConfigDict(extra="forbid", from_attributes=True)`,
  `TenantBoundModel`, `MoneyDecimal`, `PageRequest/Response`,
  `utcnow`). v1 has no equivalent — domain code uses raw `dict` and
  `dataclass`. v2 wins here.
- `idempotency.py` — see section (d).
- `tenant.py` — `current_tenant_id(request)` + `require_tenant_id`
  dependency. v1 has the equivalent in `shared_kernel.tenant_context`
  but as contextvars (which v2 W0 already covers via the OTel
  middleware that sets them).
- `time.py` — `utcnow`, `to_utc`. v1's `datetime.now(timezone.utc)` is
  used inline everywhere; v2 wins on the helper.

**Gaps v1 has that v2 W0 does not yet have:**

| v1 module | v2 W0 status | When to adopt |
|-----------|--------------|---------------|
| `shared_kernel.outbox` (publisher + relay) | Not in v2 W0 | W1 (outbox poller) |
| `shared_kernel.saga.orchestrator` | Not in v2 W0 | W1 (in-process fallback) / W2 (Temporal) |
| `shared_kernel.auditing` (hash chain) | Not in v2 W0 | W6 (audit log read API) |
| `shared_kernel.idempotency.consumer` (ProcessedEvent dedup) | Not in v2 W0 | W1 (WMS/TMS/QMS consumer dedup) |
| `shared_kernel.rls.policies.sql` template | Inlined per migration in v2 W0 | W1 (extract template) |
| `shared_kernel.tracing.setup` (FastAPI instrumentor) | v2 W0 has `init_tracing` but no auto-instrumentor | W1 |
| `shared_kernel.export.engine` | Not in v2 W0 | W6 (CSV/XLSX import-export) |

### What to adopt vs change

| # | Decision | Priority | Wave |
|---|----------|----------|------|
| 1 | **Adopt v1's split between protocol contracts (shared-kernel) and DB-backed implementations (per-service)**. v2 W0 already mirrors this — `IdempotencyStore` is the protocol, `DbIdempotencyStore` is the impl. | P0 | W0 (done) |
| 2 | **Adopt v1's `processed_events` consumer dedup table** in W1. v1's `shared-kernel/idempotency/consumer.py:IdempotentConsumer` is the pattern. The `processed_events` table lives in the consumer's service DB, not a shared DB. | P0 | W1 (WMS consumer) / W2 (QMS consumer) |
| 3 | **Adopt v1's `SagaRepository` + `SagaInstance` / `SagaStepRecord` Pydantic model shape** as the v2 W1 in-process saga contract. Keep the v1 names so the W1 in-process implementation is a drop-in for a future Temporal workflow. | P1 | W1 |
| 4 | **Adopt v1's `HashChainWriter` for the audit log** in W6. The tamper-evident chain is non-trivial to retrofit; v1's implementation is already there. | P1 | W6 |
| 5 | **Adopt v1's `TenantContextMiddleware` shape** — the JWT-or-header getter. v2 W0's `observability/middleware.py:ObservabilityMiddleware` does this, but the **JWT decode logic** in v1's `extract_tenant_from_jwt` is more thorough (handles `tenantId` and `companyId` aliases). | P0 | W1 (when JWT lands) |
| 6 | **Change: do not lift v1's `IdempotencyKey` ORM model verbatim.** v1's table has no `id` column, no `response_headers` column, and no `expires_at` index. v2's W0 ID is `BIGSERIAL`-shaped (or `(tenant_id, key_hash)` composite PK — pick one). Use v1's ADR-0011 DDL with `response_headers JSONB`. | P0 | W1 |
| 7 | **Change: keep v2's `shared/` as a flat dir, not a multi-package layout.** v1's split into `shared-kernel` + `shared-api` + `shared-events` + `flow-sagas` was driven by *cross-service reuse*. v2 is a single service in this repo; the flat dir is correct for now. Re-split in W6 if `platform/` becomes a separate package. | P2 | W6 |
| 8 | **Do not adopt v1's `encryption` empty placeholder** — YAGNI. | — | — |
| 9 | **Do not adopt v1's `workflow` empty placeholder** — Temporal replaces this. | — | — |

---

## f) Sagas (packages/flow-sagas/)

### How v1 does it

v1 has a **fully working in-process saga orchestrator** with
compensation semantics, idempotency per step, hold-gate suspension, and
typed Pydantic models. It is not a placeholder.

**The model** (`shared-kernel/saga/models.py`):

- `CompensationStrategy`: `REVERSIBLE` | `COMPENSATING` | `IRREVERSIBLE`
- `SagaStatus`: `PENDING` | `RUNNING` | `COMPENSATING` | `COMPLETED`
  | `FAILED` | `SUSPENDED` | `ABANDONED`
- `StepStatus`: `PENDING` | `RUNNING` | `COMPLETED` | `FAILED`
  | `COMPENSATED` | `SKIPPED`
- `CompensationStatus`: `NOT_REQUIRED` | `PENDING` | `COMPLETED` | `FAILED`
- `SagaStepDefinition`: `name`, `action` (async callable), `strategy`,
  `compensate` (async callable, optional), `pre_validation` (async bool)
- `SagaStepRecord`: persisted status per step
- `SagaInstance`: id, tenant_id, saga_type, status, context (dict),
  list of `SagaStepRecord`

**The orchestrator** (`shared-kernel/saga/orchestrator.py:SagaOrchestrator`):

1. `start(saga_type, steps, *, context, saga_id)` writes a new
   `SagaInstance` + a `SagaStepRecord` per step, then runs the steps.
2. For each step: try to acquire idempotency lock via
   `hash_idempotency_key(saga_id:step_index, tenant_id)`. If already
   acquired, mark `COMPLETED` (resume from a previous run).
3. If the step is `IRREVERSIBLE`, run `pre_validation` first; raise
   `IrreversibleStepError` if it fails.
4. Set step to `RUNNING`, `attempts += 1`, save.
5. Call `await step_def.action()`.
6. On success: `COMPLETED`, continue.
7. On failure: if the exception is in `suspend_exceptions` (the
   `HoldBlockedError`), set step `FAILED`, saga `SUSPENDED`, record
   `suspension_reason` and `suspended_at_step` in context. Return without
   compensation.
8. Otherwise: set step `FAILED`, saga `COMPENSATING`, run `compensate` for
   every prior `REVERSIBLE` step in reverse, then `saga = FAILED`, raise
   `SagaError`.

**The hold gate** (`flow-sagas/hold_gate.py:HoldGate`):
- Maintains a per-tenant `dict[tenant_id, list[HoldBlock]]`.
- `add_block(tenant_id, hold_id, sku, lot_id)` adds a hold.
- `is_blocked(tenant_id, *, sku, lot_id, operation)` returns True if a
  matching hold exists for the given operation (`wms_pick` or
  `erp_invoice`).
- Subscribes to `TOPIC_HOLD_BLOCKS = scm.qms.hold-blocks-updated.v1` in
  the in-process bus; on event, adds or removes blocks.

**The flow-sagas package** wires concrete sagas:

- `sagas/o2c.py:build_o2c_steps(ctx, hold_gate)` — 8 steps:
  `validate_credit` → `create_so` → `reserve_inventory` →
  `create_pick` → `create_load` → `post_gl_encumbrance` →
  `create_invoice` → `send_confirmation`
- `sagas/p2p.py:build_p2p_steps(ctx)` — 6 steps:
  `firm_buy` → `create_po` → `receive_goods` → `inspect` →
  `three_way_match` → `post_payment`
- `sagas/customs_entry.py:build_customs_entry_steps(ctx)` — 5 steps:
  `draft_entry` → `screen_parties` → `file_with_broker` →
  `await_release` → `notify_tms_pickup`

Each step is a `partial(some_handler, ctx)`. The handlers in
`flow-sagas/handlers/{erp,tms,wms,mrp_qms}.py` are stubs that mutate
`SagaContext.data` and emit in-process events. They are *not* the
real ERP/WMS/TMS handlers — they exist to make the saga testable in
isolation. The tests in `flow-sagas/tests/test_o2c_saga.py`,
`test_p2p_saga.py`, `test_customs_entry_saga.py`, `test_hold_gate.py`,
`test_saga_resume.py` cover happy-path, compensation, QMS hold
suspension, and resume.

### What to adopt vs change

| # | Decision | Priority | Wave |
|---|----------|----------|------|
| 1 | **Adopt v1's `CompensationStrategy` / `SagaStatus` / `StepStatus` enum shape verbatim** as the v2 in-process saga contract. Keep the names so the W1 in-process impl is a drop-in for W2 Temporal. | P0 | W1 (saga in-process) / W2 (Temporal) |
| 2 | **Adopt v1's `SagaStepDefinition` and `SagaInstance` Pydantic model shape** verbatim. | P0 | W1 |
| 3 | **Adopt v1's step-idempotency key format** `{saga_id}:{step_index}` (so re-running the saga is safe). | P0 | W1 |
| 4 | **Adopt v1's `HoldGate` + `HoldBlockedError` pattern** for QMS hold integration. The saga suspends (does not compensate) when `HoldBlockedError` is raised. | P1 | W1 (if QMS consumer lands) / W2 |
| 5 | **Adopt v1's `suspend_exceptions` mechanism on the orchestrator.** v1's `suspend_exceptions=(HoldBlockedError,)` is the single seam for "halt but don't compensate." | P1 | W1 |
| 6 | **Do NOT adopt v1's concrete saga step handlers** (the stubs in `flow-sagas/handlers/erp.py`). v2's W1 handlers will call the real `o2c/sales-order/` module, not stub functions. | P0 | W1 |
| 7 | **Do adopt v1's `SagaContext` shape** (tenant_id, trace_id, data dict). v1's `flow-sagas/context.py:SagaContext` is 24 lines — copy it. | P1 | W1 |
| 8 | **W1 plan: ship v2's in-process orchestrator as a port.** The interface is "SagaOrchestrator.start(saga_type, steps, ...) → SagaInstance". The W1 implementation is the v1 orchestrator copy-paste; the W2 implementation wraps a Temporal workflow. Same `SagaInstance` shape in both. | P0 | W1 |
| 9 | **W2 plan: Temporal migration.** Use Temporal's Python SDK; the workflow class is `OrderToCashWorkflow` per the v2 SPEC. The `SagaInstance` is the *internal* representation; Temporal's workflow ID + history is the *external* representation. Map one to the other in the W2 glue layer. | P1 | W2 |
| 10 | **Change: lift v1's `saga` and `saga-orchestrator` into v2's `o2c/sagas/` and `p2p/sagas/`** rather than into a `flow-sagas` package. The cross-service package was for the monorepo. v2 is a single service; the saga code lives next to the module that owns it. | P1 | W1 |
| 11 | **Do not adopt v1's `export.engine` or `workflow` placeholders.** YAGNI. | — | — |

---

## g) Trade module (services/erp/src/trade/) — the most mature v1 part

### How v1 does it

7 files, ~1280 lines. Every one of them works.

| File | Purpose | Lines | Status |
|------|---------|------:|--------|
| `trade/models.py` | SQLAlchemy 2.0 ORM: `Party`, `DeniedPartyEntry`, `DeniedPartyScreen`, `HtsScheduleVersion`, `HtsScheduleEntry`, `TariffAlertSubscription`, `CustomsEntry`, `CustomsEntryLine`, `FtzInventory` + 3 enums (`PartyType`, `ScreeningStatus`, `DeniedListSource`, `HtsVersionStatus`) | 231 | **Production-ready** |
| `trade/screening.py` | Denied-party screening: `normalize_entity_name()`, `SEED_DENIED_ENTRIES` (4 rows), `assess_risk()`, `screen_entity()`, `create_party()`, `update_party()`. Re-screens on any Party field change in `update_party()`. | 339 | **Production-ready**, with the W3+ gap that screening is skipped for `employee` / `internal_org` Party types (audit TR-4, TR-5) |
| `trade/hts_engine.py` | HTS tariff engine with **versioned reference data** + Section 232/301 overlays. `HtsScheduleVersion` (DRAFT/ACTIVE/SUPERSEDED) with `superseded_by_id` linkage, `HtsScheduleEntry` (UNIQUE on `(version_id, hts_code, country_of_origin)`), `load_hts_schedule()`, `lookup_tariff()`, `_notify_tariff_subscribers()`, `ensure_default_hts_schedule()` seeded with 5 entries (3 base + 1 s232 + 1 s301). | 251 | **Production-ready** |
| `trade/customs.py` | Customs entry lifecycle. `create_customs_entry()` in DRAFT (computes `total_value`, `total_duty` from HTS lookups), `file_customs_entry()` transitions to FILED and emits `scm.erp.customs-entry-filed.v1`. | 175 | **Production-ready**, missing the `pending_ftz` / `released` states the SPEC wants (audit TR-8) |
| `trade/ftz.py` | FTZ inventory admission. Upserts on `(tenant_id, sku, ftz_location)`, emits `scm.erp.ftz-inventory.admitted.v1`. | 119 | **Production-ready**, missing `REMOVE_FROM_FTZ` action (audit TR-7) |
| `trade/events.py` | Topic constants + 5 `build_*_payload()` factories with the same envelope shape as `o2c/order_flow.py`. | 164 | **Production-ready** |
| `trade/README.md` | Brief. | — | n/a |

The v1 API surface (in `services/erp/src/api/trade.py`):
- `POST /api/v1/erp/trade/hts-schedule` — load a new HTS version
- `POST /api/v1/erp/trade/hts/lookup` — compute duty for a single HTS code
- `POST /api/v1/erp/trade/customs-entries` — create a draft entry
- `POST /api/v1/erp/trade/customs-entries/{id}/file` — file with broker
- `POST /api/v1/erp/trade/ftz-inventory` — admit FTZ inventory

Every operation writes a domain row + an outbox row in the same
transaction (the same pattern as `o2c/order_flow.py`).

### What to adopt vs change

| # | Decision | Priority | Wave |
|---|----------|----------|------|
| 1 | **Adopt the v1 trade models VERBATIM** — every table column, every enum, every UNIQUE constraint. `services/erp/src/trade/models.py` is a fresh port of `cautious-goggles/services/erp/src/trade/models.py` with no schema changes. | P0 | W1 (initial port) / W9 (polish) |
| 2 | **Adopt `trade/screening.py` wholesale** including the seed denied-party entries, the `assess_risk()` rule (1 match → BLOCKED high; 2+ → BLOCKED critical; high-risk-country only → REVIEW medium). The risk ladder is opinionated; keep it. | P0 | W1 |
| 3 | **Adopt `trade/hts_engine.py` wholesale** including the `DRAFT/ACTIVE/SUPERSEDED` lifecycle, the `superseded_by_id` FK, the `country_of_origin` specificity (try country-specific entry first, fall back to country-agnostic, fall back to 4-digit prefix). | P0 | W1 (initial port) / W9 (auto-resolve on every SO line — TR-1, TR-2) |
| 4 | **Adopt `trade/customs.py` and `trade/ftz.py` wholesale.** | P0 | W1 |
| 5 | **Adopt `trade/events.py` topic constants verbatim** — `scm.erp.party-created.v1`, `scm.erp.party-screened.v1`, `scm.erp.hts_schedule.updated.v1`, `scm.erp.customs-entry-filed.v1`, `scm.erp.ftz-inventory.admitted.v1`. | P0 | W1 |
| 6 | **W1 ports the v1 API to `/api/v2/erp/trade/...` with the same Pydantic schemas but multi-line-friendly.** v1's `api/trade.py` is a 1:1 port. | P0 | W1 |
| 7 | **W9 polish (TR-3..9 audit items):** add `REMOVE_FROM_FTZ`; auto-resolve HTS on every SO line; re-screen on every Party field change (not just `name`); extend `CustomsEntry.status` to `pending_ftz`/`released`; roll FTZ duty-deferred into GL; publish `CUSTOMS_FILED` to a downstream Compliance consumer. | P1 | W9 |
| 6 | **Change: v1's `SEED_DENIED_ENTRIES` lists 4 entries (PetroIran, Guangdong, Nuclear Research, Defense Procurement Holdings)** — these are demonstration seeds, not real OFAC SDN data. W1 should keep the seeds (so the test suite still works) and W6 should add a real OFAC SDN ingest job. | P1 | W1 (seeds) / W6 (real ingest) |
| 9 | **Change: v1's screening only fires for `customer|vendor|carrier` Party types** (regex in `api/parties.py:29`). v2 W3 (master-data) extends Party to `employee|internal_org` and re-screens those too. | P1 | W3 |
| 10 | **Do not adopt v1's hardcoded `DEFAULT_HTS_ENTRIES`** as the production seed — those are placeholder rows. W1 should ship an `ensure_default_hts_schedule()` with the same 5 entries for dev/test, but mark them with a "demo" tag so production clearly knows they're stubs. | P1 | W1 |
| 11 | **Adopt v1's `TariffAlertSubscription` table** and the `_notify_tariff_subscribers()` loop in `load_hts_schedule()`. When a new HTS version supersedes an old one, any active subscription on a matching prefix is notified (in v1, just counts; in v2, emits an email/in-app event). | P2 | W7 (NotificationService lands) |
| 12 | **Do not re-implement the v1 trade logic in v2.** The `services/erp/src/trade/` directory in v2 W0 is *empty*. W1 fills it with a port of the v1 module, no functional change. | P0 | W1 |

### v2's W1 plan, as informed by the above

- W1 ships `services/erp/src/trade/{models,screening,hts_engine,customs,ftz,events,api}.py` as a near-verbatim port of v1, plus:
  - W1 migration `0203_trade.py` adding the 8 trade tables + 3 enums with
    RLS.
  - W1 adds `hts_engine` auto-resolve on every SO line (closes TR-1, TR-2).
  - W1 adds `duty_amount` column on `sales_order_lines` (closes TR-2).
  - W1 migration `0204_trade_duty_amount.py` adds the column.

---

## h) Consume-side patterns (services/erp/src/consumers/)

### How v1 does it

v1's "consumers" are stubs. `consumers/register.py` is 11 lines that wires
two in-process handlers:

```python
def register_stub_consumers() -> None:
    subscribe(TOPIC_ORDER_CONFIRMED, wms_stub.handle_order_confirmed)
    subscribe(TOPIC_ORDER_CONFIRMED, tms_stub.handle_order_confirmed)
```

Both stubs append a synthetic event to a module-level list:

- `wms_stub.handle_order_confirmed(topic, payload, headers)` builds a
  `TOPIC_PICK_CONFIRMED = "scm.wms.pick-confirmed.v1"` event with the
  same envelope shape, appends to `_pick_logs`, returns. It does **not**
  update any DB. It also stamps the WMS producer as
  `"wms@0.1.0-stub"`.
- `tms_stub.handle_order_confirmed(topic, payload, headers)` builds a
  `TOPIC_SHIP_CONFIRMED = "scm.tms.ship-confirmed.v1"` event, appends
  to `_shipment_logs`, returns. Producer `"tms@0.1.0-stub"`.

The contract both stubs honour:
- Input topic: `scm.erp.order-confirmed.v1`
- Input payload shape: `event_id, event_type, tenant_id, occurred_at,
  producer, trace_id, order_id, customer_token, lines[].line_id,
  lines[].sku, lines[].quantity, lines[].unit, lines[].unit_price`
- Output envelope: same six fields
- Output domain fields: pick-specific / ship-specific (different
  per stub)

**There is no real Kafka consumer in v1.** No `aiokafka` `AIOKafkaConsumer`,
no `confluent_kafka` `Consumer`, no consumer group offset tracking, no
`processed_events` dedup. The whole consumption story is "the same
process that produced the event also runs the in-process handler." The
shared-kernel has the dedup tooling ready (`IdempotentConsumer`,
`SqlAlchemyProcessedEventStore`) but no v1 service actually uses it.

### What to adopt vs change

| # | Decision | Priority | Wave |
|---|----------|----------|------|
| 1 | **Adopt the consumer-group naming** `wms-o2c` and `tms-o2c` (W1), `qms-holdgate` (W2). Encode in `o2c/consumer_groups.py`. | P0 | W1 (WMS/TMS) / W2 (QMS) |
| 2 | **Adopt the v1 `IdempotentConsumer` + `SqlAlchemyProcessedEventStore` pattern** for WMS/TMS/QMS consumers. The `processed_events` table lives in the *consuming* service's DB, not the producing service's. | P0 | W1 |
| 3 | **Adopt the v1 contract: WMS stub consumes `scm.erp.order-confirmed.v1`, emits `scm.wms.pick-confirmed.v1` and `scm.wms.pack-completed.v1`**. v2's W1 WMS consumer (running in the WMS service, not the ERP service) reads the same payload and writes the same event. | P0 | W1 |
| 4 | **Adopt the v1 contract: TMS stub consumes `scm.erp.order-confirmed.v1`, emits `scm.tms.ship-confirmed.v1`**. | P0 | W1 |
| 5 | **Adopt the v1 QMS hold-gate topic `scm.qms.hold-blocks-updated.v1`** with payload shape `{tenant_id, action: add|remove, blocks: [{hold_id, sku, lot_id}]}`. | P1 | W2 |
| 6 | **Adopt the v1 `processed_events` table** in v2 (in every consuming service): `(tenant_id, event_id, consumer_group)` composite PK + `processed_at`. | P0 | W1 |
| 7 | **Change: do not ship the in-process stubs in v2 W0.** v2 has no WMS/TMS/QMS service yet, so the consumer is genuinely a stub. v2 W1's stubs should be in `tests/fixtures/wms_stub.py` etc., not in `src/consumers/`. They exercise the consumer's contract; the real WMS service is a separate repo. | P0 | W1 |
| 8 | **Change: real consumers in v2 W1 are `aiokafka` or `confluent-kafka` driven**, started in `app.state.kafka_consumer_tasks` in the FastAPI lifespan, with a per-topic consumer group and the `IdempotentConsumer` wrapper. | P1 | W1 |
| 9 | **Adopt v1's `test_o2c_happy_path.py` consumer assertion pattern** (assert both `get_pick_logs()` and `get_shipment_logs()` fired) as the *integration test shape* for v2 W1. v2's test would assert on the actual `processed_events` row count + the published events. | P0 | W1 |

### v2's W1 plan, as informed by the above

- W1 ships `services/erp/src/o2c/consumer.py:IdempotentConsumer` (port of
  v1's).
- W1 ships `services/erp/src/o2c/consumer_groups.py:consumer_group(name,
  saga) -> str`.
- W1 ships `tests/integration/test_wms_consumer.py` asserting that an
  ERP `ORDER_CONFIRMED` event is consumed by the WMS handler and a
  `pick-confirmed` event is published (with the `processed_events` row
  written).

---

## i) Tests (services/erp/tests/)

### How v1 does it

v1's test suite is **tiny but tight**: 5 files, ~250 lines.

| File | What it tests |
|------|---------------|
| `conftest.py` | `engine` (SQLite in-memory), `session_factory`, `app`, `client` (httpx ASGITransport), `producer` (InProcessProducer that re-dispatches to the in-process bus), `tenant_id`, `customer_id` fixtures. Key insight: `InProcessProducer` bridges the outbox poller's `Producer` Protocol back onto the in-process bus, so one test exercises both the poller and the stub consumers. |
| `test_health.py` | Trivial `GET /api/v1/erp/health` returns 200. |
| `test_erp_template.py` | Trivial smoke test. |
| `test_o2c_happy_path.py` | **The reference test.** 56 lines. Single test: `POST /api/v1/erp/orders` → assert 201, `outbox_events.published_at IS NULL`, `await poll_once(...) == 1`, refresh, `published_at IS NOT NULL`. Then assert `get_published_events()[0]` has the right topic + `order_id`. Then assert `get_pick_logs()[0]` and `get_shipment_logs()[0]` fired. Then replay the same POST and assert 201 with the same `order_id` (idempotency). |
| `test_trade_compliance.py` | Tests HTS lookup + customs entry + screening. |
| `test_platform_connectors.py` | Tests the connector stub. |

**No `pytest.ini`** in the v1 ERP service — the project root
`pytest.ini` picks up everything. No `conftest.py` in subfolders.

**No test markers** — tests are auto-collected. The test runner
discovers `tests/test_*.py` and `tests/**/test_*.py`.

**No testcontainers** — the v1 suite uses SQLite in-memory via
`sqlite+aiosqlite:///:memory:`. Postgres-specific features (RLS, JSONB,
`FOR UPDATE SKIP LOCKED`) are *not* tested. The `outbox_poller.py`
fallback to a plain `SELECT` is what makes this work.

### What to adopt vs change

| # | Decision | Priority | Wave |
|---|----------|----------|------|
| 1 | **Adopt the `InProcessProducer` test double that bridges outbox → in-process bus** verbatim. v2 W1's `tests/conftest.py` should have a `producer` fixture with the same re-dispatch behaviour. | P0 | W1 |
| 2 | **Adopt the `test_o2c_happy_path.py` shape** as the W1 reference test. The same four asserts (201, outbox row, `poll_once` count, stub consumers fired) + idempotency replay. | P0 | W1 |
| 3 | **Adopt v1's `client` fixture pattern** (httpx `ASGITransport` + `AsyncClient`). v2 W0 already has this in `tests/conftest.py:client`. | P0 | W0 (done) |
| 4 | **Adopt v1's `tenant_id` / `customer_id` fixture pattern** (random UUID per test). | P0 | W1 |
| 5 | **Change: v2 W0 already uses `pytest_asyncio` and per-test schema isolation via `testcontainers[postgres]`** (see `tests/conftest.py:pg_session_factory`). This is **better than v1's SQLite-in-memory** because it tests RLS, JSONB, and `FOR UPDATE SKIP LOCKED` for real. Keep it. | P0 | W0 (done) |
| 6 | **Change: add `pytest` markers** for `-m unit` / `-m integration` per the v2 SPEC §2 "Tests". v1 has no markers; v2 W0 already declares markers in `pyproject.toml` per the WAVE_0_FOUNDATIONS.md. | P0 | W0 (done) |
| 7 | **Change: ship a `tests/contract/` directory** with pact-style tests for WMS/TMS/QMS/MRP contracts. v1 has none. v2 W1 starts with WMS + TMS; W2 adds QMS + MRP. | P1 | W1 (WMS/TMS) / W2 (QMS/MRP) / W10 (harden) |
| 8 | **Adopt v1's `test_trade_compliance.py` as the W1 trade test reference.** v2 W1 should have `tests/integration/test_trade_compliance.py` covering the same scenarios (HTS lookup, customs entry draft → file, FTZ admit, screening). | P0 | W1 |
| 9 | **Do not adopt v1's reliance on SQLite for unit tests** — v2 W0's `pg_session_factory` already uses real Postgres with a per-test schema. This is strictly better. | P0 | W0 (done) |

---

## j) Observability (shared-kernel/tracing + v2 observability/)

### How v1 does it

v1 has minimal OTel, in `packages/shared-kernel/tracing/setup.py`:

- `setup_tracing(service_name, otlp_endpoint)` — idempotent
  `TracerProvider` init with `service.name`, `service.namespace="scm"`,
  `deployment.environment` (from `DEPLOYMENT_ENV` env).
- `BatchSpanProcessor(OTLPSpanExporter(endpoint, insecure=True))` —
  standard OTLP gRPC exporter.
- If the OTLP endpoint is unreachable, the import is wrapped in
  `try/except` and a warning logged: "spans local only."
- `instrument_fastapi(app, service_name)` — wraps FastAPI with
  `opentelemetry-instrumentation-fastapi` if installed (silent skip
  otherwise).
- `shutdown_tracing()` for clean teardown.

**No Prometheus metrics anywhere in v1.** No structured logging beyond
stdlib. No `trace_id` propagation into log records (audit OPS-3 P1).
No per-tenant metric labels. No outbox publish latency histogram
(audit OPS-6, S-18).

The only v1 logging config is `logging.basicConfig(level=INFO)` in
`outbox_poller.py:256`. v1 has no `structlog` setup.

### What v2 already has (W0)

`services/erp/src/observability/` is a strict superset:

- `tracing.py:init_tracing()` — same `TracerProvider` + OTLP gRPC
  exporter shape; adds `service.version` and `deployment.environment`
  resource attributes.
- `metrics.py:Metrics` — Prometheus `Counter`/`Histogram` registry with
  the seven metrics the v2 SPEC requires (see section (a) for the
  outbox ones).
- `logging.py:init_logging()` — `structlog` + stdlib logging as JSON,
  with `trace_id`, `span_id`, `tenant_id`, `request_id` contextvars
  injected.
- `middleware.py:ObservabilityMiddleware` — request_id, OTel span,
  per-tenant + per-route metrics, log context wiring. Does what v1's
  `TenantContextMiddleware` + `instrument_fastapi` do, in one pass.

### What to adopt vs change

| # | Decision | Priority | Wave |
|---|----------|----------|------|
| 1 | **Adopt v1's OTel resource attribute shape** `service.name`, `service.namespace="scm"`, `deployment.environment`. v2 W0 already has `service.name` and `service.version`; add `service.namespace` for symmetry. | P1 | W1 |
| 2 | **Adopt v1's silent fallback** when the OTLP endpoint is unreachable (try/except, log "spans local only"). v2 W0's `init_tracing` already gracefully handles the no-endpoint case. | P0 | W0 (done) |
| 3 | **Adopt v1's `shutdown_tracing()` pattern** for the FastAPI lifespan. v2 W0's `lifespan` doesn't call it — W1 should. | P1 | W1 |
| 4 | **Adopt v1's `instrument_fastapi` pattern** but call it from the lifespan, not the factory. v1's `instrument_fastapi(app, service_name)` is fine but binds the auto-instrumentor at import time, which complicates tests. v2 W1 should call `FastAPIInstrumentor.instrument_app(app)` inside `lifespan` after `init_tracing`. | P1 | W1 |
| 5 | **Do not adopt v1's silent `ImportError` skip** for `opentelemetry-instrumentation-fastapi` — make it a hard dep so missing it is caught at boot. | P0 | W1 |
| 6 | **Keep v2 W0's Prometheus metrics** as the canonical metric names. v1 has none to copy. The v0 metrics (`http_requests_total`, `http_request_latency_seconds`, `outbox_events_published_total`, `outbox_publish_latency_seconds`, `idempotency_hits_total`, `permission_denials_total`, `rls_blocks_total`) are exactly what the SPEC requires. | P0 | W0 (done) |
| 7 | **Per-tenant metrics (OPS-5)** — v1 doesn't have them. v2 W0 declares `tenant` as a label on `http_requests_total` and `http_request_latency_seconds`. Confirm Prometheus cardinality stays bounded (a tenant_id is unbounded in theory — see W10). | P2 | W10 (cardinality guard) |
| 8 | **Do not adopt v1's stdlib-only logging.** v2 W0's `structlog` is the right choice. | P0 | W0 (done) |

### v2's W1 plan

- W1 wraps each module's saga step in an OTel span (`saga.step`,
  `outbox.poll`, `outbox.publish`, `consumer.handle`).
- W1 calls `FastAPIInstrumentor.instrument_app(app)` in `lifespan`.
- W1 ships `scripts/dev/jaeger.sh` for local trace inspection (parity
  with v1's local dev story).

---

## k) OpenAPI / SDK (v1: none; v2: scripts/dump_openapi.py)

### How v1 does it

**v1 has no OpenAPI export and no SDK generator.**

- `packages/shared-clients/README.md` is a stub.
- `packages/shared-events/scripts/` has Avro schema sync
  (`registry-sync.ts`) but no client-gen.
- The v1 frontend is wired directly to v1 backend routes via
  hand-written TypeScript in `frontend/src/lib/api/`.

### What v2 already has (W0)

- `services/erp/scripts/dump_openapi.py` — writes
  `docs/openapi/erp.json` from the running app. Wired in CI per the
  WAVE_0_FOUNDATIONS.md.
- `docs/openapi/README.md` — placeholder for the schema-drift check.
- `docs/openapi/.gitignore` — `erp.json` is generated, not committed.

### What to adopt vs change

| # | Decision | Priority | Wave |
|---|----------|----------|------|
| 1 | **Keep v2 W0's `dump_openapi.py` as the source of truth** for the OpenAPI spec. W1 runs it in CI and compares against the committed `erp.json` (when it lands). | P0 | W0 (done) |
| 2 | **Adopt v1's `registry-sync.ts` Apicurio sync script** as the basis for v2's W10 schema-registry CI gate. The `compatibility.json` shape is fine; the script can be ported to Python if Node isn't in the W10 toolchain. | P2 | W10 |
| 3 | **Do not ship a hand-rolled TS SDK in W1.** Generate from the OpenAPI spec in W6 with `openapi-typescript-codegen` or `openapi-generator-cli`. The hand-rolled `frontend/src/lib/api/partiesApi.ts` style is what the spec will replace. | P1 | W6 |
| 4 | **W1 plan: keep `dump_openapi.py` running on every CI build** with a schema-drift check (commit the file; CI fails if the diff is non-empty and unrelated to a labelled wave). | P0 | W1 |
| 5 | **W1 plan: add `info.title`, `info.version`, `info.description` matching the v2 SPEC** to the FastAPI app so the generated `erp.json` is well-formed. v2 W0 already has `title="ERP Service v2"`, `version="0.1.0"`. | P0 | W0 (done) |
| 6 | **W1 plan: ensure every mutating route has a `tag`, `summary`, `description`, `response_model`, and at least one `example` per the v2 SPEC §2 "OpenAPI".** The OpenAPI validator script in W1 (`scripts/validate_openapi.py`) enforces this. | P0 | W1 |

### v2's W1 plan

- W1 ships `services/erp/scripts/validate_openapi.py` — runs
  `dump_openapi.py`, parses the result, and asserts every mutating
  route has a `response_model` and a `tag`.
- W1 wires this as `erp-v2-validate-openapi` in CI.
- W6 generates the TS SDK.

---

## l) Frontend integration (frontend/src/app/dashboard/*)

### How v1 does it

The v1 frontend lives in `frontend/src/app/dashboard/`. Routes are
file-system based (Next.js App Router). Each page calls the v1 backend
at `:8010` (the legacy monolith) via `frontend/src/lib/api/*` (hand-written
`partiesApi`, `salesOrdersApi`, etc.).

The relevant v1 dashboard routes (from `frontend/src/app/dashboard/`)
that v2 needs to support:

| Route | v1 backend (`:8010`) | v1 API client |
|-------|----------------------|---------------|
| `/dashboard/customers` | `GET/POST/PATCH /api/v1/parties?party_type=customer` | `partiesApi.list/create/update` |
| `/dashboard/crm/leads` | `GET/POST /api/v1/crm/leads` | (hand-rolled) |
| `/dashboard/crm/leads/[id]` | `GET/PATCH /api/v1/crm/leads/{id}` | |
| `/dashboard/crm/opportunities` | `GET/POST /api/v1/crm/opportunities` | |
| `/dashboard/crm/opportunities/[id]` | `GET/PATCH /api/v1/crm/opportunities/{id}` | |
| `/dashboard/crm/pipeline` | `GET /api/v1/crm/pipelines` | |
| `/dashboard/crm/quotes` | `GET/POST /api/v1/crm/quotes` | |
| `/dashboard/crm/quotes/[id]` | `GET/PATCH /api/v1/crm/quotes/{id}` | |
| `/dashboard/crm/account?account=…` | `GET /api/v1/parties/{id}/360` (Party 360°) | |
| `/dashboard/sales-orders` | `GET/POST /api/v1/erp/orders` | `salesOrdersApi.list/create` |
| `/dashboard/sales-orders/[id]` | `GET/PATCH /api/v1/erp/orders/{id}` | |
| `/dashboard/purchase-orders` | `GET/POST /api/v1/p2p/purchase-orders` | |
| `/dashboard/suppliers` | `GET/POST /api/v1/parties?party_type=vendor` | |
| `/dashboard/inventory` | `GET /api/v1/wms/inventory` (WMS service) | |
| `/dashboard/parts` | `GET /api/v1/erp/products` | |
| `/dashboard/production-orders` | `GET/POST /api/v1/mrp/production-orders` (MRP service) | |
| `/dashboard/work-centers` | `GET/POST /api/v1/mrp/work-centers` (MRP service) | |
| `/dashboard/quality` | `GET /api/v1/qms/inspections` (QMS service) | |
| `/dashboard/finance/accounts` | `GET/POST /api/v1/finance/gl-accounts` | |
| `/dashboard/finance/journal` | `GET/POST /api/v1/finance/journal-entries` | |
| `/dashboard/finance/ar` | `GET/POST /api/v1/finance/ar-invoices` | |
| `/dashboard/finance/ap` | `GET/POST /api/v1/finance/ap-invoices` | |
| `/dashboard/finance/aging` | `GET /api/v1/finance/aging` | |
| `/dashboard/finance/aging/history` | (history of aging snapshots) | |
| `/dashboard/finance/budgets` | `GET/POST /api/v1/finance/budgets` | |
| `/dashboard/finance/webhooks` | `GET/POST /api/v1/finance/webhooks` | |
| `/dashboard/settings/roles` | `GET/POST /api/v1/identity/roles` | |

**Every page currently hits the v1 monolith at `:8010` directly** via
`fetch()` / `useResource()` patterns. The frontend doesn't know about
v2 yet. v2 is reachable at `:8001` (the v2 ERP service) but no route
is proxied to it.

The v1 frontend's `lib/api/partiesApi.ts` exposes:
- `partiesApi.list({party_type})` → `GET /api/v1/parties?party_type=…`
- `partiesApi.create(data)` → `POST /api/v1/parties`
- `partiesApi.update(id, data)` → `PATCH /api/v1/parties/{id}`
- `partiesApi.customerStats()` → `GET /api/v1/parties/stats?type=customer`

### Frontend route → v2 endpoint → v1 endpoint mapping

| Frontend route | v2 endpoint (W1+) | v1 endpoint |
|----------------|-------------------|-------------|
| `/dashboard/customers` | `GET/POST /api/v1/erp/parties?party_type=customer` (W3) | `GET/POST /api/v1/parties?party_type=customer` |
| `/dashboard/sales-orders` | `GET/POST /api/v2/erp/orders` (W1) | `GET/POST /api/v1/erp/orders` |
| `/dashboard/sales-orders/[id]` | `GET/PATCH /api/v2/erp/orders/{id}` (W1) | `GET/PATCH /api/v1/erp/orders/{id}` |
| `/dashboard/crm/leads` | `GET/POST /api/v2/erp/crm/leads` (W5) | `GET/POST /api/v1/crm/leads` |
| `/dashboard/crm/opportunities` | `GET/POST /api/v2/erp/crm/opportunities` (W5) | `GET/POST /api/v1/crm/opportunities` |
| `/dashboard/crm/pipeline` | `GET /api/v2/erp/crm/pipelines` (W5) | `GET /api/v1/crm/pipelines` |
| `/dashboard/crm/quotes` | `GET/POST /api/v2/erp/crm/quotes` (W5) | `GET/POST /api/v1/crm/quotes` |
| `/dashboard/crm/quotes/[id]` | `GET/PATCH /api/v2/erp/crm/quotes/{id}` (W5) | `GET/PATCH /api/v1/crm/quotes/{id}` |
| `/dashboard/crm/account?account=…` | `GET /api/v2/erp/parties/{id}/360` (W5) | `GET /api/v1/parties/{id}/360` |
| `/dashboard/purchase-orders` | `GET/POST /api/v2/erp/p2p/purchase-orders` (W2) | `GET/POST /api/v1/p2p/purchase-orders` |
| `/dashboard/suppliers` | `GET/POST /api/v2/erp/parties?party_type=vendor` (W3) | `GET/POST /api/v1/parties?party_type=vendor` |
| `/dashboard/inventory` | (WMS service, W3-W4) | `GET /api/v1/wms/inventory` |
| `/dashboard/parts` | `GET/POST /api/v2/erp/products` (W3) | `GET /api/v1/erp/products` |
| `/dashboard/production-orders` | (MRP service, W3) | `GET/POST /api/v1/mrp/production-orders` |
| `/dashboard/work-centers` | (MRP service, W3) | `GET/POST /api/v1/mrp/work-centers` |
| `/dashboard/quality` | (QMS service, W2) | `GET /api/v1/qms/inspections` |
| `/dashboard/finance/accounts` | `GET/POST /api/v2/erp/finance/gl-accounts` (W4) | `GET/POST /api/v1/finance/gl-accounts` |
| `/dashboard/finance/journal` | `GET/POST /api/v2/erp/finance/journal-entries` (W4) | `GET/POST /api/v1/finance/journal-entries` |
| `/dashboard/finance/ar` | `GET/POST /api/v2/erp/finance/ar-invoices` (W1 AR) | `GET/POST /api/v1/finance/ar-invoices` |
| `/dashboard/finance/ap` | `GET/POST /api/v2/erp/finance/ap-invoices` (W2 AP) | `GET/POST /api/v1/finance/ap-invoices` |
| `/dashboard/finance/aging` | `GET /api/v2/erp/finance/aging` (W4) | `GET /api/v1/finance/aging` |
| `/dashboard/finance/budgets` | `GET/POST /api/v2/erp/finance/budgets` (W4) | `GET/POST /api/v1/finance/budgets` |
| `/dashboard/finance/webhooks` | `GET/POST /api/v2/erp/finance/webhooks` (W6) | `GET/POST /api/v1/finance/webhooks` |
| `/dashboard/settings/roles` | `GET/POST /api/v2/erp/identity/roles` (W6) | `GET/POST /api/v1/identity/roles` |

### What to adopt vs change

| # | Decision | Priority | Wave |
|---|----------|----------|------|
| 1 | **Adopt v1's URL shape** `/api/v1/erp/...` for v2 *during the W0-W5 transition*. W6 shifts to `/api/v2/erp/...` when Kong flips default traffic (per the v2 SPEC W6). | P0 | W0 (now) |
| 2 | **Adopt v1's `partiesApi` client interface** (list / create / update / customerStats) as the v2 client. The transport is the only thing that changes. | P0 | W3 (when Party CRUD lands) |
| 3 | **Adopt v1's `useResource()` hook pattern** (list + create + update + label). v2's frontend should generate the TS SDK from the OpenAPI spec (W6), but the hook shape stays the same. | P0 | W1 |
| 4 | **W1 plan: the v1 frontend pages keep working against `:8010` until W6**. v2 W1's frontend integration is "make sure the W0 backend boots and the OpenAPI dump is valid" — nothing more. | P0 | W1 |
| 5 | **W6 plan: Kong shifts default traffic to v2 `:8001` per route** (one route at a time, behind `erp.v2.<module>.enabled` feature flags). The frontend keeps talking to Kong; Kong routes to v2. | P0 | W6 |
| 6 | **Adopt v1's `customer_token` tokenization on the frontend** — the `partiesApi.list()` returns `customer_token` (a string like `cust:<uuid>`) which the UI displays. v2's `/dashboard/customers` page renders the same way. | P0 | W3 |
| 7 | **Do not adopt v1's hand-written TS clients** beyond the v1 surface that's already in `frontend/src/lib/api/`. W6's openapi-typescript-codegen replaces them. | P0 | W6 |

---

## Summary table — adopted / changed / invented

| Area | Adopt from v1 | Change vs v1 | Invent (v2 only) | Wave |
|------|----------------|--------------|------------------|------|
| (a) Outbox | `FOR UPDATE SKIP LOCKED` polling, table schema, idempotency-keyed producer | Add `attempts`/`last_error`/`dlq_at` columns; per-replica sidecar not CLI; traceparent as column; metrics | Prometheus DLQ alerting | W1 |
| (b) Event payload | Flat-blend envelope, six field names, `customer_token` tokenization, decimal-as-string, Apicurio compatibility, `EventEnvelope` dataclass | Producer `erp-v2@0.1.0`; `schema_version` field P2; ISO 8601 JSON for `occurred_at` (Avro stays epoch-millis) | — | W0 (docs) / W1 |
| (c) In-process bus | `subscribe`/`publish` API, 3-tuple, auto-fallback, consumer-group naming, in-process test double | Class-based state, `confluent-kafka` only, Apicurio validator at produce-time | `consumer_group("wms", "o2c")` helper | W1 |
| (d) Idempotency | Header name, `Idempotency-Key`, body-mismatch 409, `UNIQUE (tenant_id, key_hash)` constraint, PATCH/PUT/DELETE coverage (already broader than v1) | Tenant-prefix the key hash (one-line fix in W0) | `response_headers` column, `delete_expired` job | W0 (fix) / W1 |
| (e) shared-kernel | Protocol/impl split, `processed_events` consumer dedup, Pydantic saga models, JWT-or-header tenant getter, hash chain audit | Do not lift v1's split; keep flat `shared/` until W6 | `extract_tenant_from_jwt` port with `tenantId`/`companyId` aliases | W0-W6 |
| (f) Sagas | `CompensationStrategy`/`SagaStatus`/`StepStatus` enums, `SagaStepDefinition`/`SagaInstance` Pydantic models, step idempotency key format, `HoldGate` + `HoldBlockedError`, `suspend_exceptions` | In-process orchestrator lives in `o2c/sagas/`, not a `flow-sagas` package; concrete handlers call real modules not stubs | Temporal wrapper in W2 | W1 (in-proc) / W2 (Temporal) |
| (g) Trade | All 7 files wholesale — models, screening, hts_engine, customs, ftz, events, api — verbatim port | Add `employee`/`internal_org` screening; `REMOVE_FROM_FTZ`; HTS auto-resolve on SO lines; mark `DEFAULT_HTS_ENTRIES` as demo | `duty_amount` on `sales_order_lines` (W1 mig) | W1 / W3 / W9 |
| (h) Consume-side | Consumer-group naming `wms-o2c`/`tms-o2c`/`qms-holdgate`, `IdempotentConsumer` + `processed_events` dedup, in-process test-double bridge | Real `aiokafka`/`confluent-kafka` consumer in lifespan, no in-proc stubs in `src/` | `consumer_group()` helper | W1 |
| (i) Tests | `InProcessProducer` test double, `test_o2c_happy_path.py` shape, `client`/`tenant_id`/`customer_id` fixtures | Markers `-m unit`/`-m integration` (already in W0), testcontainers Postgres (already in W0), `tests/contract/` for WMS/TMS/QMS | Trade test reference | W0 (done) / W1 |
| (j) Observability | OTel resource attributes, silent fallback, `shutdown_tracing` pattern, `instrument_fastapi` shape | Call `FastAPIInstrumentor.instrument_app` in lifespan, not factory; hard-dep the auto-instrumentor | `service.namespace` resource attribute | W1 |
| (k) OpenAPI/SDK | `registry-sync.ts` for the W10 Apicurio CI gate (port) | — | W6 TS SDK via openapi-typescript-codegen | W1 (validator) / W6 (SDK) / W10 (gate) |
| (l) Frontend | URL shape `/api/v1/erp/...`, `partiesApi` interface, `useResource()` hook, `customer_token` tokenization | — | Kong per-route feature-flag flip in W6 | W0 (now) / W6 |

---

## Adoption plan (next 2 waves)

### W0 (now → before W1 starts) — adopt-now

See `ADOPT_NOW.md` for the prioritised punch list. The top items:

1. **One-line fix in `shared/idempotency.py`:** tenant-prefix the key hash
   (`sha256(f"{tenant_id}:{key}")` not `sha256(key)`).
2. **Adopt the v1 envelope shape and topic constants in code comments** so
   the W1 implementation is mechanical.
3. **Adopt the v1 `idempotency_keys` DDL** as the W1 migration target.

### W1 — adopt in this wave

Everything in sections (a), (b), (c), (f) in-process, (g) trade port, (h)
consumers, (i) tests, (k) OpenAPI validator, (l) URL shape.
