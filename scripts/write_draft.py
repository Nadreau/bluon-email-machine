"""Create one draft row in the Email Calendar (Status = Ready for Review).

Called by the generator once per email it writes. All content comes from CLI
flags so the agent stays in control of the language.

Example:
  python scripts/write_draft.py \
    --email "Residential: Scan the Nameplate" \
    --audience Residential --engagement Engaged --channel HubSpot \
    --goal Demo --feature "Nameplate Scan" --subject-formula "Speed claim" \
    --subject "Unit Info in 3 Seconds — Just Scan the Nameplate" \
    --preview "Every tech works like a senior, instantly." \
    --cta "Book a Demo" --send-date 2026-06-15 \
    --body "Hook...\n\nAnswer...\n\n- benefit\n- benefit\n\nProof...\n\nBook a Demo →"
"""
import argparse, notion

p = argparse.ArgumentParser()
p.add_argument("--subject", required=True)
p.add_argument("--preview", default="")
p.add_argument("--body", required=True)
p.add_argument("--cta", required=True)
p.add_argument("--cta-url", dest="cta_url", default=None, help="book-a-meeting URL")
p.add_argument("--audience", required=True)
p.add_argument("--engagement", required=True, choices=["Engaged", "Unengaged"])
p.add_argument("--channel", required=True, choices=["HubSpot", "Anevvo"])
p.add_argument("--goal", required=True, choices=["Open", "Demo"])
p.add_argument("--feature", default="")
p.add_argument("--subject-formula", dest="subject_formula", default="")
p.add_argument("--send-date", dest="send_date", default=None)
p.add_argument("--status", default="Ready for Review")
p.add_argument("--notes", default="Auto-drafted by Email Machine. For Pete review.")
a = p.parse_args()

url = notion.create_draft(
    subject=a.subject, preview=a.preview, body=a.body, cta=a.cta, cta_url=a.cta_url,
    audience=a.audience, engagement=a.engagement, channel=a.channel, goal=a.goal,
    feature=a.feature, subject_formula=a.subject_formula, send_date=a.send_date,
    status=a.status, notes=a.notes)
print(f"CREATED: {url}")
