"""Contracts for the outreach pipeline: schemas, approval gates, dedupe,
compliance flags, scoring parity with the monday board, and the sync adapter.

Offline. No credentials, no network. Seeded fake data only.
"""
from __future__ import annotations

import datetime as dt
import json
import pathlib
import re
import tempfile
import unittest

from outreach import compliance, dedupe, monday_adapter as md, pipeline, scoring
from outreach.drafting import TemplateDrafter, finish, lint
from outreach.ingest import lead_from_row, manual, read_csv
from outreach.schemas import Draft, Lead, LeadRecord, LeadState, Provenance, SchemaError
from outreach.states import HUMAN, TransitionError, approve, mark_sent, transition

ROOT = pathlib.Path(__file__).resolve().parent.parent
FORMULAS = json.loads((ROOT / "tests/fixtures/outreach/leads_board_formulas.json").read_text(encoding="utf-8"))
SAMPLE_CSV = ROOT / "outreach/data/sample_leads.csv"
TODAY = dt.date(2026, 11, 12)
CONFIG = {"board_id": 1, "group_id": "topics", "licensed_states": ["TX", "FL", "GA"], "max_linkedin_per_day": 2}


def lead(**kw) -> Lead:
    base = dict(name="Jordan Sample", company="Sample Dental PLLC", email="jordan@example.com",
                linkedin_url="https://www.linkedin.com/in/jordan-sample", state="TX",
                dnc_status="Clear", consent_type="Email opt-in")
    base.update(kw)
    prov = base.pop("provenance", None) or Provenance(source="Webinar", permission_basis="owned_export",
                                                      source_detail="sample webinar")
    return Lead(provenance=prov, **base).validate()


def drafted(**kw) -> LeadRecord:
    return pipeline.process(lead(**kw), CONFIG, today=TODAY)


class SchemaTests(unittest.TestCase):
    def test_provenance_required(self):
        with self.assertRaises(SchemaError):
            Lead.from_dict({"name": "X"})
        for missing in ("source", "permission_basis", "source_detail"):
            row = {"name": "X", "source": "Webinar", "permission_basis": "owned_export", "source_detail": "list"}
            row.pop(missing)
            with self.assertRaises(SchemaError, msg=missing):
                lead_from_row(row)

    def test_permission_basis_must_be_known(self):
        with self.assertRaises(SchemaError):
            lead_from_row({"name": "X", "source": "LinkedIn", "permission_basis": "scraped", "source_detail": "x"})

    def test_bad_values_rejected(self):
        for kw in ({"email": "nope"}, {"state": "Texas"}, {"dnc_status": "maybe"},
                   {"trigger_date": "11/02/2026"}, {"linkedin_url": "https://example.com/u"}):
            with self.assertRaises(SchemaError, msg=kw):
                lead(**kw)

    def test_round_trip(self):
        rec = drafted()
        again = LeadRecord.from_dict(json.loads(json.dumps(rec.to_dict())))
        self.assertEqual(again.to_dict(), rec.to_dict())

    def test_note_length_cap(self):
        with self.assertRaises(SchemaError):
            Draft(lead_id="x", channel="linkedin_note", body="a" * 301, why_now="").validate()

    def test_csv_bad_row_does_not_stop_batch(self):
        with tempfile.TemporaryDirectory() as d:
            p = pathlib.Path(d) / "x.csv"
            p.write_text("name,source,permission_basis,source_detail\nA,Webinar,owned_export,list\nB,,,\n", encoding="utf-8")
            leads, errors = read_csv(p)
        self.assertEqual([l.name for l in leads], ["A"])
        self.assertEqual(len(errors), 1)


