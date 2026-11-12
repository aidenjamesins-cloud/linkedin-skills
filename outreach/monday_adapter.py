"""monday.com sync adapter for the CRM > Leads board.

Rules this module enforces, not just documents:

- It refuses any record that is not APPROVED (or, for the contacted update,
  SENT_MANUALLY), and re-runs the compliance check at write time.
- Dry run is the default. `live=True` is needed to write.
- It writes input columns only. The board's formula columns (Wealth Pts,
  Total Score, Priority Tier (calc), ...) compute the score themselves.
- It looks the lead up by email and phone before creating, so a re-run
  updates the existing item instead of making a duplicate.

The client is an interface, so tests use `FakeMondayClient` and another CRM can
be added later without touching the pipeline.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
from typing import Any, Optional, Protocol

from . import compliance
from .dedupe import norm_email, norm_phone
from .schemas import Lead, LeadRecord, LeadState
from .states import HUMAN, transition

API_URL = "https://api.monday.com/v2"
API_VERSION = "2025-04"

# Column ids on the Leads board (read 2026-11-12). Override in config.json if the board changes.
DEFAULT_COLUMNS = {
    "status": "lead_status",
    "company": "lead_company",
    "title": "text",
    "email": "lead_email",
    "phone": "lead_phone",
    "source": "color_mkyb8krc",
    "source_detail": "text_mm91f033",
    "entity_type": "color_mm91g8s8",
    "profession": "color_mm91dq",
    "revenue_band": "color_mm91hzyh",
    "assets_band": "color_mm915e5m",
    "age_band": "color_mm916q7n",
    "trigger_event": "color_mm91r9k6",
    "trigger_date": "date_mm91jn2g",
    "engagement": "color_mm918kse",
    "existing_advisor": "color_mm91q73e",
    "risk_flag": "color_mm91vwhv",
    "consent_type": "color_mm91yme5",
    "consent_date": "date_mm91bq9t",
    "dnc_status": "color_mm91jdqf",
    "opt_out_date": "date_mm915kd7",
    "employees": "numeric_mm914f1d",
    "state": "dropdown_mm91tf68",
    "first_contact": "date_mm91frvm",
    "last_interaction": "date__1",
}
STATUS_COLUMNS = {"source", "entity_type", "profession", "revenue_band", "assets_band", "age_band",
                  "trigger_event", "engagement", "existing_advisor", "risk_flag", "consent_type", "dnc_status"}
DATE_COLUMNS = {"trigger_date", "consent_date", "opt_out_date"}


class SyncRefused(RuntimeError):
    pass


class MondayClient(Protocol):
    def find_items(self, board_id: int, column_id: str, value: str) -> list[str]: ...
    def create_item(self, board_id: int, group_id: str, name: str, column_values: dict[str, Any]) -> str: ...
    def change_columns(self, board_id: int, item_id: str, column_values: dict[str, Any]) -> None: ...
    def create_update(self, item_id: str, body: str) -> None: ...


def column_values(lead: Lead, columns: dict[str, str] = DEFAULT_COLUMNS) -> dict[str, Any]:
    cv: dict[str, Any] = {}
    for field in ("company", "title"):
        if getattr(lead, field):
            cv[columns[field]] = getattr(lead, field)
    detail = lead.provenance.source_detail
    if lead.linkedin_url:
        detail += f" | {lead.linkedin_url}"
    cv[columns["source_detail"]] = detail
    if lead.email:
        cv[columns["email"]] = {"email": lead.email, "text": lead.email}
    if lead.phone:
        cv[columns["phone"]] = {"phone": norm_phone(lead.phone) or lead.phone, "countryShortName": "US"}
    for field in STATUS_COLUMNS:
        value = lead.provenance.source if field == "source" else getattr(lead, field)
        if value:
            cv[columns[field]] = {"label": value}
    for field in DATE_COLUMNS:
        if getattr(lead, field):
            cv[columns[field]] = {"date": getattr(lead, field)}
    if lead.employees is not None:
        cv[columns["employees"]] = str(lead.employees)
    if lead.state:
        cv[columns["state"]] = {"labels": [lead.state]}
    return cv


def _recheck(rec: LeadRecord, config: dict[str, Any]) -> None:
    result = compliance.check(rec.lead, config.get("licensed_states", []))
    if result.blocked:
        raise SyncRefused(f"{rec.lead.id}: compliance now blocks this lead: {'; '.join(result.reasons)}")


def _existing_item(client: MondayClient, board_id: int, lead: Lead, columns: dict[str, str]) -> Optional[str]:
    if lead.email:
        hits = client.find_items(board_id, columns["email"], lead.email)
        if hits:
            return hits[0]
    if lead.phone and (p := norm_phone(lead.phone)):
        hits = client.find_items(board_id, columns["phone"], p)
        if hits:
            return hits[0]
    return None


def sync(rec: LeadRecord, client: Optional[MondayClient], config: dict[str, Any], *, live: bool = False,
         log=None, today: Optional[_dt.date] = None) -> dict[str, Any]:
    """Write one approved lead to monday. Returns the plan (dry run) or what was written."""
    if rec.state != LeadState.APPROVED:
        raise SyncRefused(f"{rec.lead.id}: state is {rec.state.value}; only approved leads sync")
    if rec.approved_by != HUMAN:
        raise SyncRefused(f"{rec.lead.id}: approval was not recorded from a human")
    _recheck(rec, config)
    board_id = int(config["board_id"])
    columns = {**DEFAULT_COLUMNS, **config.get("columns", {})}
    cv = column_values(rec.lead, columns)
    cv[columns["status"]] = {"label": config.get("new_status", "New Lead")}
    note = (f"Outreach draft approved {rec.approved_at} ({rec.draft.channel}). Not sent: send it by hand.\n\n"
            + (f"Subject: {rec.draft.subject}\n\n" if rec.draft.subject else "") + rec.draft.body)
    plan: dict[str, Any] = {"board_id": board_id, "group_id": config.get("group_id", "topics"),
                            "item_name": rec.lead.name, "column_values": cv, "update": note, "live": live}
    if not live:
        if log:
            log.write(lead_id=rec.lead.id, source=rec.lead.provenance.source, action="monday.sync",
                      approval="approved", actor="system", outcome="dry_run", detail={"plan": plan})
        return plan
    if client is None:
        raise SyncRefused("live sync needs a monday client (set MONDAY_API_TOKEN)")
    item_id = _existing_item(client, board_id, rec.lead, columns)
    if item_id:
        cv.pop(columns["status"], None)   # never reset the status of an item that already moved on
        client.change_columns(board_id, item_id, cv)
        plan["action"] = "updated"
    else:
        item_id = client.create_item(board_id, plan["group_id"], rec.lead.name, cv)
        plan["action"] = "created"
    client.create_update(item_id, note)
    rec.monday_item_id = item_id
    transition(rec, LeadState.SYNCED, actor="system", note=f"monday item {item_id}")
    plan["item_id"] = item_id
    if log:
        log.write(lead_id=rec.lead.id, source=rec.lead.provenance.source, action="monday.sync",
                  approval="approved", actor="system", outcome=plan["action"], detail={"item_id": item_id})
    return plan


def mark_contacted(rec: LeadRecord, client: Optional[MondayClient], config: dict[str, Any], *, live: bool = False,
                   log=None, today: Optional[_dt.date] = None) -> dict[str, Any]:
    """After Aiden sends by hand: set Status to Contacted and the contact dates."""
    if rec.state != LeadState.SENT_MANUALLY:
        raise SyncRefused(f"{rec.lead.id}: record it as sent first (state is {rec.state.value})")
    if not rec.monday_item_id:
        raise SyncRefused(f"{rec.lead.id}: no monday item yet; sync it first")
    columns = {**DEFAULT_COLUMNS, **config.get("columns", {})}
    day = (today or _dt.date.today()).isoformat()
    cv = {columns["status"]: {"label": config.get("contacted_status", "Contacted")},
          columns["first_contact"]: {"date": day}, columns["last_interaction"]: {"date": day}}
    plan = {"item_id": rec.monday_item_id, "column_values": cv, "live": live}
    if live:
        if client is None:
            raise SyncRefused("live sync needs a monday client (set MONDAY_API_TOKEN)")
        client.change_columns(int(config["board_id"]), rec.monday_item_id, cv)
    if log:
        log.write(lead_id=rec.lead.id, source=rec.lead.provenance.source, action="monday.contacted",
                  approval="human_confirmed_sent", actor=HUMAN, outcome="written" if live else "dry_run", detail=plan)
    return plan


# ---------------------------------------------------------------- clients

class FakeMondayClient:
    """In-memory board for tests and demos."""

    def __init__(self) -> None:
        self.items: dict[str, dict[str, Any]] = {}
        self.updates: list[tuple[str, str]] = []
        self._next = 1000

    def find_items(self, board_id: int, column_id: str, value: str) -> list[str]:
        out = []
        for iid, item in self.items.items():
            cell = item["columns"].get(column_id)
            raw = cell.get("email") or cell.get("phone") if isinstance(cell, dict) else cell
            if raw is None:
                continue
            if norm_email(str(raw)) == norm_email(value) or (norm_phone(str(raw)) and norm_phone(str(raw)) == norm_phone(value)):
                out.append(iid)
        return out

    def create_item(self, board_id: int, group_id: str, name: str, column_values: dict[str, Any]) -> str:
        self._next += 1
        iid = str(self._next)
        self.items[iid] = {"board_id": board_id, "group_id": group_id, "name": name, "columns": dict(column_values)}
        return iid

    def change_columns(self, board_id: int, item_id: str, column_values: dict[str, Any]) -> None:
        self.items[item_id]["columns"].update(column_values)

    def create_update(self, item_id: str, body: str) -> None:
        self.updates.append((item_id, body))


class HttpMondayClient:
    """GraphQL client. Needs MONDAY_API_TOKEN; only constructed for live runs."""

    def __init__(self, token: Optional[str] = None, timeout: int = 30) -> None:
        self.token = token or os.environ.get("MONDAY_API_TOKEN")
        if not self.token:
            raise SyncRefused("MONDAY_API_TOKEN is not set")
        self.timeout = timeout

    def _q(self, query: str, variables: dict[str, Any]) -> dict[str, Any]:
        import requests  # lazy: dry runs need no dependencies

        resp = requests.post(API_URL, json={"query": query, "variables": variables}, timeout=self.timeout,
                             headers={"Authorization": self.token, "API-Version": API_VERSION})
        resp.raise_for_status()
        data = resp.json()
        if data.get("errors"):
            raise RuntimeError(f"monday API error: {data['errors']}")
        return data["data"]

    def find_items(self, board_id: int, column_id: str, value: str) -> list[str]:
        q = """query ($b: ID!, $c: String!, $v: String!) {
          items_page_by_column_values(board_id: $b, limit: 5, columns: [{column_id: $c, column_values: [$v]}]) { items { id } }
        }"""
        d = self._q(q, {"b": str(board_id), "c": column_id, "v": value})
        return [i["id"] for i in d["items_page_by_column_values"]["items"]]

    def create_item(self, board_id: int, group_id: str, name: str, column_values: dict[str, Any]) -> str:
        q = """mutation ($b: ID!, $g: String!, $n: String!, $cv: JSON!) {
          create_item(board_id: $b, group_id: $g, item_name: $n, column_values: $cv, create_labels_if_missing: false) { id }
        }"""
        return self._q(q, {"b": str(board_id), "g": group_id, "n": name, "cv": json.dumps(column_values)})["create_item"]["id"]

    def change_columns(self, board_id: int, item_id: str, column_values: dict[str, Any]) -> None:
        q = """mutation ($b: ID!, $i: ID!, $cv: JSON!) {
          change_multiple_column_values(board_id: $b, item_id: $i, column_values: $cv, create_labels_if_missing: false) { id }
        }"""
        self._q(q, {"b": str(board_id), "i": item_id, "cv": json.dumps(column_values)})

    def create_update(self, item_id: str, body: str) -> None:
        q = """mutation ($i: ID!, $body: String!) { create_update(item_id: $i, body: $body) { id } }"""
        self._q(q, {"i": item_id, "body": body})
