# outreach: lead sourcing and outreach, synced to monday.com

A local, approval-first pipeline for Aiden James Health. It takes leads from
sources you're allowed to use, drops duplicates, scores them the way the monday
**CRM > Leads** board does, checks compliance, and drafts outreach in your voice.
Only after you approve a draft does it write the lead to monday.

**It never sends anything.** LinkedIn notes and emails are sent by you, by hand.
The tool only records that you sent them.

```
ingest -> dedupe -> score -> compliance -> draft -> YOU APPROVE -> monday sync -> YOU SEND -> Contacted
```

## Guardrails it enforces

| Rule | Where |
|---|---|
| Every lead must say where it came from (`source`, `permission_basis`, `source_detail`) or it is rejected | `schemas.py`, `ingest.py` |
| No scraping. There's no fetcher at all, so a LinkedIn URL is stored only when you supply it | `ingest.py` |
| DNC, opt-out, unlicensed or unknown state: no draft, no sync | `compliance.py` |
| Purchased, data-provider and aged lists get a "verify consent" flag | `compliance.py` |
| Email only with an opt-in or a written lawful-basis note; every email gets a stop line | `compliance.py`, `drafting.py` |
| Phone and SMS are never drafted (TCPA) | `compliance.py` |
| Savings, guarantee, rate, coverage, carrier, underwriting, tax and legal language is flagged on the card | `compliance.py` |
| Only a human can approve, mark sent, or close a lead | `states.py` |
| Daily cap on approved LinkedIn drafts (default 15) | `pipeline.py` |
| monday writes refuse anything not approved, re-check compliance, and default to dry run | `monday_adapter.py` |
| Formula columns are never written; monday computes the score | `monday_adapter.py` |
| Every decision, including blocks and duplicates, goes to an append-only log | `action_log.py` |

## Setup

Python 3.10+. Dry runs need no packages. Live monday writes need `requests`
(already in the bundle's `requirements.txt`).

```bash
cp outreach/config.example.json outreach/config.json   # gitignored
# check licensed_states (TX, FL, GA, VA, MO, OH as of 2026-11-12)
```

For live writes, add a monday API token (monday > your avatar > Developers >
My access tokens) to `.env`:

```
MONDAY_API_TOKEN=...
```

Lead data and the action log live in `outreach/.data/` (gitignored). Nothing
in this folder sends lead data anywhere except the monday writes you ask for.

## Daily use

```bash
python -m outreach ingest outreach/data/sample_leads.csv   # seeded fake data
python -m outreach brief                 # your three most useful actions
python -m outreach show <id>             # review card: score, why now, claim check, flags, draft
python -m outreach approve <id>          # or: approve <id> --edit my-version.txt
python -m outreach sync <id>             # dry run: prints the exact monday write
python -m outreach sync <id> --live      # creates or updates the item in New Leads
python -m outreach sent <id> --live      # after you send it: Status -> Contacted, contact dates set
python -m outreach replied <id> / qualified <id> / reject <id> / irrelevant <id>
python -m outreach log
```

### CSV columns

Required: `name`, `source`, `permission_basis`, `source_detail`.
`source` uses the Leads board's Lead Source labels (Webinar, COI introduction,
Purchased list, ...). `permission_basis` is one of `user_provided`,
`owned_export`, `public_record`, `referral`, `inbound`.

Optional, using the board's labels: `company`, `title`, `email`, `phone`,
`linkedin_url`, `state`, `entity_type`, `profession`, `revenue_band`,
`assets_band`, `age_band`, `employees`, `trigger_event`, `trigger_date`,
`engagement`, `existing_advisor`, `risk_flag`, `consent_type`, `consent_date`,
`dnc_status`, `opt_out_date`, `lawful_basis_note`, `notes`. See
`outreach/data/sample_leads.csv`.

## What it writes to monday

Board `18439617620` (CRM > Leads), group New Leads. Input columns only:
company, title, email, phone, Lead Source, Source Detail (with the LinkedIn URL
appended, since the board has no LinkedIn column), the scoring bands, trigger
and date, engagement, existing advisor, risk flag, consent, DNC, opt-out,
employees, State, and Status = New Lead. The approved draft is posted as an
update on the item marked "Not sent". A re-sync finds the item by email or phone
and updates it without resetting Status. Column ids are in
`monday_adapter.DEFAULT_COLUMNS`; override them in `config.json` under
`columns`.

## Drafting

`TemplateDrafter` is offline and uses only facts on the record: no invented
mutual connections, results or shared history. Trigger events turn into a
neutral topic line ("moving off a group plan is a decision people often wish
they had a second set of eyes on"), so a message never tells someone what a list
said about them. Drafts are linted against `references/voice-rules.md` (the
vocabulary blacklist, dash rules, dead closing questions) plus pitch tells.

To use a model instead, implement the `Drafter` protocol in `drafting.py` and
pass it to `pipeline.ingest(..., drafter=...)`. Wrap its output in `finish()`
so the same lint and claim check run. Feed it `references/voice-profile.md`
and `references/story-bank.md` once they're filled, and route the text through
`linkedin-humanizer` before approval.

## Tests

```bash
python3 -m unittest tests.test_outreach
```

They cover: provenance and field validation; scoring parity (the local tables
are re-parsed from the board's own formula text in
`tests/fixtures/outreach/leads_board_formulas.json`, so a board change fails the
test until the mirror is updated); the 90-day trigger decay and HOLD tier; every
hard block and channel gate; claim flags; dedupe across email, Gmail dot/plus
variants, phone formats, LinkedIn URL variants and name + company; the approval
gate (system actors can't approve or mark sent, illegal jumps, forged approvals,
compliance re-check at sync, daily cap); the monday adapter (dry run writes
nothing, create then update, no status reset, no formula columns, Contacted only
after a human records the send); draft lint; and a check that seed data uses only
`example.*` emails and 555 numbers.

## Not built yet

Follow-up reminders from Next Follow-up, cost-per-qualified-lead rollups into
Sources & Campaigns, and reply tracking from the november pipeline board.
