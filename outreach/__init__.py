"""Lead sourcing and outreach, approval-first, synced to monday.com.

See outreach/README.md. Nothing in this package sends a message.
"""
from .schemas import Lead, LeadRecord, LeadState, Provenance, SchemaError

__all__ = ["Lead", "LeadRecord", "LeadState", "Provenance", "SchemaError"]
