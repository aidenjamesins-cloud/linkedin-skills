"""Append-only action log (JSONL).

One line per decision: timestamp, lead, source, proposed action, approval
status, actor, outcome. Blocks are logged too, so the log answers "why did
this lead never get a draft" as well as "what did we write to monday".
"""
from __future__ import annotations

import json
import pathlib
from typing import Any, Iterator, Optional

from .schemas import now_iso


class ActionLog:
    def __init__(self, path: pathlib.Path):
        self.path = pathlib.Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def write(self, *, lead_id: str, action: str, approval: str, actor: str,
              outcome: str, source: Optional[str] = None, detail: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        entry = {"at": now_iso(), "lead_id": lead_id, "source": source, "action": action,
                 "approval": approval, "actor": actor, "outcome": outcome, "detail": detail or {}}
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
        return entry

    def entries(self) -> Iterator[dict[str, Any]]:
        if not self.path.exists():
            return iter(())
        with self.path.open(encoding="utf-8") as fh:
            return iter([json.loads(line) for line in fh if line.strip()])
