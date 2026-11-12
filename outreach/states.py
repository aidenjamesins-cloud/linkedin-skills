"""Approval state machine.

The only way into APPROVED is `approve()`, which requires a human actor. The
only way into SENT_MANUALLY is `mark_sent()`, also human-only: this tool never
sends anything, so it can only record that Aiden did.
"""
from __future__ import annotations

from typing import Optional

from .schemas import LeadRecord, LeadState as S, now_iso

TRANSITIONS: dict[S, set[S]] = {
    S.INGESTED: {S.SCORED, S.NOT_RELEVANT, S.BLOCKED_COMPLIANCE},
    S.SCORED: {S.DRAFTED, S.NOT_RELEVANT, S.BLOCKED_COMPLIANCE},
    S.DRAFTED: {S.APPROVED, S.REJECTED, S.DRAFTED, S.NOT_RELEVANT, S.BLOCKED_COMPLIANCE},
    S.APPROVED: {S.SYNCED, S.DRAFTED, S.REJECTED},
    S.SYNCED: {S.SENT_MANUALLY, S.NOT_RELEVANT},
    S.SENT_MANUALLY: {S.REPLIED, S.NOT_RELEVANT},
    S.REPLIED: {S.QUALIFIED, S.NOT_RELEVANT},
    S.QUALIFIED: set(),
    S.REJECTED: {S.DRAFTED},
    S.BLOCKED_COMPLIANCE: {S.SCORED},     # only after the blocking fact is corrected
    S.NOT_RELEVANT: set(),
}

HUMAN_ONLY = {S.APPROVED, S.SENT_MANUALLY, S.REPLIED, S.QUALIFIED, S.REJECTED, S.NOT_RELEVANT}
HUMAN = "human"


class TransitionError(RuntimeError):
    pass


def transition(record: LeadRecord, to: S, *, actor: str, note: Optional[str] = None) -> LeadRecord:
    if to not in TRANSITIONS[record.state]:
        raise TransitionError(f"{record.lead.id}: {record.state.value} -> {to.value} is not allowed")
    if to in HUMAN_ONLY and actor != HUMAN:
        raise TransitionError(f"{record.lead.id}: only a human can move a lead to {to.value}")
    record.history.append({"at": now_iso(), "from": record.state.value, "to": to.value, "actor": actor, "note": note})
    record.state = to
    return record


def approve(record: LeadRecord, *, actor: str, edited_body: Optional[str] = None) -> LeadRecord:
    if record.draft is None:
        raise TransitionError(f"{record.lead.id}: nothing to approve, no draft")
    if record.compliance is None or record.compliance.blocked:
        raise TransitionError(f"{record.lead.id}: compliance blocked, cannot approve")
    if edited_body is not None:
        record.draft.body = edited_body
        record.draft.validate()
    transition(record, S.APPROVED, actor=actor)
    record.approved_by = actor
    record.approved_at = record.history[-1]["at"]
    return record


def mark_sent(record: LeadRecord, *, actor: str) -> LeadRecord:
    return transition(record, S.SENT_MANUALLY, actor=actor, note="sent by hand outside this tool")
