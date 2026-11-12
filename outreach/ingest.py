"""Ingestion from permitted sources only: CSV exports you own and manual entry.

There is deliberately no fetcher here. LinkedIn profiles are never scraped; a
LinkedIn URL is only stored when you supply it. Every row must say where it came
from (`source`, `permission_basis`, `source_detail`), or it is rejected.
"""
from __future__ import annotations

import csv
import pathlib
from typing import Any, Iterable, Optional

from .schemas import Lead, Provenance, SchemaError

# CSV header aliases -> Lead field. Lowercased, spaces and dashes folded to "_".
ALIASES = {
    "full_name": "name", "contact": "name", "contact_name": "name",
    "company_name": "company", "business": "company", "business_name": "company",
    "job_title": "title", "email_address": "email", "e_mail": "email",
    "phone_number": "phone", "mobile": "phone",
    "linkedin": "linkedin_url", "linkedin_profile": "linkedin_url",
    "st": "state",
    "lead_source": "source",
    "trigger": "trigger_event",
}
PROVENANCE_FIELDS = {"source", "permission_basis", "source_detail", "source_url", "collected_at"}


def _key(header: str) -> str:
    k = header.strip().lower().replace(" ", "_").replace("-", "_")
    return ALIASES.get(k, k)


def lead_from_row(row: dict[str, Any], defaults: Optional[dict[str, str]] = None) -> Lead:
    clean = {_key(k): (v.strip() if isinstance(v, str) else v) for k, v in row.items() if k}
    clean = {k: v for k, v in clean.items() if v not in ("", None)}
    for k, v in (defaults or {}).items():
        clean.setdefault(k, v)
    prov = {k: clean.pop(k) for k in list(clean) if k in PROVENANCE_FIELDS}
    missing = [f for f in ("source", "permission_basis", "source_detail") if not prov.get(f)]
    if missing:
        raise SchemaError(f"row for {clean.get('name', '?')!r} is missing provenance: {', '.join(missing)}")
    return Lead.from_dict({**clean, "provenance": prov})


def read_csv(path: pathlib.Path, defaults: Optional[dict[str, str]] = None) -> tuple[list[Lead], list[str]]:
    """Return (valid leads, row errors). A bad row never stops the batch."""
    leads: list[Lead] = []
    errors: list[str] = []
    with pathlib.Path(path).open(newline="", encoding="utf-8-sig") as fh:
        for i, row in enumerate(csv.DictReader(fh), start=2):
            try:
                leads.append(lead_from_row(row, defaults))
            except SchemaError as exc:
                errors.append(f"line {i}: {exc}")
    return leads, errors


def manual(name: str, *, source_detail: str, source: str = "LinkedIn",
           permission_basis: str = "user_provided", **fields: Any) -> Lead:
    return Lead(name=name, provenance=Provenance(source=source, permission_basis=permission_basis,
                                                 source_detail=source_detail), **fields).validate()


def from_dicts(rows: Iterable[dict[str, Any]]) -> list[Lead]:
    return [lead_from_row(r) for r in rows]
