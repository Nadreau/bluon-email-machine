"""Rolling 7-day calendar maintenance.

The email calendar runs on a ROLLING basis, not weekly batches: every day the
window advances, yesterday's sends roll off, and a fresh rotation day rolls in,
so Pete/Tanner always see ~the next 7 days of drafts to approve and prep.

Two mechanisms keep it rolling:
  1) A Notion VIEW FILTER ("Send Date is on or after today") makes past rows
     disappear from the approval view automatically — Notion relative-date
     filters re-evaluate daily, so no job is needed for the disappearing.
  2) This script (daily cron) tops up the window: it marks past rows as Sent and
     reports/creates any missing rotation slots for the next 7 days.

The ROTATION is the rule of record — one audience per weekday, so every audience
gets filled at least once across the window. Anevo (capacity-limited) is reserved
for the unengaged slot that needs it.

  python scripts/rolling.py --maintain   # mark past->Sent, render missing mockups, print status (daily)
  python scripts/rolling.py --gaps       # print the (date, audience, engagement) slots missing in the next 7 days
  python scripts/rolling.py --create     # deterministically create drafts for the gaps (fallback if AI step is skipped)
"""
import sys, datetime
import notion

# weekday (Mon=0 .. Sun=6) -> (Audience, Engagement, Channel). Sun is a rest day.
ROTATION = {
    0: ("ServiceTitan", "Engaged",   "HubSpot"),
    1: ("ServiceTitan", "Unengaged", "HubSpot"),
    2: ("Commercial",   "Engaged",   "HubSpot"),
    3: ("Commercial",   "Unengaged", "Anevvo"),
    4: ("Residential",  "Engaged",   "HubSpot"),
    5: ("Residential",  "Unengaged", "HubSpot"),
}
WINDOW = 7

# light, campaign-agnostic first-draft per audience (the --create fallback; AI/human improves)
ANGLE = {
    "ServiceTitan": ("Live Tech Support", "Live Human Backup — Right Alongside ServiceTitan",
        ["Your techs already run ServiceTitan. Now they've got a live human expert to call when a job gets ugly — no new software to learn.",
         "Real HVAC experts with 20+ years experience pick up in real time, any brand, any equipment.",
         "- Fewer callbacks, more first-time fixes",
         "- Works alongside the tools you already use"]),
    "Commercial": ("Live Tech Support", "Real Experts on Your Toughest Commercial Calls",
        ["Bluon's live technical support is back — real HVAC experts on the line for the commercial jobs that stall your team.",
         "Any brand, any equipment, any issue — your tech gets unstuck in minutes and the job keeps moving.",
         "- Live, real-time troubleshooting",
         "- Video chat so we see exactly what's going on"]),
    "Residential": ("Live Tech Support", "A Live HVAC Expert, Whenever Your Tech Needs One",
        ["Real, live HVAC experts with 20+ years experience, ready to support your team — no guessing on ChatGPT, no waiting on an OEM callback.",
         "Your tech gets the right answer fast, so more jobs close on the first visit.",
         "- Instantly routed to a veteran technician",
         "- Trained on that exact equipment and issue"]),
}


def _today():
    return datetime.date.today()


def _rows():
    out, cur = [], None
    while True:
        body = {"page_size": 100}
        if cur:
            body["start_cursor"] = cur
        r = notion._call("POST", f"/databases/{notion.CALENDAR_DB_ID}/query", body)
        out += r["results"]
        if not r.get("has_more"):
            break
        cur = r["next_cursor"]
    return out


def _f(p, key, kind="select"):
    v = p.get(key, {})
    if kind == "select":
        return (v.get("select") or {}).get("name")
    if kind == "date":
        return (v.get("date") or {}).get("start")


def mark_past_sent():
    n = 0
    for r in _rows():
        p = r["properties"]
        sd = _f(p, "Send Date", "date")
        if not sd:
            continue
        if datetime.date.fromisoformat(sd[:10]) < _today() and _f(p, "Status") != "Sent":
            notion._call("PATCH", f"/pages/{r['id']}", {"properties": {"Status": {"select": {"name": "Sent"}}}})
            n += 1
    print(f"rolled off {n} past row(s) → Status = Sent")
    return n


def upcoming_gaps():
    """Rotation slots in the next WINDOW days that have no drafted row yet."""
    have = set()
    for r in _rows():
        p = r["properties"]
        sd = _f(p, "Send Date", "date")
        if sd:
            have.add((sd[:10], _f(p, "Audience"), _f(p, "Engagement")))
    gaps = []
    for i in range(WINDOW):
        d = _today() + datetime.timedelta(days=i)
        slot = ROTATION.get(d.weekday())
        if not slot:
            continue
        aud, eng, ch = slot
        if (d.isoformat(), aud, eng) not in have:
            gaps.append((d.isoformat(), aud, eng, ch))
    return gaps


def create_gaps():
    made = 0
    import mockup
    for d, aud, eng, ch in upcoming_gaps():
        feat, subject, body = ANGLE[aud]
        cta = "Book a Demo" if eng == "Engaged" else "Learn More"
        url = notion.create_draft(subject=subject, preview="", body="\n".join(body), cta=cta,
                audience=aud, engagement=eng, channel=ch, feature=feat,
                goal=("Demo" if eng == "Engaged" else "Open"),
                send_date=f"{d}T08:00:00-04:00", status=None)
        made += 1
        print(f"  + {d}  {aud} {eng} ({ch})")
    print(f"created {made} rolling draft(s)")
    return made


def fill_mockups():
    import regenerate
    n = 0
    for r in notion.get_calendar_rows():
        blocks = notion._call("GET", f"/blocks/{r['id']}/children?page_size=100")["results"]
        if any(b["type"] == "image" for b in blocks):
            continue
        if regenerate.regen_page(r["id"]):
            n += 1
    print(f"rendered {n} mockup(s)")


def status():
    print(f"--- rolling window: {_today()} .. {_today()+datetime.timedelta(days=WINDOW-1)} ---")
    g = upcoming_gaps()
    print("open rotation slots:", len(g))
    for d, a, e, c in g:
        print(f"  GAP {d}  {a} {e} ({c})")


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "--maintain"
    if mode == "--gaps":
        for d, a, e, c in upcoming_gaps():
            print(f"{d}\t{a}\t{e}\t{c}")
    elif mode == "--create":
        mark_past_sent(); create_gaps(); fill_mockups()
    else:  # --maintain
        mark_past_sent(); fill_mockups(); status()


if __name__ == "__main__":
    main()
