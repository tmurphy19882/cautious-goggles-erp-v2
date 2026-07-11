"""Master-data package — party, product, location, pricing (W1-W3).

W1 ships the bare minimum needed by O2C: products, locations, and a
thin "customer" wrapper around the existing `users` table (the
v1 platform already has parties as users; v2 keeps the same model
until W3 lands a true `parties` table).

Subpackages land in their respective waves:
- `master_data.product.*`  — W1
- `master_data.location.*` — W1
- `master_data.party.*`    — W1 (thin) → W3 (full)
- `master_data.pricing.*`  — W1 (read) → W3 (write)
"""
