"""Reporting lives IN the Email Calendar (not a separate DB). Every SENT email — including
HubSpot native A/B-test variants — is a calendar row linked to its HubSpot email, with live
stats + Channel, so the calendar's reporting view shows it with the mockups/items intact.

HubSpot native A/B = two email objects: the 'A' send is state PUBLISHED_AB, the 'B' is
PUBLISHED_AB_VARIANT (created with B's primaryEmailCampaignId = A's + 2). The B never existed
as a calendar row, so we CREATE it (Variant B, same Test Group as its A, Status=Sent, linked to
the HubSpot B). To avoid flooding the calendar we ONLY touch tests whose A is already a calendar
row (i.e. emails the machine planned); other HubSpot sends are left alone.

  python scripts/email_report.py
"""
import os, re, json, datetime, urllib.request
import notion

CAL = notion.CALENDAR_DB_ID
HS = (os.environ.get("HUBSPOT_TOKEN") or open(os.path.expanduser("~/.config/hubspot/api_key")).read()).strip()
WINDOW_DAYS = 90


def _hs(url):
    return json.load(urllib.request.urlopen(urllib.request.Request(url, headers={"Authorization": f"Bearer {HS}"}), timeout=60))


def sent_emails():
    cutoff = (datetime.datetime.utcnow() - datetime.timedelta(days=WINDOW_DAYS)).date().isoformat()
    out = []; after = None
    for _ in range(12):
        url = "https://api.hubapi.com/marketing/v3/emails?limit=50&sort=-updatedAt&includeStats=true" + (f"&after={after}" if after else "")
        d = _hs(url)
        for e in d.get("results", []):
            if not e.get("state", "").startswith("PUBLISHED"):
                continue
            c = (e.get("stats", {}) or {}).get("counters", {}) or {}
            if (c.get("sent") or 0) <= 0:
                continue
            pd = e.get("publishDate")
            if pd and pd[:10] < cutoff:
                continue
            out.append(e)
        after = ((d.get("paging", {}) or {}).get("next", {}) or {}).get("after")
        if not after:
            break
    return out


def stats_props(e):
    s = e.get("stats", {}) or {}; c = s.get("counters", {}) or {}; r = s.get("ratios", {}) or {}
    pct = lambda v: round((v or 0) / 100.0, 5)
    return {"Recipients": {"number": c.get("delivered", c.get("sent", 0))},
            "Delivery Rate": {"number": pct(r.get("deliveredratio"))},
            "Open Rate": {"number": pct(r.get("openratio"))}, "CTR": {"number": pct(r.get("clickratio"))},
            "Clicks": {"number": c.get("click", 0)}, "Bounce Rate": {"number": pct(r.get("bounceratio"))},
            "Unsubscribes": {"number": c.get("unsubscribed", 0)}}


def _txt(r): return "".join(x.get("plain_text", "") for x in (r or []))
def _sel(p, k): return ((p.get(k, {}) or {}).get("select") or {}).get("name")


def cal_index():
    """hubspot_id -> {id, tg, audience, engagement} for calendar rows linked to a HubSpot email."""
    out = {}
    for r in notion._call("POST", f"/databases/{CAL}/query", {"page_size": 100})["results"]:
        m = re.search(r"/edit/(\d+)/", (r["properties"].get("Hubspot Email", {}) or {}).get("url") or "")
        if m:
            p = r["properties"]
            out[m.group(1)] = {"id": r["id"], "tg": _txt((p.get("Test Group", {}) or {}).get("rich_text")),
                               "audience": _sel(p, "Audience"), "engagement": _sel(p, "Engagement"),
                               "title": _txt(p.get("Email", {}).get("title"))}
    return out


def run():
    emails = sent_emails()
    def _cid(e):
        try: return int(e.get("primaryEmailCampaignId"))
        except (TypeError, ValueError): return None
    by_camp = {c: e for e in emails for c in [_cid(e)] if c is not None}
    cal = cal_index()
    made = updated = 0
    for e in emails:
        eid = str(e["id"]); st = e.get("state", "")
        if st == "PUBLISHED_AB_VARIANT":
            var = "B"; paired = by_camp.get((_cid(e) or 0) - 2)   # its 'A'
        elif st == "PUBLISHED_AB":
            var = "A"; paired = by_camp.get((_cid(e) or 0) + 2)   # its 'B'
        else:
            var = None; paired = None
        paired_eid = str(paired["id"]) if paired else None
        # this test is "planned" if EITHER variant already has a calendar row (link can point to A or B)
        anchor = cal.get(eid) or (cal.get(paired_eid) if paired_eid else None)
        if not anchor:
            continue
        tg = anchor["tg"] or (e.get("name", ""))[:120]
        props = stats_props(e)
        props.update({
            "Email": {"title": [{"type": "text", "text": {"content": (e.get("name") or "")[:200]}}]},
            "Test Group": {"rich_text": [{"type": "text", "text": {"content": (tg or "")[:200]}}]},
            "Channel": {"select": {"name": "HubSpot"}},
            "Status": {"select": {"name": "Sent"}},
            "Subject": {"rich_text": [{"type": "text", "text": {"content": (e.get("subject") or "")[:200]}}]},
            "Hubspot Email": {"url": f"https://app.hubspot.com/email/6885872/edit/{eid}/content"},
        })
        if var: props["Variant"] = {"select": {"name": var}}
        if anchor.get("audience"): props["Audience"] = {"select": {"name": anchor["audience"]}}
        if anchor.get("engagement"): props["Engagement"] = {"select": {"name": anchor["engagement"]}}
        pd = e.get("publishDate")
        if pd: props["Send Date"] = {"date": {"start": pd[:10]}}
        existing = cal.get(eid)   # a row already linked to THIS email's id?
        if existing:
            notion._call("PATCH", f"/pages/{existing['id']}", {"properties": props}); updated += 1
            print(f"  [{var}] upd  {(e.get('name') or '')[:46]}")
        else:
            notion._call("POST", "/pages", {"parent": {"database_id": CAL}, "properties": props}); made += 1
            print(f"  [{var}] NEW  {(e.get('name') or '')[:46]}")
    print(f"calendar reporting refreshed: {made} new variant row(s), {updated} updated")


if __name__ == "__main__":
    run()
