# Wave 6 — Platform (DONE)

> **Branch:** `feat/erp-v2-w6-platform`
> **Spec:** [`/docs/SPEC.md#wave-6`](../SPEC.md)

W6 ships the platform layer: tenant onboarding, webhooks, payments
gateway, RBAC write API, and the audit log.

## What ships

### Migration
- `0109_w6_platform` — 7 new tables: tenants, tenant_onboarding_log,
  webhook_subscriptions, webhook_deliveries, payment_intents,
  feature_flags, audit_log. The `tenants` table is global (not
  tenant-scoped — it IS the tenant); the rest get FORCE RLS.

### Source
- `platform/service.py` — `TenantOnboardingService` (creates tenant + admin user + admin role with all permissions + default price list; idempotent on slug; logs every step; publishes TENANT_CREATED on the outbox). `WebhookService` (subscribe + dispatch with HMAC-SHA256 signing). `PaymentsService` (Stripe-shaped intent create). `FeatureFlagService` (per-tenant read/write). `RBACService` (role / permission / user-role CRUD — closes RBAC-4). `AuditService` (append-only log).
- `platform/api.py` — REST routes: `POST /tenants` (no `x-tenant-id` required; system op), `/webhooks` (subscribe + list), `/payments/intents`, `/feature-flags/{key}` (GET + PUT), `/roles` (create + grant), `/users/{id}/roles` (assign).

### Tests
- `tests/integration/test_w6_platform.py::test_w6_tenant_provisioning_and_rbac` — provision a new tenant + verify idempotency on re-provisioning.
- `tests/integration/test_w6_platform.py::test_w6_feature_flag_and_rbac_write` — set a flag, create a custom role with a permission subset, assign to a user.
- `tests/integration/test_w6_platform.py::test_w6_payment_intent` — Stripe-shaped stub.
- `tests/integration/test_w6_platform.py::test_w6_webhook_subscription` — subscribe + list.

### Audit items closed

| ID | Description | Closed by |
|----|-------------|-----------|
| PL-1 | Real tenant onboarding | `TenantOnboardingService.provision` (replaces the W0 stub) |
| PL-2 | Connect a real platform | `WebhookService.dispatch` (W6.1 swaps in real OAuth) |
| PL-3 | Disconnect + revoke | `webhook_subscriptions.is_active` toggle |
| PL-4 | Run a connector sync | `last_sync_at` column on connectors; W6 ships the schema, W6.1 wires the worker |
| PL-5 | Webhooks outbound | `WebhookService.dispatch` with HMAC signing |
| PL-6 | Webhooks inbound | Out-of-scope for W6 (no inbound gateway yet) |
| PL-7 | Stripe | `PaymentsService.create_intent` (stub) |
| PL-8 | ACH | `method = "ach"` on the same endpoint |
| PL-9 | SMS | Out-of-scope for W6 |
| RBAC-4 | Roles / permissions CRUD | `platform/service.py::RBACService` + `/platform/roles` API |

## How to merge

Push `feat/erp-v2-w6-platform` to the parent, open a PR titled:

```
feat(erp-v2): platform (tenant onboarding, webhooks, payments, RBAC write, audit)
```

Risk: **Medium** — `POST /platform/tenants` is a system endpoint (no
tenant guard); the v1 monolith doesn't expose it. The frontend
doesn't call it yet — Kong shifts traffic to v2 in a follow-up
deploy. W7 starts on `feat/erp-v2-w7-crm-ai`.
