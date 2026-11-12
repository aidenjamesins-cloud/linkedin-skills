"""Review cards and the daily brief.

Follows the shape of `lib/approval.py`'s approval card (what, preview, context,
an explicit prompt), with the fields this flow needs: score, why now, claim
check, risk flags and the next action. The prompt says approve, not publish,
because approval here only unlocks the monday write. Sending stays manual.
"""
from __future__ import annotations

from typing import Iterable

from .schemas import LeadRecord, LeadState


def card(rec: LeadRecord) -> str:
    lead, d, s, c = rec.lead, rec.draft, rec.score, rec.compliance
    out = [f"## {lead.name}" + (f", {lead.title}" if lead.title else "") + (f" at {lead.company}" if lead.company else ""),
           "", f"**State:** {rec.state.value}   **Id:** `{lead.id}`"]
    if s:
        out.append(f"**Score:** {s.total} (tier {s.tier}; wealth {s.wealth}, business {s.business}, intent {s.intent}, risk {s.risk_adj})")
    if c:
        if c.reasons:
            out.append("**Blocked:** " + "; ".join(c.reasons))
        if c.flags:
            out.append("**Risk flags:** " + "; ".join(c.flags))
    if d:
        out += [f"**Why now:** {d.why_now}", f"**Channel:** {d.channel} ({len(d.body)} chars)"]
        if d.subject:
            out.append(f"**Subject:** {d.subject}")
        out.append("**Claim check:** " + ("; ".join(d.claim_flags) if d.claim_flags else "no coverage, carrier, savings, underwriting, tax or legal claims found"))
        if d.lint:
            out.append("**Voice lint:** " + "; ".join(d.lint))
        out += ["", "**Draft:**", ""] + [f"> {line}" for line in d.body.splitlines()]
    out += ["", f"**Next:** {next_action(rec)}"]
    return "\n".join(out)


def next_action(rec: LeadRecord) -> str:
    st = rec.state
    if st == LeadState.BLOCKED_COMPLIANCE:
        return "fix the blocking fact on the lead, or mark it not relevant"
    if st == LeadState.DRAFTED:
        return f"approve (`python -m outreach approve {rec.lead.id}`), edit, or reject"
    if st == LeadState.APPROVED:
        return f"sync to monday (`python -m outreach sync {rec.lead.id}`, add --live to write)"
    if st == LeadState.SYNCED:
        return "send it yourself, then record it with `python -m outreach sent`"
    if st == LeadState.SENT_MANUALLY:
        return "wait for a reply; record it with `python -m outreach replied`"
    return "nothing pending"


_TIER_RANK = {"A": 0, "B": 1, "C": 2, "HOLD": 3}
_STATE_RANK = {LeadState.SYNCED: 0, LeadState.APPROVED: 1, LeadState.DRAFTED: 2}


def brief(records: Iterable[LeadRecord], n: int = 3) -> list[LeadRecord]:
    """The n most useful actions: ready-to-send first, then ready-to-sync, then best drafts."""
    actionable = [r for r in records if r.state in _STATE_RANK]
    actionable.sort(key=lambda r: (_STATE_RANK[r.state], _TIER_RANK.get(r.score.tier if r.score else "C", 2),
                                   -(r.score.total if r.score else 0)))
    return actionable[:n]
