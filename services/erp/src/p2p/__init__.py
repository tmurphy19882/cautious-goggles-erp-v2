"""P2P domain package — requisition, PO, approval, receipt, 3-way match, AP.

Wave 2 — P2P complete. Closes P2P-1..P2P-9 from the audit.

Status machine for a PO:
    draft → pending_approval → approved → sent → partially_received
                                                          → received
                                                          → closed
                                ↘ rejected
                                ↘ cancelled
"""
