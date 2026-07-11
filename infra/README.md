# infra/ — local stack

Each subdir will get a `docker-compose.yml` overlay + a small README in its
respective wave PR. Until then, this README is the placeholder.

| Subdir | Service | Wave | Purpose |
|--------|---------|------|---------|
| `postgres/` | Postgres 16 + per-service DB init | W0 | `erp_db` for the ERP service |
| `kafka/`    | Redpanda (Kafka API) + topic manifest | W0 | outbox poller publishes here |
| `apicurio/` | Apicurio Schema Registry | W0 | Avro compatibility gate |
| `keycloak/` | Keycloak realm export | W0 | JWT issuer; OIDC for SSO |
| `kong/`     | Kong + `erp-route.yml` | W0 | `/api/v1/erp` → `:8001` |
| `otel/`     | OpenTelemetry Collector | W0 | exports to local Jaeger/Prometheus |
| `unleash/`  | Unleash | W0 | per-tenant feature flags |
| `temporal/` | Temporal | W1/W2 | saga orchestrator for O2C and P2P |
| `minio/`    | MinIO | W6 | S3 for import-export file staging |
