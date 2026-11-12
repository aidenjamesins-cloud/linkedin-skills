"""Typed records for the outreach pipeline.

Stdlib dataclasses only, so the module imports with no dependencies installed.
Every record is a plain JSON-serialisable shape (`to_dict` / `from_dict`), which
is what makes each stage inspectable and editable on disk.

Label vocabularies below are copied from the monday CRM > Leads board, because
that board is where approved leads land and its formulas score on these exact
strings. `tests/test_outreach_scoring.py` pins them against the board formulas.
"""
from __future__ import annotations

import dataclasses
import datetime as _dt
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


class SchemaError(ValueError):
    """A record failed validation. The message names the field."""


# ---------------------------------------------------------------- vocabularies

LEAD_SOURCES = {
    "Email", "LinkedIn", "COI introduction", "Facebook ads", "Client referral",
    "Event/Seminar", "Webinar", "Inbound web/Calendly", "Outbound reply",
    "Public-record signal", "Purchased list", "Aged lead", "Data-provider list",
    "monday Form", "Google ads",
}

# Sources that arrive without a direct relationship: consent must be verified
# before any outreach channel opens.
VERIFY_CONSENT_SOURCES = {"Purchased list", "Data-provider list", "Aged lead"}

# How a record is allowed into the system at all. Ingestion rejects anything else.
PERMISSION_BASES = {
    "user_provided",      # Aiden typed or pasted it
    "owned_export",       # a CSV export from a list or tool Aiden owns or licenses
    "public_record",      # a public filing, press release or public post, supplied by URL
    "referral",           # a named person introduced them
    "inbound",            # they reached out (form, Calendly, reply)
}

CONSENT_TYPES = {"Express written (TCPA)", "Email opt-in", "Verbal", "None"}
DNC_STATUSES = {"Clear", "Internal DNC", "National DNC", "Unknown"}
RISK_FLAGS = {"None", "Strong existing advisor", "DNC/opt-out", "Outside licensed state", "Low-quality data"}
ENGAGEMENT_LEVELS = {"None", "Opened/clicked", "Replied", "Meeting booked"}
EXISTING_ADVISOR = {"None", "Weak", "Strong", "Unknown"}

CHANNELS = {"linkedin_note", "linkedin_message", "email"}


class LeadState(str, Enum):
    INGESTED = "ingested"
    SCORED = "scored"
    DRAFTED = "drafted"
    APPROVED = "approved"
    SYNCED = "synced"
    SENT_MANUALLY = "sent_manually"
    REPLIED = "replied"
    QUALIFIED = "qualified"
    REJECTED = "rejected"
    BLOCKED_COMPLIANCE = "blocked_compliance"
    NOT_RELEVANT = "not_relevant"


def now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat()


def _parse_date(value: Optional[str], name: str) -> Optional[str]:
    if value in (None, ""):
        return None
    try:
        return _dt.date.fromisoformat(str(value)[:10]).isoformat()
    except ValueError as exc:
        raise SchemaError(f"{name}: expected YYYY-MM-DD, got {value!r}") from exc


def _check(value: Optional[str], allowed: set[str], name: str) -> Optional[str]:
    if value in (None, ""):
        return None
    if value not in allowed:
        raise SchemaError(f"{name}: {value!r} is not one of {sorted(allowed)}")
    return value


# ---------------------------------------------------------------- records

@dataclass
class Provenance:
    """Where a lead came from and why we are allowed to hold it. Required."""

    source: str                 # one of LEAD_SOURCES
    permission_basis: str       # one of PERMISSION_BASES
    source_detail: str          # e.g. "Nov 6 estate-planning webinar registrants"
    collected_at: str = field(default_factory=now_iso)
    source_url: Optional[str] = None

    def validate(self) -> "Provenance":
        if not self.source:
            raise SchemaError("provenance.source is required")
        _check(self.source, LEAD_SOURCES, "provenance.source")
        if not self.permission_basis:
            raise SchemaError("provenance.permission_basis is required")
        _check(self.permission_basis, PERMISSION_BASES, "provenance.permission_basis")
        if not (self.source_detail or "").strip():
            raise SchemaError("provenance.source_detail is required: say which list, event or URL")
        return self


