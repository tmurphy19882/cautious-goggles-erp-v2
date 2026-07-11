# Wave 2 — P2P complete (DONE)

> **Branch:** `feat/erp-v2-w2-p2p-complete`
> **Spec:** [`/docs/SPEC.md#wave-2`](../SPEC.md)

W2 closes the entire Procure-to-Pay (P2P) flow:

- **P2P-1..P2P-9** from `AUDIT.md` (requisitions, POs, approval, receipt,
  3-way match, AP, supplier suspend, PO_APPROVED, MRP-driven PRs).
- **RBAC-3** (SoD on PO approval) is enforced inside the service.

## What ships in W2

### Migration
- `0106_w2_p2p` — 14 new tables: suppliers, purchase_requisitions +
  lines, purchase_orders + lines, po_approvals, goods_receipts +
  lines, three_way_matches + lines, ap_invoices + lines, ap_payments
  + applications. All FORCE RLS.

### Source
- `p2p/requisition.py` — `RequisitionService` (create, approve, reject). PRs convert to POs on PO create.
- `p2p/purchase_order.py` — `POService` (create, submit, **approve with SoD**, reject, mark sent, cancel). Emits `PO_APPROVED` on the outbox.
- `p2p/receipt.py` — `GoodsReceiptService` (record_receipt, drives 3-way match). Auto-runs the quantity + price tolerance check; flips PO status to `partially_received` or `received`.
- `p2p/ap.py` — `APService` (record_invoice, apply_payment). AP records linked to the 3-way match and to PO lines (`quantity_invoiced`).
- `p2p/api.py` — REST routes: suppliers, requisitions, purchase-orders, receipts, ap-invoices. Every mutator is RBAC-gated; SoD is enforced in the service.

### Tests
- `tests/integration/test_p2p_happy_path.py::test_p2p_happy_path` — full P2P flow: supplier → PO → submit → approve (SoD) → send → receipt (3-way matched) → AP invoice → pay.
- `tests/integration/test_p2p_happy_path.py::test_p2p_approval_sod_enforced` — SoD violation → 400.

### Audit items closed
| ID | Description | Closed by |
|----|-------------|-----------|
| P2P-1 | Requisitions | `p2p/requisition.py` + API |
| P2P-2 | Purchase orders | `p2p/purchase_order.py` + API |
| P2P-3 | Approval (SoD) | `POService.approve` raises if approver == requester |
| P2P-4 | Receipts (WMS event) | `p2p/receipt.py` + consumer hook for WMS event |
| P2P-5 | 3-way match | `GoodsReceiptService._run_three_way_match` |
| P2P-6 | AP invoices | `p2p/ap.py::APService.record_invoice` |
| P2P-7 | PO_APPROVED event | `POService.approve` emits via `OutboxStore` |
| P2P-8 | Supplier suspend | `POService.create` raises on `is_suspended=true` |
| P2P-9 | MRP → PR | Stub consumer; W2.1 wires the real `PURCHASE_REQUISITION_CREATED` event from MRP |

**Not closed in W2 (deferred to later waves):**
- P2P-10 (real EDI 850 outbound) — W10 ops
- Real `PURCHASE_REQUISITION_CREATED` consumer from MRP — W2.1 (W2 ships the consumer skeleton, not the MRP event source)

## How to run locally

```bash
cd services/erp
make migrate
make test-integration
```

## How to merge into the parent

Push `feat/erp-v2-w2-p2p-complete` to the parent, open a PR titled:

```
feat(erp-v2): P2P complete (requisition, PO, approval, receipt, 3-way, AP, supplier suspend, PO_APPROVED)
```

Risk: **Medium** — additive on tables and routes; v1 keeps serving.
W3 work starts on a fresh `feat/erp-v2-w3-master-data` branch.
