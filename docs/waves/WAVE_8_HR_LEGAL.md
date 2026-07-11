# Wave 8 — HR + Legal (DONE)

> **Branch:** `feat/erp-v2-w8-hr-legal`
> **Spec:** [`/docs/SPEC.md#wave-8`](../SPEC.md)

W8 ships the people-and-paperwork surface.

## What ships

### Migration
- `0111_w8_hr_legal` — 7 new tables: `departments`, `employees`,
  `payroll_exports`, `contract_templates`, `contracts`,
  `esign_envelopes`, `esign_events`. All FORCE RLS.

### Source
- `hr/service.py` — `HRService` (departments + employees + payroll
  export stub), `ContractService` (templates + contracts +
  state machine: draft → sent → signed → countersigned → active →
  expired/terminated; declined can go back to draft), `ESignService`
  (envelopes + events; a `signed` event auto-transitions the
  contract to `signed`). `render_template` is a minimal
  `{{key}}` handlebar engine.
- `hr/api.py` — REST routes: `/hr/departments`, `/hr/employees`,
  `/hr/payroll-exports`, `/legal/contract-templates`,
  `/legal/contracts`, `/legal/contracts/{id}/transition`,
  `/legal/contracts/{id}/envelopes`,
  `/legal/envelopes/{id}/events`.

### Tests
- `tests/integration/test_w8_hr_legal.py` — department + employee
  create, payroll export, template render (incl. missing var),
  contract create + state machine (incl. invalid transition),
  e-sign envelope + signed event moves contract to `signed`.

## Audit items closed

| ID | Description | Closed by |
|----|-------------|-----------|
| HR-1 | Employee records | `employees` + `HRService.create_employee` |
| HR-2 | Departments | `departments` + `HRService.create_department` |
| HR-3 | Payroll export | `payroll_exports` + `HRService.export_payroll` (stub) |
| LEG-1 | Contract templates | `contract_templates` + `ContractService.create_template` |
| LEG-2 | Contracts | `contracts` + state machine |
| LEG-3 | E-sign envelope | `esign_envelopes` + `ESignService` (DocuSign-shaped; real swap is W8.1) |
| LEG-4 | E-sign events | `esign_events` + `record_event` |

## How to merge

Push `feat/erp-v2-w8-hr-legal` to the parent, open a PR titled:

```
feat(erp-v2): HR + Legal (employees, departments, contracts, e-sign)
```

Risk: **Low** — additive. W8.1 swaps DocuSign-shaped stub for
the real provider; payroll export for S3 upload. W9 starts on
`feat/erp-v2-w9-trade-polish`.
