"""Audit fix — add the `platform.payment.write` permission key to the
global catalog.

Background: the W6 `POST /platform/payments/intents` route originally
declared `dependencies=[Depends(require_permission("platform.webhook.write"))]`
(copy-paste from the webhook route). The audit (P2-2) changed it to
`platform.payment.write`, but that key was never seeded in migration
0100. This migration adds it, idempotently, so existing deployments
pick it up and fresh ones get it from a single source of truth.
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0115_platform_payment_permission"
down_revision: str | Sequence[str] | None = "0114_w6_w7_rls_repair"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        INSERT INTO permissions (key, resource, action, description)
        VALUES (
            'platform.payment.write', 'payment', 'write',
            'Create payment intents (PL-7, PL-8)'
        )
        ON CONFLICT (key) DO NOTHING
        """
    )


def downgrade() -> None:
    op.execute("DELETE FROM permissions WHERE key = 'platform.payment.write'")
