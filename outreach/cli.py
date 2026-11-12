"""Command line: `python -m outreach <command>`.

    ingest FILE.csv          load leads, dedupe, score, check, draft
    list                     every lead with state, tier and score
    show ID                  the review card
    brief                    the three most useful actions right now
    approve ID [--edit F]    approve the draft (optionally replacing its text with file F)
    reject ID | irrelevant ID
    sync ID [--live]         write to monday (dry run unless --live)
    sent ID [--live]         record that you sent it yourself; sets Contacted in monday
    replied ID | qualified ID
    log                      the action log
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

from . import monday_adapter as md
from . import pipeline, review
from .ingest import read_csv
from .schemas import LeadState
from .states import TransitionError


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="outreach")
    p.add_argument("--config", type=pathlib.Path)
    p.add_argument("--data", type=pathlib.Path, default=pipeline.DEFAULT_DATA)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("ingest").add_argument("csv", type=pathlib.Path)
    sub.add_parser("list")
    sub.add_parser("brief")
    sub.add_parser("log")
    for name in ("show", "reject", "irrelevant", "replied", "qualified"):
        sub.add_parser(name).add_argument("id")
    ap = sub.add_parser("approve")
    ap.add_argument("id")
    ap.add_argument("--edit", type=pathlib.Path)
    for name in ("sync", "sent"):
        sp = sub.add_parser(name)
        sp.add_argument("id")
        sp.add_argument("--live", action="store_true")
    a = p.parse_args(argv)

    config = pipeline.load_config(a.config)
    store = pipeline.Store(a.data)
    try:
        if a.cmd == "ingest":
            leads, errors = read_csv(a.csv)
            for e in errors:
                print("skipped", e, file=sys.stderr)
            print(json.dumps(pipeline.ingest(leads, store, config), indent=2))
        elif a.cmd == "list":
            for r in store.load().values():
                s = r.score
                print(f"{r.lead.id}  {r.state.value:<18} {s.tier if s else '-':<4} {s.total if s else '-':>3}  {r.lead.name}")
        elif a.cmd == "brief":
            top = review.brief(store.load().values())
            if not top:
                print("Nothing actionable. Ingest a list or review blocked leads.")
            for i, r in enumerate(top, 1):
                print(f"{i}. {r.lead.name} ({r.score.tier}, {r.score.total}): {review.next_action(r)}")
        elif a.cmd == "show":
            print(review.card(store.load()[a.id]))
        elif a.cmd == "approve":
            body = a.edit.read_text(encoding="utf-8") if a.edit else None
            print(review.card(pipeline.approve_lead(store, a.id, config, edited_body=body)))
        elif a.cmd in ("reject", "irrelevant", "replied", "qualified"):
            to = {"reject": LeadState.REJECTED, "irrelevant": LeadState.NOT_RELEVANT,
                  "replied": LeadState.REPLIED, "qualified": LeadState.QUALIFIED}[a.cmd]
            print(pipeline.human_transition(store, a.id, to).state.value)
        elif a.cmd in ("sync", "sent"):
            records = store.load()
            rec = records[a.id]
            client = md.HttpMondayClient() if a.live else None
            if a.cmd == "sent":
                pipeline.human_transition(store, a.id, LeadState.SENT_MANUALLY)
                records = store.load()
                rec = records[a.id]
                plan = md.mark_contacted(rec, client, config, live=a.live, log=store.log)
            else:
                plan = md.sync(rec, client, config, live=a.live, log=store.log)
            store.save(records)
            print(json.dumps(plan, indent=2))
            if not a.live:
                print("\nDry run. Nothing was written. Add --live to write to monday.", file=sys.stderr)
        elif a.cmd == "log":
            for e in store.log.entries():
                print(json.dumps(e))
    except (TransitionError, md.SyncRefused, KeyError) as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    return 0
