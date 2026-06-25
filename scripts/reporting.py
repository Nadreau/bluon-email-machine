"""Pull HubSpot send stats into each Email Calendar row's reporting properties.

For every row that has a HubSpot draft linked ("Hubspot Email" url), read the
email's stats from HubSpot and write them back: Recipients, Delivery Rate, Open
Rate, CTR, Clicks, Bounce Rate, Unsubscribes. Rows with no sends yet (still a
draft) are skipped, so the report only fills in once an email actually goes out.

  python scripts/reporting.py            # refresh all linked rows
  python scripts/reporting.py <page_id>  # one row

Designed to run on a daily schedule (cron / GitHub Actions) once sends are live.
"""
import os, sys, re, json, urllib.request, urllib.error
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
        "Recipients":    {"number": c.get("delivered", c.get("sent", 0))},
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


RECIP_FLOOR = 200      # every variant needs this many recipients before we crown anyone
CTR_MARGIN = 0.10      # winner's CTR must beat runner-up by >=10% RELATIVE (else "too close")


def mark_winners():
    """Within each Test Group, flag the winning variant on per-recipient RATES — but
    only once the result is TRUSTWORTHY, so a tiny/early sample never crowns a fluke.

    Gates (all must pass, else the whole group is left winner-less = "pending"):
      • every variant has >= RECIP_FLOOR recipients (kills the 36-recipient fluke),
      • the leader's CTR beats the runner-up by >= CTR_MARGIN relative (kills noise),
      • the leader actually has clicks (CTR > 0).
    Winner is judged on CTR (open-rate tiebreak). We ALWAYS write every row in the
    group (True on the one winner, False on the rest) — including singleton/typo'd or
    ungated groups, which all get forced to False so a stale trophy can't linger."""
    groups = {}
    for r in notion._call("POST", f"/databases/{notion.CALENDAR_DB_ID}/query", {"page_size": 100})["results"]:
        p = r["properties"]
        tg = "".join(x.get("plain_text", "") for x in (p.get("Test Group", {}).get("rich_text") or []))
        if tg:
            groups.setdefault(tg, []).append((r["id"], p))
    for tg, rows in groups.items():
        scored = [(pid, p) for pid, p in rows if (p.get("Open Rate", {}) or {}).get("number") is not None]
        winner = None
        if len(scored) >= 2 and all(_num(p, "Recipients") >= RECIP_FLOOR for _, p in scored):
            ranked = sorted(scored, key=lambda x: (_num(x[1], "CTR"), _num(x[1], "Open Rate")), reverse=True)
            top_ctr, runner_ctr = _num(ranked[0][1], "CTR"), _num(ranked[1][1], "CTR")
            clear = top_ctr > 0 and (runner_ctr == 0 or (top_ctr - runner_ctr) / runner_ctr >= CTR_MARGIN)
            if clear:
                winner = ranked[0][0]
        for pid, _ in rows:                      # force-write the whole group (no stale True)
            notion._call("PATCH", f"/pages/{pid}", {"properties": {"Winner": {"checkbox": pid == winner}}})
        if winner:
            print(f"  winner crowned in '{tg}' (CTR, open-rate tiebreak)")
        elif len(scored) >= 2:
            print(f"  '{tg}' pending — not enough recipients or too close to call")
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
