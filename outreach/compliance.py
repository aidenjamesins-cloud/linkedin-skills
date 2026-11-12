"""Compliance gates.

Two kinds of output:

- **reasons** are hard blocks. A blocked lead gets no draft and cannot be synced.
- **flags** are soft. They ride on the review card so Aiden decides with them in view.

This is a guardrail, not legal advice. It errs toward blocking.
"""
from __future__ import annotations

import re
from typing import Iterable, Optional

from .schemas import VERIFY_CONSENT_SOURCES, ComplianceResult, Lead

# Claims an insurance advisor should not make in cold outreach without review.
CLAIM_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("savings claim", re.compile(r"\b(sav(e|es|ed|ing|ings)|cheaper|lower (your )?(premium|rate|cost)s?|cut (your )?costs?)\b", re.I)),
    ("guarantee", re.compile(r"\b(guarantee[sd]?|risk[- ]free|no risk)\b", re.I)),
    ("superlative rate claim", re.compile(r"\b(best|lowest|top)\s+(rate|price|plan|coverage|carrier)s?\b", re.I)),
    ("coverage claim", re.compile(r"\b(fully covered|covers everything|covered for|pre-?existing)\b", re.I)),
    ("carrier claim", re.compile(r"\b(carrier|insurer)s?\b.*\b(better|best|worse|cheapest)\b", re.I)),
    ("underwriting claim", re.compile(r"\b(underwriting|approved|approval|qualify|qualifies|eligible)\b", re.I)),
    ("tax claim", re.compile(r"\b(tax[- ]free|tax[- ]deductible|write[- ]off|deduct(ion|ible)?)\b", re.I)),
    ("legal or compliance claim", re.compile(r"\b(legal(ly)?|compliant|ERISA|HIPAA)\b", re.I)),
]

# Words that suggest a real client is identifiable in the copy.
PRIVACY_PATTERN = re.compile(r"\b(?i:my|a|our) client(,)? [A-Z][a-z]+\b")


def claim_flags(text: str) -> list[str]:
    found = [label for label, pat in CLAIM_PATTERNS if pat.search(text or "")]
    if PRIVACY_PATTERN.search(text or ""):
        found.append("possible identifiable client: anonymize or confirm consent")
    return found


def check(lead: Lead, licensed_states: Iterable[str], *, channels: Optional[Iterable[str]] = None) -> ComplianceResult:
    licensed = {s.strip().upper() for s in licensed_states}
    reasons: list[str] = []
    flags: list[str] = []

    if lead.dnc_status in {"Internal DNC", "National DNC"}:
        reasons.append(f"DNC status is {lead.dnc_status}")
    if lead.opt_out_date:
        reasons.append(f"opted out on {lead.opt_out_date}")
    if lead.risk_flag == "DNC/opt-out":
        reasons.append("risk flag is DNC/opt-out")
    if not licensed:
        reasons.append("no licensed states configured: set licensed_states in outreach/config.json")
    elif not lead.state:
        reasons.append("state unknown: the licensing gate needs a state before outreach")
    elif lead.state not in licensed:
        reasons.append(f"{lead.state} is outside licensed states")

    if lead.provenance.source in VERIFY_CONSENT_SOURCES:
        flags.append(f"source is {lead.provenance.source}: verify consent and scrub against DNC before contact")
    if lead.dnc_status in (None, "Unknown"):
        flags.append("DNC status unknown: scrub before any phone contact")
    if lead.risk_flag == "Strong existing advisor":
        flags.append("strong existing advisor: lead with usefulness, not a switch pitch")
    if lead.risk_flag == "Low-quality data":
        flags.append("low-quality data: confirm identity before drafting")

    wanted = list(channels) if channels else ["linkedin_note", "linkedin_message", "email"]
    allowed: list[str] = []
    for ch in wanted:
        if ch.startswith("linkedin"):
            if lead.linkedin_url:
                allowed.append(ch)
        elif ch == "email":
            if not lead.email:
                continue
            if lead.consent_type in {"Email opt-in", "Express written (TCPA)"} or (lead.lawful_basis_note or "").strip():
                allowed.append(ch)
            else:
                flags.append("email channel closed: no opt-in and no lawful-basis note")
    # Phone and SMS are never drafted here. Calls and texts need TCPA express written
    # consent and a DNC scrub, and are out of scope for this tool.

    if not allowed and not reasons:
        reasons.append("no permitted channel: needs a LinkedIn URL, or an email with opt-in or a lawful-basis note")

    return ComplianceResult(blocked=bool(reasons), reasons=reasons, flags=flags, allowed_channels=allowed)
