# Wave 3 + Wave 4 — Master Data (full) + Finance/GL (DONE)

> **Branch:** `feat/erp-v2-w3-master-data`
> **Spec:** [`/docs/SPEC.md#wave-3`](../SPEC.md), [`/docs/SPEC.md#wave-4`](../SPEC.md)

W3 ships the full party model + search; W4 ships GL.

## What ships

### Migrations
- `0107_w3_master_data` — 10 new tables: parties, party_contacts, party_addresses, party_tax_ids, search_index, import_jobs, gl_accounts, accounting_periods, journal_entries, journal_lines. The `journal_lines` table has a CHECK constraint enforcing one-sided lines (debit OR credit, never both).

### Source
- `master_data/party.py` — `PartyService` (create party with kind, add contact / address / tax id; writes to `search_index` inline).
- `master_data/search.py` — `SearchService.query` (trigram-fuzzy search via the `%` operator and `similarity()`).
- `master_data/api.py` — extended with `POST /parties`, `GET /parties`, `POST /parties/{id}/contacts`, `.../addresses`, `.../tax-ids`, `GET /search`.
- `finance/gl.py` — `GLService` (create_account, open_period, close_period, post_journal) + auto-post helpers (`post_sales_invoice`, `post_ar_payment`, `post_ap_invoice`, `post_ap_payment`) used by the O2C / P2P flows.
- `finance/api.py` — REST routes for GL accounts, periods, journal entries.

### Tests
- `tests/integration/test_w3_w4_happy_path.py::test_w3_party_create_and_search` — create party + contact + address + tax id + search.
- `tests/integration/test_w3_w4_happy_path.py::test_w4_post_journal_and_close_period` — GL accounts + balanced journal + unbalanced rejection + period close.

### Audit items closed

| ID | Description | Closed by |
|----|-------------|-----------|
| FIN-1 | Chart of accounts | `gl_accounts` table + `POST /finance/gl/accounts` |
| FIN-2 | Manual journal entries | `journal_entries` + `journal_lines` + `post_journal` (CHECK constraint enforces one-sided lines) |
| FIN-3 | Auto-post from SO/PO/AP/payment | `finance.gl.post_sales_invoice`, `post_ar_payment`, `post_ap_invoice`, `post_ap_payment` (helpers called from W1/W2 flows) |
| FIN-4 | Period close | `close_period` API + `accounting_periods` table |
| FIN-7 | Tax engine | Shared with O2C via `o2c.tax_fx` |
| MD-1..MD-10 | Master data | `parties` + `party_contacts` + `party_addresses` + `party_tax_ids` + `search_index` + `import_jobs` |

**Not closed (deferred to later waves):**
- FIN-5/6 (AR/AP aging views) — W4.1
- FIN-8 (FX revaluation) — W4.1
- FIN-9/10 (bank reconciliation, fixed assets) — out of scope for MVP
- FIN-12 (credit/debit memos) — W4.1
- FIN-13 (cost center allocation) — W4.1
- MD-9/10 (bulk import) — import jobs table ships in W3, but the CSV/XLSX parser is W6 (along with the rest of platform)

## How to merge

Push `feat/erp-v2-w3-master-data` to the parent, open a PR titled:

```
feat(erp-v2): master data (W3) + finance/GL (W4)
```

Risk: **Low** — additive only.

W5 (CRM core) starts on a fresh `feat/erp-v2-w5-crm-core` branch.
