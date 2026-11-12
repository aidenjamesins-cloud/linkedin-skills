"""Local mirror of the monday Leads board scoring formulas.

The board computes the score itself once an item exists; this mirror exists so
leads can be ranked and filtered *before* anything reaches monday. The tables
below are transcribed from the board's formula columns (Wealth Pts,
Business/Person Pts, Intent Pts, Risk Adj, Total Score, Priority Tier (calc)).
`tests/test_outreach_scoring.py` re-parses the captured formula text and fails
if a number here drifts from the board.
"""
from __future__ import annotations

import datetime as _dt
from typing import Optional

from .schemas import Lead, Score

ASSETS_PTS = {"$30M+": 20, "$5M-$30M": 20, "$1M-$5M": 13, "$500K-$1M": 6}
REVENUE_PTS = {"$10M+": 14, "$3M-$10M": 12, "$1M-$3M": 9, "$500K-$1M": 6, "$250K-$500K": 3}

ENTITY_PTS = {"S-corp": 6, "Partnership/PLLC": 6, "Professional practice": 6,
              "LLC": 5, "C-corp": 5, "Single-member LLC": 3, "Sole prop": 3}
ENTITY_DEFAULT = 1
AGE_PTS = {"45-54": 8, "55-64": 8, "35-44": 6, "65-74": 5, "<35": 2}
AGE_DEFAULT = 1
PROFESSION_PTS = {"Physician/Dentist": 4, "Attorney": 4, "CPA/Advisor": 4,
                  "Real estate/Developer": 4, "Franchise/Multi-unit": 4,
                  "Family business": 4, "Tech/E-commerce founder": 3,
                  "Contractor/Trades": 3}
PROFESSION_DEFAULT = 2

SOURCE_PTS = {"COI introduction": 15, "Client referral": 15, "Event/Seminar": 9,
              "Webinar": 9, "Inbound web/Calendly": 8, "Outbound reply": 6,
              "Public-record signal": 3, "Purchased list": 1}
TRIGGER_TIER_PTS = {"T1": 15, "T2": 9, "T3": 4}
TRIGGER_DECAY_DAYS = 90
ENGAGEMENT_PTS = {"Meeting booked": 10, "Replied": 6, "Opened/clicked": 3}

RISK_PTS = {"Strong existing advisor": -15, "DNC/opt-out": -20,
            "Outside licensed state": -10, "Low-quality data": -10}

TIER_A = 75
TIER_B = 50
HOLD_DNC = {"National DNC", "Internal DNC"}


def _employee_pts(n: Optional[int]) -> int:
    if n is None:
        return 0
    if n >= 20:
        return 8
    if n >= 5:
        return 6
    if n >= 1:
        return 3
    return 0


def trigger_age_days(lead: Lead, today: Optional[_dt.date] = None) -> int:
    # Board: IF({Trigger Date}, DAYS(TODAY(), {Trigger Date}), 0). No date reads as age 0.
    if not lead.trigger_date:
        return 0
    today = today or _dt.date.today()
    return (today - _dt.date.fromisoformat(lead.trigger_date)).days


def score(lead: Lead, today: Optional[_dt.date] = None) -> Score:
    wealth = ASSETS_PTS.get(lead.assets_band or "", 0) + REVENUE_PTS.get(lead.revenue_band or "", 0)
    business = (
        _employee_pts(lead.employees)
        + ENTITY_PTS.get(lead.entity_type or "", ENTITY_DEFAULT)
        + AGE_PTS.get(lead.age_band or "", AGE_DEFAULT)
        + PROFESSION_PTS.get(lead.profession or "", PROFESSION_DEFAULT)
    )
    trigger = 0
    if trigger_age_days(lead, today) <= TRIGGER_DECAY_DAYS:
        trigger = TRIGGER_TIER_PTS.get((lead.trigger_event or "")[:2], 0)
    intent = (
        SOURCE_PTS.get(lead.provenance.source, 0)
        + trigger
        + ENGAGEMENT_PTS.get(lead.engagement or "", 0)
    )
    risk = RISK_PTS.get(lead.risk_flag or "", 0)
    total = max(0, wealth + business + intent + risk)
    if (lead.dnc_status or "") in HOLD_DNC:
        tier = "HOLD"
    elif total >= TIER_A:
        tier = "A"
    elif total >= TIER_B:
        tier = "B"
    else:
        tier = "C"
    return Score(wealth=wealth, business=business, intent=intent, risk_adj=risk, total=total, tier=tier)
