"""Print everything the generator needs in one read:
the Email Content Intelligence guide + the current Email Calendar rows +
what's recently gone out in HubSpot (so we don't repeat topics).

Usage: python scripts/get_context.py
"""
import os, json, urllib.request
import notion


def recent_hubspot_sends(days=28, limit=40):
    """Subjects of emails recently published/sent in HubSpot — so the AI varies
    messaging and doesn't reuse a topic that just went out. (Per-audience
    attribution isn't reliable — lists shift — so this is topic-level awareness.)"""
    tok = os.environ.get("HUBSPOT_TOKEN", "").strip()
    if not tok:
        try:
            tok = open(os.path.expanduser("~/.config/hubspot/api_key")).read().strip()
        except Exception:
            return []
    try:
        req = urllib.request.Request(
            "https://api.hubapi.com/marketing/v3/emails?limit=100&sort=-updatedAt",
            headers={"Authorization": f"Bearer {tok}"})
        with urllib.request.urlopen(req, timeout=30) as r:
            data = json.load(r)
        out = []
        for e in data.get("results", []):
            if e.get("state") in ("PUBLISHED", "SENT", "AUTOMATED_SENDING", "SCHEDULED", "SCHEDULED_AB"):
                out.append((str(e.get("publishDate") or e.get("updatedAt", ""))[:10],
                            e.get("subject") or e.get("name", "")))
        return out[:limit]
    except Exception:
        return []


print("=" * 70)
print("EMAIL CONTENT INTELLIGENCE GUIDE")
print("=" * 70)
print(notion.get_guide_text())

print()
print("=" * 70)
print("RECENTLY SENT IN HUBSPOT — do NOT repeat these topics/angles; vary week-over-week")
print("=" * 70)
sends = recent_hubspot_sends()
if not sends:
    print("(none found / no HubSpot access)")
for d, subj in sends:
    print(f"- {d} | {subj}")

print()
print("=" * 70)
print("EXISTING EMAIL CALENDAR ROWS (avoid duplicating these)")
print("=" * 70)
rows = notion.get_calendar_rows()
if not rows:
    print("(none yet)")
for r in rows:
    flags = ("approved" if r["approved"] else "draft") + (", done" if r["done"] else "")
    print(f"- [{flags}] {r['audience']} / {r['engagement']} | {r['send_date']} | {r['name']}")
