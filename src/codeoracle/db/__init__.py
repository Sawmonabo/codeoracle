"""The platform's own Postgres (PG18 + AGE + pgvector) database access layer.

Layer purity (see CLAUDE.md §strict-layer-purity):
  - this package OWNS connection lifecycle, transaction management, and the
    AGE-per-connection contract
  - it does NOT own business logic — that's services/
  - it does NOT wrap external systems — that's adapters/ (and this DB is
    internal)
"""
