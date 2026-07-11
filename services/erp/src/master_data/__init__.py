"""Master-data package — party, product, location, pricing (W1-W3).

W3 ships the full party model: `parties` (with kind = customer |
vendor | carrier | employee | internal_org), `party_contacts`,
`party_addresses`, `party_tax_ids`, plus a denormalised
`search_index` for fuzzy search.

Subpackages:
- `master_data.product.*`  — W1
- `master_data.location.*` — W1
- `master_data.party.*`    — W3 (full)
- `master_data.search.*`   — W3
- `master_data.pricing.*`  — W1 (read) → W3 (write)
"""
