# Wave 9 — Trade compliance polish (DONE)

> **Branch:** `feat/erp-v2-w9-trade-polish`
> **Spec:** [`/docs/SPEC.md#wave-9`](../SPEC.md)

W9 closes the trade-compliance audit items the W3 research
flagged as P2.

## What ships

### Migration
- `0112_w9_trade_polish` — 3 new tables: `hts_codes` (tenant-scoped
  cache of the public HTS schedule, GIN index on `keywords` for
  fast lookup), `ftz_entries` (admit / remove / weekly inventory),
  `screening_snapshots` (per-source snapshots, indexed by party +
  time). All FORCE RLS.

### Source
- `trade/service.py` — `HTSResolver` (keyword scoring against
  `hts_codes.keywords`; returns top 5). `FTZService` (admit +
  remove + weekly inventory per zone). `ScreeningService` (re-screen
  a party against the W9 stub OFAC SDN list; W9.1 swaps in the real
  feed). Closes TR-3 (FTZ) and TR-4 (re-screen on update).
- `trade/api.py` — REST routes: `/trade/hts/resolve`,
  `/trade/ftz/entries` + `/remove` + `/weekly-inventory`,
  `/trade/screen` + `/screen/{party_id}/latest`.

### Tests
- `tests/integration/test_w9_trade_polish.py` — seed HTS, resolve
  by description (laptop / LED / power supply), FTZ admit + weekly
  inventory + remove, screening match (Acme Bad Corp LLC hits the
  stub list) + no-match (Totally Innocent Co).

## Audit items closed

| ID | Description | Closed by |
|----|-------------|-----------|
| TR-1 | HTS auto-resolver | `HTSResolver` (naive keyword scoring; W9.1 swaps in a real tariff engine) |
| TR-2 | HTS code table | `hts_codes` + tenant-scoped cache + GIN index |
| TR-3 | FTZ entries | `ftz_entries` + `FTZService` (admit / remove / weekly inventory) |
| TR-4 | Re-screen on party update | `ScreeningService` + `screening_snapshots` (stub SDN; W9.1 swaps real feed) |
| TR-5 | OFAC / BIS / EU feeds | Out of scope for W9 (W9.1 — same `ScreeningService` interface, real provider) |

## How to merge

Push `feat/erp-v2-w9-trade-polish` to the parent, open a PR titled:

```
feat(erp-v2): trade compliance polish (HTS auto-resolve, FTZ, screening)
```

Risk: **Low** — additive. W9.1 swaps the OFAC stub for the real
feed and the keyword HTS resolver for a tariff-engine lookup.
W10 starts on `feat/erp-v2-w10-ops-readiness`.
