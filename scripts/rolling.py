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

# 3-week rotating cycle. The audience order shifts by one each week so no audience
# sits in the same early/mid/late slot two weeks running. Audience-grouped,
# engaged then unengaged, one email per day Mon–Sat (Sun = rest).
ORDERS = [
    ["Residential",  "Commercial",   "ServiceTitan"],  # week % 3 == 0
    ["Commercial",   "ServiceTitan", "Residential"],   # week % 3 == 1
    ["ServiceTitan", "Residential",  "Commercial"],    # week % 3 == 2
]
WINDOW = 7


def slot_for(d):
    """(Audience, Engagement, Channel) for a date per the rotating routine, or None on Sunday."""
    wd = d.weekday()                       # 0=Mon .. 6=Sun
    if wd > 5:
        return None                        # Sunday = rest
    order = ORDERS[d.isocalendar()[1] % 3]
    aud = order[wd // 2]                    # Mon/Tue=early, Wed/Thu=mid, Fri/Sat=late
    eng = "Engaged" if wd % 2 == 0 else "Unengaged"
    return (aud, eng, "HubSpot")           # Anevo stays a per-send curation choice, not baked in

# light, campaign-agnostic first-draft per audience (the --create fallback; AI/human improves)
ANGLE = {
    # ServiceTitan = audience segment only. Live human support is Bluon's STANDALONE app —
    # NEVER frame it as inside/alongside the ServiceTitan integration (Tanner rule, Jun 16 2026).
    # The ST integration = parts/specs/manuals/AI lookup, not live support. Copy decoupled; finalize w/ Tanner.
    "ServiceTitan": ("Live Tech Support", "Live Human Backup for Your HVAC Techs",
        ["Bluon's live tech support is back — real HVAC experts your techs can call the moment a job gets ugly.",
         "Real HVAC experts with 20+ years experience pick up in real time, any brand, any equipment.",
         "- Fewer callbacks, more first-time fixes",
         "- One call, as long as it takes — no tickets, no hold"]),
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


def roll_off():
    """Past rows leave the approval window. Approved (Ready to Go) → Sent; never
    approved → Unused (the backlog of stuff that passed without going out)."""
    s = u = 0
    for r in _rows():
        p = r["properties"]
        sd = _f(p, "Send Date", "date")
        if not sd:
            continue
        if datetime.date.fromisoformat(sd[:10]) < _today() and _f(p, "Status") not in ("Sent", "Unused"):
            ready = p.get("Ready to Go", {}).get("checkbox")
            new = "Sent" if ready else "Unused"
            notion._call("PATCH", f"/pages/{r['id']}", {"properties": {"Status": {"select": {"name": new}}}})
            s += ready; u += not ready
    print(f"rolled off: {s} → Sent (approved), {u} → Unused (passed un-approved → backlog)")
    return s + u


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
        slot = slot_for(d)
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
        roll_off(); create_gaps(); fill_mockups()
    else:  # --maintain
        roll_off(); fill_mockups(); status()


if __name__ == "__main__":
    main()