class ScoringParityTests(unittest.TestCase):
    """The local mirror must match the board's formula text, number for number."""

    @staticmethod
    def switch_pairs(formula: str, column: str) -> tuple[dict[str, int], int]:
        m = re.search(r"SWITCH\((?:LEFT\()?\{" + re.escape(column) + r"\}(?:,2\))?,(.*?)\)", formula)
        assert m, column
        parts = re.findall(r'"([^"]*)",(-?\d+)|,?(-?\d+)$', m.group(1))
        table = {lab: int(n) for lab, n, _ in parts if lab}
        default = [int(d) for _, _, d in parts if d]
        return table, default[0] if default else 0

    def check(self, formula_name, column, table, default=0):
        got, got_default = self.switch_pairs(FORMULAS[formula_name], column)
        self.assertEqual(got, table, f"{formula_name} {column}")
        self.assertEqual(got_default, default, f"{formula_name} {column} default")

    def test_tables_match_board(self):
        self.check("Wealth Pts", "color_mm915e5m", scoring.ASSETS_PTS)
        self.check("Wealth Pts", "color_mm91hzyh", scoring.REVENUE_PTS)
        self.check("Business/Person Pts", "color_mm91g8s8", scoring.ENTITY_PTS, scoring.ENTITY_DEFAULT)
        self.check("Business/Person Pts", "color_mm916q7n", scoring.AGE_PTS, scoring.AGE_DEFAULT)
        self.check("Business/Person Pts", "color_mm91dq", scoring.PROFESSION_PTS, scoring.PROFESSION_DEFAULT)
        self.check("Intent Pts", "color_mkyb8krc", scoring.SOURCE_PTS)
        self.check("Intent Pts", "color_mm91r9k6", scoring.TRIGGER_TIER_PTS)
        self.check("Intent Pts", "color_mm918kse", scoring.ENGAGEMENT_PTS)
        self.check("Risk Adj", "color_mm91vwhv", scoring.RISK_PTS)
        self.assertIn(f">{scoring.TRIGGER_DECAY_DAYS},0", FORMULAS["Intent Pts"])
        tier = FORMULAS["Priority Tier (calc)"]
        self.assertIn(f">={scoring.TIER_A},\"A\"", tier)
        self.assertIn(f">={scoring.TIER_B},\"B\"", tier)
        for status in scoring.HOLD_DNC:
            self.assertIn(f'"{status}"', tier)

    def test_worked_example(self):
        s = scoring.score(lead(assets_band="$1M-$5M", revenue_band="$1M-$3M", employees=12,
                               entity_type="Partnership/PLLC", age_band="45-54", profession="Physician/Dentist",
                               trigger_event="T1 - Lost group coverage", trigger_date="2026-10-20",
                               engagement="Replied"), TODAY)
        self.assertEqual((s.wealth, s.business, s.intent, s.total, s.tier), (22, 24, 30, 76, "A"))

    def test_trigger_decays_after_90_days(self):
        fresh = scoring.score(lead(trigger_event="T1 - Estate work started", trigger_date="2026-10-01"), TODAY)
        stale = scoring.score(lead(trigger_event="T1 - Estate work started", trigger_date="2026-07-01"), TODAY)
        self.assertEqual(fresh.intent - stale.intent, 15)

    def test_dnc_is_hold_and_floor_is_zero(self):
        self.assertEqual(scoring.score(lead(dnc_status="Internal DNC"), TODAY).tier, "HOLD")
        self.assertEqual(scoring.score(lead(risk_flag="DNC/opt-out", provenance=Provenance(
            source="Aged lead", permission_basis="owned_export", source_detail="x")), TODAY).total, 0)


class ComplianceTests(unittest.TestCase):
    def reasons(self, **kw):
        return compliance.check(lead(**kw), CONFIG["licensed_states"]).reasons

    def test_hard_blocks(self):
        self.assertTrue(self.reasons(dnc_status="National DNC"))
        self.assertTrue(self.reasons(dnc_status="Internal DNC"))
        self.assertTrue(self.reasons(opt_out_date="2026-01-01"))
        self.assertTrue(self.reasons(risk_flag="DNC/opt-out"))
        self.assertTrue(self.reasons(state="CA"))
        self.assertTrue(self.reasons(state=None))
        self.assertTrue(compliance.check(lead(), []).blocked, "no licensed states must block everything")
        self.assertFalse(self.reasons())

    def test_blocked_lead_gets_no_draft(self):
        rec = drafted(dnc_status="National DNC")
        self.assertEqual(rec.state, LeadState.BLOCKED_COMPLIANCE)
        self.assertIsNone(rec.draft)

    def test_email_needs_opt_in_or_lawful_basis(self):
        no = compliance.check(lead(consent_type="None", linkedin_url=None), ["TX"])
        self.assertTrue(no.blocked)
        self.assertNotIn("email", no.allowed_channels)
        yes = compliance.check(lead(consent_type="None", linkedin_url=None,
                                    lawful_basis_note="existing client asked for a review"), ["TX"])
        self.assertIn("email", yes.allowed_channels)

    def test_phone_never_a_channel(self):
        res = compliance.check(lead(phone="555-010-0101", consent_type="Express written (TCPA)"), ["TX"],
                               channels=["linkedin_note", "email", "sms", "call"])
        self.assertEqual(set(res.allowed_channels) - {"linkedin_note", "email"}, set())

    def test_purchased_list_flags_consent(self):
        res = compliance.check(lead(provenance=Provenance(source="Purchased list", permission_basis="owned_export",
                                                          source_detail="x")), ["TX"])
        self.assertTrue(any("verify consent" in f for f in res.flags))

    def test_claim_flags(self):
        for text, label in [("You could save 30% on premiums", "savings claim"),
                            ("guaranteed approval", "guarantee"),
                            ("the lowest rates in Texas", "superlative rate claim"),
                            ("pre-existing conditions are covered", "coverage claim"),
                            ("you'll qualify easily", "underwriting claim"),
                            ("premiums are tax-deductible", "tax claim"),
                            ("fully HIPAA compliant", "legal or compliance claim"),
                            ("My client Sarah saved a lot", "possible identifiable client: anonymize or confirm consent")]:
            self.assertIn(label, compliance.claim_flags(text), text)
        self.assertEqual(compliance.claim_flags("Happy to connect."), [])


