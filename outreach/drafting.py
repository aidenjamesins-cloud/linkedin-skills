"""Voice-matched outreach drafts.

The default drafter is a template drafter: deterministic, offline, and it only
uses facts already on the lead record. It never invents shared context, a
mutual connection, or a result. An LLM drafter can be plugged in through the
`Drafter` protocol; whatever produces the text, `finish()` runs the same lint
and claim check before a draft is shown.

Voice inputs are the bundle's own files: `references/voice-rules.md` (the
vocabulary blacklist and dash rules, applied here as lint) and
`references/voice-profile.md` (read when its status says `filled: yes`).
"""
from __future__ import annotations

import pathlib
import re
from typing import Optional, Protocol

from .compliance import claim_flags
from .schemas import Draft, Lead

ROOT = pathlib.Path(__file__).resolve().parent.parent
VOICE_PROFILE = ROOT / "references" / "voice-profile.md"

# From references/voice-rules.md, "Vocabulary blacklist".
BLACKLIST = [
    "leverage", "utilize", "facilitate", "streamline", "robust", "seamless", "delve",
    "navigate", "unlock", "harness", "foster", "cultivate", "fundamentally",
    "essentially", "ultimately", "crucially", "notably", "landscape", "ecosystem",
    "paradigm", "realm", "tapestry", "journey", "game-changer", "deep dive",
    "at the end of the day", "in today's fast-paced world",
]
PITCH_TELLS = ["quick call", "15 minutes", "hop on a call", "book a time", "limited time", "act now"]

# Neutral topic lines per trigger. They name the decision, not the data point,
# so a message never tells someone what a list said about them.
TRIGGER_TOPICS = {
    "Sale closing": "a business sale changes how coverage and buy-sell planning fit together",
    "LOI/M&A": "the months around a sale are when coverage gaps tend to show up",
    "Partnership/entity": "a new entity is a good moment to set up coverage on purpose rather than by default",
    "Lost group": "moving off a group plan is a decision people often wish they had a second set of eyes on",
    "Peer/partner death": "key-person and buy-sell questions are easier to work through before they are urgent",
    "Estate work": "estate work and coverage decisions tend to go better when they are done together",
    "liquidity event": "a liquidity event usually reshuffles the coverage questions worth asking",
    "pre-Medicare": "the years before Medicare are where individual coverage choices matter most",
    "Divorce": "coverage often needs rebuilding after a big household change",
    "First W-2 hire": "a first hire is when owners start weighing group versus individual options",
    "qualified plan": "a new retirement plan is a natural time to look at the rest of the picture",
    "Revenue/profit": "growth tends to outpace the coverage set up in the early years",
    "OEP": "open enrollment is a short window for a calm comparison",
    "ACA renewal": "renewal notices are a good prompt to compare options clearly",
    "Marriage/new child": "a growing household is a good time to revisit coverage",
    "Real estate purchase": "a new property often raises life and disability questions",
    "Turned 50": "fifty is when a lot of people take a fresh look at life and disability coverage",
    "S-corp": "an S-corp election changes how owner health coverage is handled",
    "400% FPL": "household income near subsidy lines makes plan choice worth a closer look",
}
DEFAULT_TOPIC = "health and life coverage decisions are easier with someone who explains the trade-offs plainly"
EVENT_SOURCES = {"Event/Seminar", "Webinar"}


class Drafter(Protocol):
    def draft(self, lead: Lead, channel: str) -> Draft: ...


def voice_profile_filled(path: pathlib.Path = VOICE_PROFILE) -> bool:
    try:
        return bool(re.search(r"^-\s*filled:\s*yes\b", path.read_text(encoding="utf-8"), re.M | re.I))
    except OSError:
        return False


def topic_for(lead: Lead) -> str:
    trig = lead.trigger_event or ""
    for needle, line in TRIGGER_TOPICS.items():
        if needle.lower() in trig.lower():
            return line
    return DEFAULT_TOPIC


