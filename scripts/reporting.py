"""Pull HubSpot send stats into each Email Calendar row's reporting properties.

For every row that has a HubSpot draft linked ("Hubspot Email" url), read the
email's stats from HubSpot and write them back: Recipients, Delivery Rate, Open
Rate, CTR, Clicks, Bounce Rate, Unsubscribes. Rows with no sends yet (still a
draft) are skipped, so the report only fills in once an email actually goes out.

  python scripts/reporting.py            # refresh all linked rows
  python scripts/reporting.py <page_id>  # one row

Designed to run on a daily schedule (cron / GitHub Actions) once sends are live.
"""
import os, sys, re, json, datetime, urllib.request, urllib.error
import notion

HS_TOKEN = os.environ.get("HUBSPOT_TOKEN", "").strip() or open(
    os.path.expanduser("~/.config/hubspot/api_key")).read().strip()
EID_RE = re.compile(r"/edit/(\d+)/")


def hs_email(eid):
    req = urllib.request.Request(
        f"https://api.hubapi.com/marketing/v3/emails/{eid}?includeStats=true",
        headers={"Authorization": f"Bearer {HS_TOKEN}"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


def stats_to_props(stats):
    """Map HubSpot stats.counters/ratios → Notion number properties.
    Notion percent format stores a fraction (0.25 → shows 25%), so ratios/100."""
    c = stats.get("counters", {}) or {}
    r = stats.get("ratios", {}) or {}
    if not c.get("sent"):
        return None  # nothing sent yet — leave the row alone
    pct = lambda v: round((v or 0) / 100.0, 5)
    return {
        "Audience Size": {"number": c.get("selected") or c.get("sent", 0)},  # pre-suppression target pool
        "Recipients":    {"number": c.get("delivered", c.get("sent", 0))},   # delivered = rate denominator
        "Delivery Rate": {"number": pct(r.get("deliveredratio"))},
        "Open Rate":     {"number": pct(r.get("openratio"))},
        "CTR":           {"number": pct(r.get("clickratio"))},
        "Clicks":        {"number": c.get("click", 0)},
        "Bounce Rate":   {"number": pct(r.get("bounceratio"))},
        "Unsubscribes":  {"number": c.get("unsubscribed", 0)},
    }


def refresh(page_id):
    pr = notion._call("GET", f"/pages/{page_id}")["properties"]
    url = (pr.get("Hubspot Email", {}) or {}).get("url")
    name = "".join(x.get("plain_text", "") for x in pr.get("Email", {}).get("title", []))
    if not url:
        return False
    m = EID_RE.search(url)
    if not m:
        print("  no email id in url:", name); return False
    try:
        email = hs_email(m.group(1))
    except urllib.error.HTTPError as e:
        print("  HubSpot error", e.code, "for", name); return False
    props = stats_to_props(email.get("stats", {}) or {})
    if not props:
        print("  no sends yet:", name); return False
    props["Status"] = {"select": {"name": "Sent"}}  # flips it into the Reporting view
    notion._call("PATCH", f"/pages/{page_id}", {"properties": props})
    print(f"  updated {name}: {props['Recipients']['number']} recipients, "
          f"open {props['Open Rate']['number']*100:.1f}%, ctr {props['CTR']['number']*100:.1f}%")
    return True


def _num(p, key):
    return (p.get(key, {}) or {}).get("number") or 0


SETTLE_DAYS = 7   # wait this long after the last variant sends, THEN decide the winner


def _send_day(p):
    sd = (p.get("Send Date", {}) or {}).get("date") or {}
    try:
        return datetime.date.fromisoformat((sd.get("start") or "")[:10])
    except ValueError:
        return None


def mark_winners():
    """Within each Test Group, crown the winning variant — but only once the test has
    SETTLED, so an early read never gets locked in. Settle rule is purely TIME-based
    (pool size is irrelevant and often unknown): wait SETTLE_DAYS after the latest
    variant's send, then decide. Winner = highest CTR (open-rate tiebreak).

    Until a group settles it stays winner-less ("pending") — the per-variant CTR /
    Open Rate columns still show the live leaning in the meantime. We ALWAYS write
    every row in the group (True on the winner, False on the rest), so a stale trophy
    can never linger on a singleton/typo'd/not-yet-settled group."""
    today = datetime.date.today()
    groups = {}
    for r in notion._call("POST", f"/databases/{notion.CALENDAR_DB_ID}/query", {"page_size": 100})["results"]:
        p = r["properties"]
        tg = "".join(x.get("plain_text", "") for x in (p.get("Test Group", {}).get("rich_text") or []))
        if tg:
            groups.setdefault(tg, []).append((r["id"], p))
    for tg, rows in groups.items():
        scored = [(pid, p) for pid, p in rows if (p.get("Open Rate", {}) or {}).get("number") is not None]
        winner = None
        days = None
        if len(scored) >= 2:
            sent_days = [d for d in (_send_day(p) for _, p in scored) if d]
            if sent_days:
                days = (today - max(sent_days)).days       # days since the LAST variant sent
                if days >= SETTLE_DAYS:
                    winner = max(scored, key=lambda x: (_num(x[1], "CTR"), _num(x[1], "Open Rate")))[0]
        for pid, _ in rows:                                  # force-write the whole group (no stale True)
            notion._call("PATCH", f"/pages/{pid}", {"properties": {"Winner": {"checkbox": pid == winner}}})
        if winner:
            print(f"  winner crowned in '{tg}' (CTR, open-rate tiebreak)")
        elif len(scored) >= 2:
            left = SETTLE_DAYS - days if days is not None else "?"
            print(f"  '{tg}' pending — settles in {left} day(s)")
        else:
            print(f"  '{tg}' has <2 scored variants — left winner-less")


def main():
    if len(sys.argv) > 1:
        refresh(sys.argv[1]); return
    n = 0
    for r in notion.get_calendar_rows():
        if refresh(r["id"]):
            n += 1
    print(f"refreshed {n} row(s) with live stats")
    mark_winners()


if __name__ == "__main__":
    main()