class DedupeTests(unittest.TestCase):
    def test_variants_match(self):
        a = lead()
        for b in (lead(email="Jordan@Example.com", linkedin_url=None, company=None),
                  lead(email=None, linkedin_url="linkedin.com/in/Jordan-Sample/?trk=x", company=None),
                  lead(email=None, linkedin_url=None, phone=None, company="Sample Dental, PLLC"),
                  lead(email=None, linkedin_url=None, company=None, phone="+1 (555) 010-0101")):
            if b.phone:
                a = lead(phone="555.010.0101")
            self.assertIsNotNone(dedupe.find_duplicate(b, [a]), b)

    def test_gmail_dots_and_plus(self):
        self.assertEqual(dedupe.norm_email("Jor.Dan+leads@googlemail.com"), "jordan@gmail.com")

    def test_distinct_people_do_not_match(self):
        a = lead()
        b = lead(name="Riley Example", company="Example Builders", email="riley@example.org",
                 linkedin_url="https://linkedin.com/in/riley-example")
        self.assertIsNone(dedupe.find_duplicate(b, [a]))

    def test_ingest_skips_duplicates_across_runs(self):
        with tempfile.TemporaryDirectory() as d:
            store = pipeline.Store(pathlib.Path(d))
            leads, _ = read_csv(SAMPLE_CSV)
            first = pipeline.ingest(leads, store, CONFIG, today=TODAY)
            self.assertEqual(len(first["duplicates"]), 1)      # the seeded repeat row
            again, _ = read_csv(SAMPLE_CSV)
            second = pipeline.ingest(again, store, CONFIG, today=TODAY)
            self.assertEqual(second["added"], [])
            self.assertEqual(len(store.load()), len(first["added"]))


class ApprovalGateTests(unittest.TestCase):
    def test_system_cannot_approve_or_mark_sent(self):
        rec = drafted()
        self.assertEqual(rec.state, LeadState.DRAFTED)
        with self.assertRaises(TransitionError):
            approve(rec, actor="system")
        with self.assertRaises(TransitionError):
            transition(rec, LeadState.APPROVED, actor="agent")
        approve(rec, actor=HUMAN)
        rec.state = LeadState.SYNCED
        with self.assertRaises(TransitionError):
            mark_sent(rec, actor="system")

    def test_illegal_jumps(self):
        rec = drafted()
        with self.assertRaises(TransitionError):
            transition(rec, LeadState.SYNCED, actor="system")
        with self.assertRaises(TransitionError):
            transition(rec, LeadState.SENT_MANUALLY, actor=HUMAN)

    def test_sync_refuses_unapproved(self):
        client = md.FakeMondayClient()
        for state in LeadState:
            if state == LeadState.APPROVED:
                continue
            rec = drafted()
            rec.state = state
            with self.assertRaises(md.SyncRefused, msg=state):
                md.sync(rec, client, CONFIG, live=True)
        self.assertEqual(client.items, {})

    def test_sync_refuses_forged_approval(self):
        rec = drafted()
        rec.state = LeadState.APPROVED
        rec.approved_by = "system"
        with self.assertRaises(md.SyncRefused):
            md.sync(rec, md.FakeMondayClient(), CONFIG, live=True)

    def test_sync_rechecks_compliance(self):
        rec = drafted()
        approve(rec, actor=HUMAN)
        rec.lead.dnc_status = "Internal DNC"
        with self.assertRaises(md.SyncRefused):
            md.sync(rec, md.FakeMondayClient(), CONFIG, live=True)

    def test_daily_linkedin_cap(self):
        with tempfile.TemporaryDirectory() as d:
            store = pipeline.Store(pathlib.Path(d))
            people = [lead(name=f"Person {i}", email=f"p{i}@example.com", company=f"Co {i}",
                           linkedin_url=f"https://linkedin.com/in/p{i}") for i in range(3)]
            pipeline.ingest(people, store, CONFIG, today=TODAY)
            ids = list(store.load())
            pipeline.approve_lead(store, ids[0], CONFIG)
            pipeline.approve_lead(store, ids[1], CONFIG)
            with self.assertRaises(TransitionError):
                pipeline.approve_lead(store, ids[2], CONFIG)