@dataclass
class Lead:
    name: str
    provenance: Provenance
    company: Optional[str] = None
    title: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    linkedin_url: Optional[str] = None
    state: Optional[str] = None             # US state code, the licensing gate
    entity_type: Optional[str] = None
    profession: Optional[str] = None
    revenue_band: Optional[str] = None
    assets_band: Optional[str] = None
    age_band: Optional[str] = None
    employees: Optional[int] = None
    trigger_event: Optional[str] = None
    trigger_date: Optional[str] = None
    engagement: Optional[str] = None
    existing_advisor: Optional[str] = None
    risk_flag: Optional[str] = None
    consent_type: Optional[str] = None
    consent_date: Optional[str] = None
    dnc_status: Optional[str] = None
    opt_out_date: Optional[str] = None
    lawful_basis_note: Optional[str] = None   # why an email is appropriate without opt-in
    notes: Optional[str] = None
    id: str = field(default_factory=lambda: "lead_" + uuid.uuid4().hex[:12])

    def validate(self) -> "Lead":
        if not (self.name or "").strip():
            raise SchemaError("name is required")
        if not isinstance(self.provenance, Provenance):
            raise SchemaError("provenance is required on every lead")
        self.provenance.validate()
        if self.email and "@" not in self.email:
            raise SchemaError(f"email: {self.email!r} is not an address")
        if self.state:
            self.state = self.state.strip().upper()
            if len(self.state) != 2:
                raise SchemaError(f"state: expected a 2-letter code, got {self.state!r}")
        _check(self.consent_type, CONSENT_TYPES, "consent_type")
        _check(self.dnc_status, DNC_STATUSES, "dnc_status")
        _check(self.risk_flag, RISK_FLAGS, "risk_flag")
        _check(self.engagement, ENGAGEMENT_LEVELS, "engagement")
        _check(self.existing_advisor, EXISTING_ADVISOR, "existing_advisor")
        self.trigger_date = _parse_date(self.trigger_date, "trigger_date")
        self.consent_date = _parse_date(self.consent_date, "consent_date")
        self.opt_out_date = _parse_date(self.opt_out_date, "opt_out_date")
        if self.employees not in (None, ""):
            try:
                self.employees = int(self.employees)
            except (TypeError, ValueError) as exc:
                raise SchemaError(f"employees: expected a number, got {self.employees!r}") from exc
        else:
            self.employees = None
        if self.linkedin_url and "linkedin.com/" not in self.linkedin_url:
            raise SchemaError(f"linkedin_url: {self.linkedin_url!r} is not a LinkedIn URL")
        return self

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Lead":
        data = dict(data)
        prov = data.pop("provenance", None)
        if not isinstance(prov, dict):
            raise SchemaError("provenance is required on every lead")
        known = {f.name for f in dataclasses.fields(cls)}
        unknown = set(data) - known
        if unknown:
            raise SchemaError(f"unknown lead fields: {sorted(unknown)}")
        return cls(provenance=Provenance(**prov), **data).validate()


@dataclass
class Score:
    wealth: int
    business: int
    intent: int
    risk_adj: int
    total: int
    tier: str                    # A / B / C / HOLD

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


@dataclass
class ComplianceResult:
    blocked: bool
    reasons: list[str]           # hard blocks: no draft, no sync
    flags: list[str]             # soft flags: shown on the review card
    allowed_channels: list[str]

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


@dataclass
class Draft:
    lead_id: str
    channel: str
    body: str
    why_now: str
    claim_flags: list[str] = field(default_factory=list)
    lint: list[str] = field(default_factory=list)
    subject: Optional[str] = None
    created_at: str = field(default_factory=now_iso)

    def validate(self) -> "Draft":
        _check(self.channel, CHANNELS, "channel")
        if not self.body.strip():
            raise SchemaError("draft body is empty")
        if self.channel == "linkedin_note" and len(self.body) > 300:
            raise SchemaError(f"linkedin_note is {len(self.body)} chars; LinkedIn caps connection notes at 300")
        if self.channel == "email" and not self.subject:
            raise SchemaError("email drafts need a subject")
        return self

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


@dataclass
class LeadRecord:
    """A lead plus everything the pipeline has decided about it."""

    lead: Lead
    state: LeadState = LeadState.INGESTED
    score: Optional[Score] = None
    compliance: Optional[ComplianceResult] = None
    draft: Optional[Draft] = None
    monday_item_id: Optional[str] = None
    approved_by: Optional[str] = None
    approved_at: Optional[str] = None
    history: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        d = dataclasses.asdict(self)
        d["state"] = self.state.value
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "LeadRecord":
        data = dict(data)
        return cls(
            lead=Lead.from_dict(data["lead"]),
            state=LeadState(data.get("state", "ingested")),
            score=Score(**data["score"]) if data.get("score") else None,
            compliance=ComplianceResult(**data["compliance"]) if data.get("compliance") else None,
            draft=Draft(**data["draft"]) if data.get("draft") else None,
            monday_item_id=data.get("monday_item_id"),
            approved_by=data.get("approved_by"),
            approved_at=data.get("approved_at"),
            history=list(data.get("history") or []),
        )
