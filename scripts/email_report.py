"""Email Reporting ledger — pulls every email that ACTUALLY SENT straight from HubSpot
(the source of truth), INCLUDING native A/B-test variants, fully decoupled from the
planning calendar. Nothing in the calendar can hide, collapse, or delete a real send.

HubSpot native A/B = two email objects: the 'A' send is state PUBLISHED_AB, the 'B' is
PUBLISHED_AB_VARIANT; HubSpot creates them with consecutive primaryEmailCampaignId
(B = A + 2), which is how we pair them into one Test Group. Winner = higher open rate.

  python scripts/email_report.py
"""
import os, re, json, datetime, urllib.request, urllib.error
from collections import defaultdict
import notion

REPORT_DB = "38e576a5-c12d-81fc-bed8-e9cd1fe94d8f"
HS = (os.environ.get("HUBSPOT_TOKEN") or open(os.path.expanduser("~/.config/hubspot/api_key")).read()).strip()
WINDOW_DAYS = 75
_EID = re.compile(r"/edit/(\d+)/")


def _hs(url):
    return json.load(urllib.request.urlopen(urllib.request.Request(url, headers={"Authorization": f"Bearer {HS}"}), timeout=60))


def sent_emails():
    """Marketing emails that actually went out in the last WINDOW_DAYS (state PUBLISHED*, sent > 0)."""
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
    return {
        "Recipients":   {"number": c.get("delivered", c.get("sent", 0))},
        "Delivery Rate":{"number": pct(r.get("deliveredratio"))},
        "Open Rate":    {"number": pct(r.get("openratio"))},
        "CTR":          {"number": pct(r.get("clickratio"))},
        "Clicks":       {"number": c.get("click", 0)},
        "Bounce Rate":  {"number": pct(r.get("bounceratio"))},
        "Unsubscribes": {"number": c.get("unsubscribed", 0)},
    }


def existing_index():
    idx = {}
    for r in notion._call("POST", f"/databases/{REPORT_DB}/query", {"page_size": 100})["results"]:
        m = _EID.search((r["properties"].get("HubSpot Email", {}) or {}).get("url") or "")
        if m:
            idx[m.group(1)] = r["id"]
    return idx


def run():
    emails = sent_emails()
    def _cid(e):
        try: return int(e.get("primaryEmailCampaignId"))
        except (TypeError, ValueError): return None
    by_camp = {c: e for e in emails for c in [_cid(e)] if c is not None}

    def variant_group(e):
        st = e.get("state", "")
        if st == "PUBLISHED_AB_VARIANT":
            cid = _cid(e)
            a = by_camp.get(cid - 2) if cid is not None else None         # its paired 'A' send (consecutive campaign id)
            return "B", ((a or e).get("name") or "")[:140]
        if st == "PUBLISHED_AB":
            return "A", (e.get("name") or "")[:140]
        return None, (e.get("name") or "")[:140]                          # single send (no A/B)

    idx = existing_index(); tracked = []
    for e in emails:
        eid = str(e.get("id"))
        var, group = variant_group(e)
        props = stats_props(e)
        props.update({
            "Email":   {"title": [{"type": "text", "text": {"content": (e.get("name") or "")[:200]}}]},
            "Subject": {"rich_text": [{"type": "text", "text": {"content": (e.get("subject") or "")[:200]}}]},
            "Test Group": {"rich_text": [{"type": "text", "text": {"content": group}}]},
            "HubSpot Email": {"url": f"https://app.hubspot.com/email/6885872/edit/{eid}/content"},
        })
        if var:
            props["Variant"] = {"select": {"name": var}}
        pd = e.get("publishDate")
        if pd:
            props["Send Date"] = {"date": {"start": pd[:10]}}
        pid = idx.get(eid)
        if pid:
            notion._call("PATCH", f"/pages/{pid}", {"properties": props})
        else:
            pid = notion._call("POST", "/pages", {"parent": {"database_id": REPORT_DB}, "properties": props})["id"]
            idx[eid] = pid
        tracked.append((pid, group, props["Open Rate"]["number"], props["CTR"]["number"]))
        print(f"  ✓ [{var or '-'}] {(e.get('name') or '')[:40]:40} open {props['Open Rate']['number']*100:4.1f}% ctr {props['CTR']['number']*100:.1f}%")
    # Winner per Test Group (only where 2+ variants ran), by open rate (CTR tiebreak)
    groups = defaultdict(list)
    for pid, group, opn, ctr in tracked:
        groups[group].append((pid, opn, ctr))
    for g, members in groups.items():
        win = max(members, key=lambda x: (x[1], x[2]))[0] if len(members) >= 2 else None
        for pid, _, _ in members:
            notion._call("PATCH", f"/pages/{pid}", {"properties": {"Winner": {"checkbox": pid == win}}})
    print(f"ledger: {len(tracked)} sent email(s) → https://www.notion.so/{REPORT_DB.replace('-','')}")


if __name__ == "__main__":
    run()