class MondayAdapterTests(unittest.TestCase):
    def approved(self, **kw):
        rec = drafted(**kw)
        approve(rec, actor=HUMAN)
        return rec

    def test_dry_run_writes_nothing(self):
        client = md.FakeMondayClient()
        plan = md.sync(self.approved(), client, CONFIG)
        self.assertFalse(plan["live"])
        self.assertEqual(client.items, {})

    def test_live_creates_then_updates(self):
        client = md.FakeMondayClient()
        first = md.sync(self.approved(), client, CONFIG, live=True)
        self.assertEqual(first["action"], "created")
        item = client.items[first["item_id"]]
        self.assertEqual(item["columns"]["lead_status"], {"label": "New Lead"})
        self.assertIn("Not sent", client.updates[0][1])
        client.change_columns(1, first["item_id"], {"lead_status": {"label": "Discovery Booked"}})
        second = md.sync(self.approved(email="JORDAN@example.com"), client, CONFIG, live=True)
        self.assertEqual(second["action"], "updated")
        self.assertEqual(len(client.items), 1)
        self.assertEqual(client.items[first["item_id"]]["columns"]["lead_status"], {"label": "Discovery Booked"},
                         "a re-sync must not reset status")

    def test_never_writes_formula_columns(self):
        cv = md.column_values(lead(assets_band="$1M-$5M", trigger_event="T1 - Lost group coverage"))
        self.assertFalse([k for k in cv if k.startswith("formula_")])
        self.assertTrue(set(cv) <= set(md.DEFAULT_COLUMNS.values()))

    def test_contacted_only_after_human_sent(self):
        client = md.FakeMondayClient()
        rec = self.approved()
        md.sync(rec, client, CONFIG, live=True)
        with self.assertRaises(md.SyncRefused):
            md.mark_contacted(rec, client, CONFIG, live=True)
        mark_sent(rec, actor=HUMAN)
        md.mark_contacted(rec, client, CONFIG, live=True, today=TODAY)
        cols = client.items[rec.monday_item_id]["columns"]
        self.assertEqual(cols["lead_status"], {"label": "Contacted"})
        self.assertEqual(cols["date_mm91frvm"], {"date": "2026-11-12"})


class DraftingTests(unittest.TestCase):
    def test_template_drafts_are_clean(self):
        for ch in ("linkedin_note", "linkedin_message", "email"):
            d = TemplateDrafter().draft(lead(trigger_event="T2 - First W-2 hire"), ch)
            self.assertEqual(d.lint, [], ch)
            self.assertEqual(d.claim_flags, [], ch)
        email = TemplateDrafter().draft(lead(), "email")
        self.assertIn("stop", email.body.lower(), "email needs an opt-out line")

    def test_lint_catches_voice_and_pitch(self):
        self.assertTrue(lint("Let's leverage synergies. Quick call?"))
        self.assertTrue(lint("Great post -- what do you think?"))

    def test_finish_flags_llm_text(self):
        d = finish(Draft(lead_id="x", channel="linkedin_message", why_now="",
                         body="I can guarantee you'll save on premiums."))
        self.assertIn("guarantee", d.claim_flags)
        self.assertIn("savings claim", d.claim_flags)


class SeedDataTests(unittest.TestCase):
    def test_seed_rows_are_obviously_fake(self):
        text = SAMPLE_CSV.read_text(encoding="utf-8")
        for email in re.findall(r"[\w.+-]+@([\w.-]+)", text):
            self.assertRegex(email.lower(), r"^example\.(com|org|net)$")
        for phone in re.findall(r"\(?\d{3}\)?[ -.]?\d{3}-\d{4}", text):
            self.assertIn("555", phone)


if __name__ == "__main__":
    unittest.main()
