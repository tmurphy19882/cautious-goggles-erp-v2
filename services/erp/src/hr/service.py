"""W8 — HR + Legal services.

W8 ships:
- `HRService` — departments CRUD, employees CRUD, payroll export
  (synthesizes a stub file_url — the W8.1 swap uploads to S3).
- `ContractService` — contract templates + contracts, render the
  handlebar-style template, state-machine status transitions
  (draft → sent → signed → active, etc.).
- `ESignService` — create an envelope, mark sent, mark completed.
  The W8.1 swap talks to DocuSign.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import text as _sa_text
from sqlalchemy.ext.asyncio import AsyncSession

from shared.schemas import utcnow

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------- #
# HR                                                                      #
# --------------------------------------------------------------------- #


class HRService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create_department(
        self, *, tenant_id: UUID, name: str, code: str, parent_id: UUID | None = None
    ) -> UUID:
        did = uuid4()
        await self._session.execute(
            _sa_text(
                """
                INSERT INTO departments (id, tenant_id, name, code, parent_id)
                VALUES (:id, :tenant_id, :name, :code, :parent_id)
                """
            ),
            {"id": did, "tenant_id": tenant_id, "name": name, "code": code, "parent_id": parent_id},
        )
        await self._session.flush()
        return did

    async def create_employee(
        self,
        *,
        tenant_id: UUID,
        employee_number: str,
        first_name: str,
        last_name: str,
        email: str,
        department_id: UUID | None = None,
        title: str | None = None,
        hire_date: str | None = None,
    ) -> UUID:
        eid = uuid4()
        await self._session.execute(
            _sa_text(
                """
                INSERT INTO employees (
                    id, tenant_id, employee_number, first_name, last_name, email,
                    department_id, title, hire_date
                ) VALUES (
                    :id, :tenant_id, :num, :fn, :ln, :email,
                    :did, :title, :hire
                )
                """
            ),
            {
                "id": eid, "tenant_id": tenant_id, "num": employee_number,
                "fn": first_name, "ln": last_name, "email": email,
                "did": department_id, "title": title, "hire": hire_date,
            },
        )
        await self._session.flush()
        return eid

    async def export_payroll(
        self, *, tenant_id: UUID, period_start: str, period_end: str, currency: str = "USD"
    ) -> UUID:
        """Synthesize a payroll export. W8 stub. The W8.1 swap uploads
        the actual file to S3 and writes the file_url.
        """
        eid = uuid4()
        rows = (
            await self._session.execute(
                _sa_text(
                    "SELECT id FROM employees WHERE tenant_id = :tid AND status = 'active'"
                ),
                {"tid": tenant_id},
            )
        ).all()
        await self._session.execute(
            _sa_text(
                """
                INSERT INTO payroll_exports (
                    id, tenant_id, period_start, period_end, status, row_count,
                    total_amount, currency
                ) VALUES (
                    :id, :tenant_id, :start, :end, 'generated', :rows, 0, :cur
                )
                """
            ),
            {
                "id": eid, "tenant_id": tenant_id, "start": period_start,
                "end": period_end, "rows": len(rows), "cur": currency,
            },
        )
        await self._session.flush()
        return eid


# --------------------------------------------------------------------- #
# Legal                                                                   #
# --------------------------------------------------------------------- #


# Simple {{key}} handlebar replacement. W8.1 swaps in a real engine.
_VAR_RE = re.compile(r"\{\{\s*([a-zA-Z0-9_]+)\s*\}\}")


def render_template(body_md: str, variables: dict[str, Any]) -> str:
    def _sub(m: re.Match[str]) -> str:
        key = m.group(1)
        if key not in variables:
            raise ValueError(f"missing template variable: {key}")
        return str(variables[key])
    return _VAR_RE.sub(_sub, body_md)


class ContractService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create_template(
        self,
        *,
        tenant_id: UUID,
        name: str,
        kind: str,
        body_md: str,
        variables: list[str] | None = None,
    ) -> UUID:
        tid = uuid4()
        await self._session.execute(
            _sa_text(
                """
                INSERT INTO contract_templates (
                    id, tenant_id, name, kind, body_md, variables
                ) VALUES (
                    :id, :tenant_id, :name, :kind, :body, :vars::jsonb
                )
                """
            ),
            {
                "id": tid, "tenant_id": tenant_id, "name": name, "kind": kind,
                "body": body_md, "vars": json.dumps(variables or []),
            },
        )
        await self._session.flush()
        return tid

    async def create_contract(
        self,
        *,
        tenant_id: UUID,
        template_id: UUID,
        title: str,
        kind: str,
        variables: dict[str, Any],
        party_id: UUID | None = None,
        employee_id: UUID | None = None,
        effective_date: str | None = None,
        expiration_date: str | None = None,
    ) -> UUID:
        # Load + render the template
        tmpl = (
            await self._session.execute(
                _sa_text(
                    "SELECT body_md FROM contract_templates "
                    "WHERE id = :id AND tenant_id = :tid"
                ),
                {"id": template_id, "tid": tenant_id},
            )
        ).first()
        if tmpl is None:
            raise ValueError("template not found")
        rendered = render_template(tmpl[0], variables)
        cid = uuid4()
        await self._session.execute(
            _sa_text(
                """
                INSERT INTO contracts (
                    id, tenant_id, template_id, party_id, employee_id, kind, title,
                    effective_date, expiration_date, rendered_body_md, variables
                ) VALUES (
                    :id, :tenant_id, :tid, :pid, :eid, :kind, :title,
                    :eff, :exp, :body, :vars::jsonb
                )
                """
            ),
            {
                "id": cid, "tenant_id": tenant_id, "tid": template_id,
                "pid": party_id, "eid": employee_id, "kind": kind, "title": title,
                "eff": effective_date, "exp": expiration_date,
                "body": rendered, "vars": json.dumps(variables),
            },
        )
        await self._session.flush()
        return cid

    # State machine ----------------------------------------------------- #
    _TRANSITIONS = {
        "draft": {"sent"},
        "sent": {"signed", "declined"},
        "signed": {"countersigned", "active"},
        "countersigned": {"active"},
        "active": {"expired", "terminated"},
        "expired": set(),
        "terminated": set(),
        "declined": {"draft"},  # can revise + re-send
    }

    async def transition(
        self, *, tenant_id: UUID, contract_id: UUID, target: str
    ) -> None:
        current = (
            await self._session.execute(
                _sa_text(
                    "SELECT status FROM contracts "
                    "WHERE id = :id AND tenant_id = :tid"
                ),
                {"id": contract_id, "tid": tenant_id},
            )
        ).scalar_one_or_none()
        if current is None:
            raise ValueError("contract not found")
        allowed = self._TRANSITIONS.get(current, set())
        if target not in allowed:
            raise ValueError(f"invalid transition: {current} -> {target}")
        await self._session.execute(
            _sa_text(
                "UPDATE contracts SET status = :target WHERE id = :id AND tenant_id = :tid"
            ),
            {"target": target, "id": contract_id, "tid": tenant_id},
        )
        await self._session.flush()


class ESignService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create_envelope(
        self, *, tenant_id: UUID, contract_id: UUID, provider: str = "docusign"
    ) -> UUID:
        eid = uuid4()
        await self._session.execute(
            _sa_text(
                """
                INSERT INTO esign_envelopes (id, tenant_id, contract_id, provider)
                VALUES (:id, :tenant_id, :cid, :provider)
                """
            ),
            {"id": eid, "tenant_id": tenant_id, "cid": contract_id, "provider": provider},
        )
        # Move the contract to 'sent'
        await self._session.execute(
            _sa_text(
                "UPDATE contracts SET status = 'sent' "
                "WHERE id = :cid AND tenant_id = :tid"
            ),
            {"cid": contract_id, "tid": tenant_id},
        )
        await self._session.flush()
        return eid

    async def record_event(
        self,
        *,
        tenant_id: UUID,
        envelope_id: UUID,
        event_type: str,
        signer_email: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> UUID:
        ev_id = uuid4()
        await self._session.execute(
            _sa_text(
                """
                INSERT INTO esign_events (
                    id, tenant_id, envelope_id, event_type, signer_email, payload
                ) VALUES (
                    :id, :tenant_id, :eid, :etype, :email, :payload::jsonb
                )
                """
            ),
            {
                "id": ev_id, "tenant_id": tenant_id, "eid": envelope_id,
                "etype": event_type, "email": signer_email,
                "payload": json.dumps(payload or {}),
            },
        )
        # If signed, mark the envelope as completed and move the contract
        # to 'signed' (the next step is 'countersigned' or 'active').
        if event_type == "signed":
            await self._session.execute(
                _sa_text(
                    "UPDATE esign_envelopes SET status = 'completed', completed_at = :now "
                    "WHERE id = :eid AND tenant_id = :tid"
                ),
                {"now": utcnow(), "eid": envelope_id, "tid": tenant_id},
            )
            # Find the contract + transition
            cid = (
                await self._session.execute(
                    _sa_text(
                        "SELECT contract_id FROM esign_envelopes WHERE id = :eid"
                    ),
                    {"eid": envelope_id},
                )
            ).scalar_one_or_none()
            if cid is not None:
                try:
                    cs = ContractService(self._session)
                    await cs.transition(
                        tenant_id=tenant_id, contract_id=cid, target="signed"
                    )
                except ValueError:  # pragma: no cover
                    pass
        await self._session.flush()
        return ev_id
