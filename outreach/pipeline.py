"""Pipeline: wires the stages together over a local JSON store.

ingest -> dedupe -> score -> compliance -> draft -> (human approve) -> monday sync -> (human sent)

Local and private by default: records live in `outreach/.data/` (gitignored).
Swapping the store for hosted storage means replacing `Store`; nothing else
reads the file.
"""
from __future__ import annotations

import datetime as _dt
import json
import pathlib
from typing import Any, Iterable, Optional

from . import compliance, scoring
from .action_log import ActionLog
from .dedupe import split_new
from .drafting import Drafter, TemplateDrafter, pick_channel
from .schemas import Lead, LeadRecord, LeadState
from .states import HUMAN, TransitionError, approve, mark_sent, transition

PKG = pathlib.Path(__file__).resolve().parent
DEFAULT_DATA = PKG / ".data"


def load_config(path: Optional[pathlib.Path] = None) -> dict[str, Any]:
    for p in [path, PKG / "config.json", PKG / "config.example.json"]:
        if p and pathlib.Path(p).is_file():
            return json.loads(pathlib.Path(p).read_text(encoding="utf-8"))
    raise FileNotFoundError("no outreach config found")


class Store:
    def __init__(self, data_dir: pathlib.Path = DEFAULT_DATA):
        self.dir = pathlib.Path(data_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.path = self.dir / "records.json"
        self.log = ActionLog(self.dir / "actions.jsonl")

    def load(self) -> dict[str, LeadRecord]:
        if not self.path.exists():
            return {}
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        return {k: LeadRecord.from_dict(v) for k, v in raw.items()}

    def save(self, records: dict[str, LeadRecord]) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({k: r.to_dict() for k, r in records.items()}, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.path)


def process(lead: Lead, config: dict[str, Any], *, drafter: Optional[Drafter] = None,
            log: Optional[ActionLog] = None, today: Optional[_dt.date] = None) -> LeadRecord:
    rec = LeadRecord(lead=lead)
    src = lead.provenance.source
    rec.score = scoring.score(lead, today)
    rec.compliance = compliance.check(lead, config.get("licensed_states", []), channels=config.get("channels"))
    if rec.compliance.blocked:
        transition(rec, LeadState.BLOCKED_COMPLIANCE, actor="system", note="; ".join(rec.compliance.reasons))
        if log:
            log.write(lead_id=lead.id, source=src, action="draft", approval="n/a", actor="system",
                      outcome="blocked", detail={"reasons": rec.compliance.reasons})
        return rec
    transition(rec, LeadState.SCORED, actor="system")
    if rec.score.tier == "HOLD" or rec.score.total < int(config.get("min_score_to_draft", 0)):
        if log:
            log.write(lead_id=lead.id, source=src, action="draft", approval="n/a", actor="system",
                      outcome="skipped_low_score", detail=rec.score.to_dict())
        return rec
    channel = pick_channel(rec.compliance.allowed_channels)
    rec.draft = (drafter or TemplateDrafter()).draft(lead, channel)
    transition(rec, LeadState.DRAFTED, actor="system")
    if log:
        log.write(lead_id=lead.id, source=src, action=f"draft.{channel}", approval="pending", actor="system",
                  outcome="drafted", detail={"claim_flags": rec.draft.claim_flags, "lint": rec.draft.lint})
    return rec


def ingest(leads: Iterable[Lead], store: Store, config: dict[str, Any], *,
           drafter: Optional[Drafter] = None, today: Optional[_dt.date] = None) -> dict[str, Any]:
    records = store.load()
    new, dupes = split_new(leads, [r.lead for r in records.values()])
    for dup, match in dupes:
        store.log.write(lead_id=dup.id, source=dup.provenance.source, action="ingest", approval="n/a",
                        actor="system", outcome="duplicate", detail={"matches": match.id, "name": dup.name})
    for lead in new:
        records[lead.id] = process(lead, config, drafter=drafter, log=store.log, today=today)
    store.save(records)
    return {"added": [l.id for l in new], "duplicates": [(d.id, m.id) for d, m in dupes]}


def linkedin_approvals_today(store: Store, today: Optional[_dt.date] = None) -> int:
    day = (today or _dt.date.today()).isoformat()
    return sum(1 for e in store.log.entries()
               if e["action"].startswith("approve.linkedin") and e["at"].startswith(day))


def approve_lead(store: Store, lead_id: str, config: dict[str, Any], *, actor: str = HUMAN,
                 edited_body: Optional[str] = None, today: Optional[_dt.date] = None) -> LeadRecord:
    records = store.load()
    rec = records[lead_id]
    if rec.draft and rec.draft.channel.startswith("linkedin"):
        cap = int(config.get("max_linkedin_per_day", 15))
        if linkedin_approvals_today(store, today) >= cap:
            raise TransitionError(f"daily LinkedIn cap of {cap} reached; approve more tomorrow")
    approve(rec, actor=actor, edited_body=edited_body)
    store.log.write(lead_id=lead_id, source=rec.lead.provenance.source, action=f"approve.{rec.draft.channel}",
                    approval="approved", actor=actor, outcome="approved", detail={"edited": edited_body is not None})
    store.save(records)
    return rec


def human_transition(store: Store, lead_id: str, to: LeadState, *, note: Optional[str] = None) -> LeadRecord:
    records = store.load()
    rec = records[lead_id]
    if to == LeadState.SENT_MANUALLY:
        mark_sent(rec, actor=HUMAN)
    else:
        transition(rec, to, actor=HUMAN, note=note)
    store.log.write(lead_id=lead_id, source=rec.lead.provenance.source, action=f"state.{to.value}",
                    approval="human", actor=HUMAN, outcome=to.value, detail={"note": note})
    store.save(records)
    return rec
