# ADR-0006 — Multiline-by-default for SO and PO

| Field | Value |
|-------|-------|
| Status | **Accepted** |
| Date | 2026-07-11 |
| Wave | W0 (decision + validation hook); W1 ships the multiline O2C SO |
| Supersedes | v1's `body.lines[0]` shortcut (audit item O2C-1) |
| Related | [`SPEC.md`](../SPEC.md#wave-1) |

## Context

v1's sales-order and (planned) purchase-order handlers hardcode
`line = body.lines[0]`. Every additional line is silently dropped
on the floor. This is the most-cited P0 in [`AUDIT.md`](../../AUDIT.md)
(item O2C-1) and the largest single gap between the v1 service and
the SPEC.

Two options were considered for the v2 path:

1. **One-line path + multiline extension.** Keep a `single_line`
   path for the common case (one line, one product, one price)
   and add a `multiline` path for everything else. Cheaper to
   ship; lower blast radius for the migration.
2. **Multiline by default.** Always iterate `for line in body.lines`.
   The "one line" case is just a list of length 1. No shortcut.

The trap with (1): every downstream computation (subtotal, tax,
discount, credit exposure, reservation, GL post) has to be written
*twice* — once for the single-line path, once for the multiline
path. Drift between the two paths is the bug class that the
shortcut was supposed to avoid in the first place. v1's
`body.lines[0]` is the canonical example: it was a shortcut that
became the only path, and now we have a multiline-shaped
business process running through a single-line-shaped data path.

## Decision

**Multiline is the only path.** The schemas in
`o2c/sales_order/schemas.py` (W1) declare `lines: list[SalesOrderLine]`
with `min_length=1`. Every computation — subtotal, tax, discount,
credit exposure, reservation projection, FIFO COGS allocation, GL
post — iterates `for line in lines`. There is no
`body.lines[0]` shortcut anywhere in v2.

- **Schemas.** `SalesOrderLine`, `PurchaseOrderLine`,
  `QuoteLine`, `InvoiceLine` are first-class Pydantic v2 models
  with their own validators (UoM conversion, currency check,
  required fields).
- **Validators** are module-level (`@field_validator` /
  `@model_validator`) so the rules are testable in isolation
  and reusable across the four line types.
- **Discount / surcharge** are line-level (per-line) AND
  header-level (per-SO); the computation is `sum(line.net for
  line in lines) + header_adjustments`. The header adjustment
  is itself typed (`OrderDiscount | OrderSurcharge`).
- **One-line SOs** are `lines: [SalesOrderLine(...)]`. The
  ergonomics for the one-line case are slightly more verbose at
  the schema declaration site, identical at the call site.

## Consequences

### Positive

- **No dual code paths.** Every computation runs on `list[Line]`.
  v1's class of bug (subtotal correct, tax wrong because the
  shortcut only handled one line) is structurally impossible.
- **Audit O2C-1 closes.** "Multiline support" was the headline
  number in [`AUDIT.md`](../../AUDIT.md); this decision is the
  structural fix.
- **Quoting and invoicing inherit the same shape.** A quote with
  3 lines converts to an SO with 3 lines converts to an invoice
  with 3 lines. No line-count mismatch at any boundary.
- **Tax / discount are per-line and per-header.** This is how
  Avalara / TaxJar / real-world ERP pricing works; the schema
  matches reality.

### Negative / costs

- **One-line SOs are slightly more verbose.** `lines: [SalesOrderLine(product_id=..., quantity=..., unit_price=...)]`
  vs v1's `line: { ... }`. The frontend SDK generator handles
  the wrapping; the user-visible cost is one extra pair of
  brackets.
- **Header-level discount** can be a foot-gun if the implementer
  forgets to allocate it across lines for accounting. The
  validator enforces an allocation at SO confirm time
  (header discount = sum of per-line allocations, modulo
  rounding to the smallest currency unit).
- **Performance.** Iterating over a 100-line SO is the same cost
  as iterating over a 1-line SO at our scale; this is not a
  regression.

### What we lose

- v1's `body.lines[0]` "convenience." It was never convenient;
  it was a bug magnet. Gone.
- v1's `if len(lines) > 1: raise` defensive check. The schema
  enforces the minimum (1) and the maximum (TBD by tenant
  config, default 1000); no defensive check at the handler.

### W0 scope

W0 ships the decision and the validator hook (an
`o2c/` empty package with a placeholder `validators.py` stub
that future W1 work fills in). The first multiline O2C
sales order handler lands in W1.
