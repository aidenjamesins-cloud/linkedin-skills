"""Duplicate detection.

A lead matches another when any strong key matches: normalised email, phone
digits, LinkedIn profile slug, or name + company. The same keys drive the
monday adapter's lookup before create, so a re-run updates instead of
duplicating.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Iterable, Optional
from urllib.parse import urlparse

from .schemas import Lead

_COMPANY_SUFFIX = re.compile(r"\b(llc|pllc|inc|corp|co|ltd|lp|llp|pc|pa|company|group)\b\.?", re.I)


def _fold(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def norm_email(email: Optional[str]) -> Optional[str]:
    if not email:
        return None
    local, _, domain = email.strip().lower().partition("@")
    if domain in {"gmail.com", "googlemail.com"}:
        local = local.split("+", 1)[0].replace(".", "")
        domain = "gmail.com"
    return f"{local}@{domain}" if local and domain else None


def norm_phone(phone: Optional[str]) -> Optional[str]:
    if not phone:
        return None
    digits = re.sub(r"\D", "", phone)
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    return digits if len(digits) >= 10 else None


def norm_linkedin(url: Optional[str]) -> Optional[str]:
    if not url:
        return None
    raw = url.strip()
    if "://" not in raw:
        raw = "https://" + raw
    path = urlparse(raw).path.strip("/").lower()
    parts = path.split("/")
    if len(parts) >= 2 and parts[0] == "in":
        return "in/" + parts[1]
    return path or None


def norm_name_company(name: Optional[str], company: Optional[str]) -> Optional[str]:
    if not name or not company:
        return None
    return f"{_fold(name)}|{_fold(_COMPANY_SUFFIX.sub('', company))}"


def keys(lead: Lead) -> set[str]:
    out = set()
    if (e := norm_email(lead.email)):
        out.add("email:" + e)
    if (p := norm_phone(lead.phone)):
        out.add("phone:" + p)
    if (li := norm_linkedin(lead.linkedin_url)):
        out.add("linkedin:" + li)
    if (nc := norm_name_company(lead.name, lead.company)):
        out.add("nameco:" + nc)
    return out


def find_duplicate(lead: Lead, existing: Iterable[Lead]) -> Optional[Lead]:
    mine = keys(lead)
    if not mine:
        return None
    for other in existing:
        if other.id != lead.id and mine & keys(other):
            return other
    return None


def split_new(leads: Iterable[Lead], existing: Iterable[Lead]) -> tuple[list[Lead], list[tuple[Lead, Lead]]]:
    """Return (new leads, [(duplicate, the lead it matched)]). Also dedupes within the batch."""
    seen: list[Lead] = list(existing)
    new: list[Lead] = []
    dupes: list[tuple[Lead, Lead]] = []
    for lead in leads:
        match = find_duplicate(lead, seen)
        if match:
            dupes.append((lead, match))
        else:
            new.append(lead)
            seen.append(lead)
    return new, dupes
