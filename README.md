# Bluon Email Machine

Cloud automation that drafts Bluon's weekly marketing emails and drops them into
the Notion **Email Calendar** as `Ready for Review`. Runs **Claude Code (Opus) on a
Max subscription via OAuth** inside GitHub Actions — **no Anthropic API key, no
per-token billing**. Human-in-the-loop: Pete reviews every draft before it ships.

```
GitHub Actions (weekly cron / manual)
        │  Claude Code (Opus) via OAuth — runs PROMPT.md
        ▼
  scripts/get_context.py ──reads──► Notion "Email Content Intelligence" guide + Email Calendar
        │
        ▼  generates drafts per the guide (§3 formulas, §4 skeleton, §6 templates, §9 rules)
  scripts/write_draft.py ──writes──► new Email Calendar rows, Status = Ready for Review
        ▼
  Pete reviews in Notion ──► Approved ──► sent (HubSpot engaged / Anevvo unengaged)
```

## What it produces each run
One draft per active audience (full week, all segments):
Residential·Engaged, Commercial·Engaged, ServiceTitan·Engaged (HubSpot, Demo) +
Prospecting·Unengaged (Anevvo, Open). HousecallPro is skipped (rarely/never email).
It never sends — it only writes drafts for review.

## One-time setup

1. **Push this repo to GitHub** (private), e.g. `github.com/Nadreau/bluon-email-machine`.

2. **Generate a Claude OAuth token** (needs a Pro/Max subscription) on your machine:
   ```bash
   claude setup-token
   ```
   Copy the `sk-ant-oat01-…` token it prints (valid ~1 year).

3. **Create a Notion internal integration token** that can read/write the Email
   Machine pages: notion.so/my-integrations → New integration → copy the secret.
   Then in Notion, share the **Email Machine** page (and its Email Calendar +
   Email Content Intelligence children) with that integration.
   *(The existing Bluon integration token already has access and can be reused.)*

4. **Add two GitHub repo secrets** (Settings → Secrets and variables → Actions):
   | Secret name | Value |
   |---|---|
   | `CLAUDE_CODE_OAUTH_TOKEN` | the token from step 2 |
   | `NOTION_BLUON_TOKEN` | the Notion integration token from step 3 |

5. **Run it.** Actions tab → "Email Machine — Weekly Drafts" → *Run workflow*
   (or wait for the Sunday cron). Drafts appear in the Email Calendar.

## Run locally (test without CI)
```bash
export NOTION_TOKEN=…           # the Notion integration token
python scripts/get_context.py   # prints the guide + existing rows
python scripts/write_draft.py --help
```

## Files
- `.github/workflows/weekly-drafts.yml` — the schedule + Claude Code action (OAuth).
- `PROMPT.md` — the weekly instructions Claude follows.
- `scripts/notion.py` — Notion read/write helpers (IDs hard-wired to the Email Machine).
- `scripts/get_context.py` — prints guide + calendar for the generator.
- `scripts/write_draft.py` — creates one `Ready for Review` draft row.

## Guardrails
- Drafts only — no send/publish anywhere. Pete is the required review step.
- Notion token reads the guide and creates calendar drafts; it does not delete.
- Change cadence in the workflow `cron`. Change the audience set / rules in `PROMPT.md`.
- The guide is the brain: edit **Email Content Intelligence** in Notion and the next
  run picks up the changes automatically — no code change needed.