def why_now(lead: Lead) -> str:
    parts = []
    if lead.trigger_event and lead.trigger_event != "No trigger":
        when = f" ({lead.trigger_date})" if lead.trigger_date else ""
        parts.append(f"trigger: {lead.trigger_event}{when}")
    parts.append(f"source: {lead.provenance.source}, {lead.provenance.source_detail}")
    if lead.engagement and lead.engagement != "None":
        parts.append(f"engagement: {lead.engagement}")
    return "; ".join(parts)


def _first(name: str) -> str:
    return name.strip().split()[0]


def _opener(lead: Lead) -> Optional[str]:
    src = lead.provenance.source
    if src in EVENT_SOURCES:
        event = lead.provenance.source_detail.strip().rstrip(".")
        if not event.lower().startswith(("the ", "our ", "my ")):
            event = "the " + event
        return f"Thanks for registering for {event}."
    if src in {"COI introduction", "Client referral"}:
        return "Glad we were introduced."
    if src == "Inbound web/Calendly":
        return "Thanks for reaching out."
    return None


def lint(text: str) -> list[str]:
    issues = []
    low = text.lower()
    for word in BLACKLIST:
        if re.search(r"\b" + re.escape(word) + r"\b", low):
            issues.append(f"blacklisted phrase: {word}")
    for tell in PITCH_TELLS:
        if tell in low:
            issues.append(f"reads as a pitch: {tell}")
    if text.count("\u2014") > max(1, len(text.split()) // 100):
        issues.append("too many em dashes")
    if "–" in text or "--" in text:
        issues.append("en dash or double dash between clauses")
    if low.rstrip().endswith("what do you think?"):
        issues.append("dead closing question")
    return issues


def finish(draft: Draft) -> Draft:
    """Lint and claim-check any draft, however it was produced."""
    text = f"{draft.subject or ''}\n{draft.body}"
    draft.claim_flags = claim_flags(text)
    draft.lint = lint(draft.body)
    return draft.validate()


class TemplateDrafter:
    """Offline drafter. Uses only facts on the lead record."""

    def draft(self, lead: Lead, channel: str) -> Draft:
        first = _first(lead.name)
        opener = _opener(lead)
        topic = topic_for(lead)
        topic_cap = topic[0].upper() + topic[1:]
        if channel == "linkedin_note":
            lines = [f"Hi {first}."]
            if opener:
                lines.append(opener)
            lines.append(f"{topic_cap}. I'm an independent health and life advisor and I'd rather be useful here than pitch.")
            lines.append("Happy to connect.")
            body = " ".join(lines)
            if len(body) > 300:
                body = f"Hi {first}. {topic_cap}. I'm an independent health and life advisor. Happy to connect."
            subject = None
        elif channel == "linkedin_message":
            lines = [f"Hi {first},", ""]
            if opener:
                lines += [opener, ""]
            lines += [
                f"{topic_cap}. I'm an independent advisor, so I can lay options side by side without a carrier to push.",
                "",
                "If it's useful, I can send a one-page checklist of the questions worth asking. No call needed.",
                "",
                "Aiden",
            ]
            body = "\n".join(lines)
            subject = None
        elif channel == "email":
            lines = [f"Hi {first},", ""]
            if opener:
                lines += [opener, ""]
            lines += [
                f"{topic_cap}. I'm an independent health and life advisor, and I keep a short checklist of the questions worth asking at this stage.",
                "",
                "Want me to send it over? A one-word reply is plenty.",
                "",
                "Aiden James",
                "",
                "If you'd rather not hear from me, reply \"stop\" and I won't write again.",
            ]
            body = "\n".join(lines)
            subject = "A short checklist, if useful"
        else:
            raise ValueError(f"unknown channel {channel!r}")
        return finish(Draft(lead_id=lead.id, channel=channel, body=body, subject=subject, why_now=why_now(lead)))


def pick_channel(allowed: list[str]) -> Optional[str]:
    for ch in ("linkedin_note", "email", "linkedin_message"):
        if ch in allowed:
            return ch
    return None
