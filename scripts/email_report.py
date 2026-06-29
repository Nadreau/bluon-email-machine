"""Email Reporting — a PERMANENT ledger of every email that actually SENT, in its OWN database
(decoupled from the planning calendar). One row per sent HubSpot email, keyed by the HubSpot
email id, with Test Group + Variant so A/B variants stack. Refreshed daily from HubSpot.

Why a separate DB: the old "reporting" was just a VIEW of the planning calendar, so deleting/
re-fanning a calendar row changed what reporting showed. This ledger records what WENT OUT — a
calendar change can never erase it.

Which emails are "ours" (two self-sustaining sources):
  - calendar rows that carry a "Hubspot Email" link  -> captures metadata (audience/test-group/variant)
  - rows already in THIS ledger                       -> once captured, we keep updating from HubSpot forever

  python scripts/email_report.py
"""
import os, re, urllib.error
from collections import defaultdict
import notion
from reporting import hs_email, stats_to_props, EID_RE   # reuse the HubSpot stat plumbing

REPORT_DB = "38e576a5-c12d-81fc-bed8-e9cd1fe94d8f"
KEEP_STATS = {"Recipients", "Delivery Rate", "Open Rate", "CTR", "Clicks", "Bounce Rate", "Unsubscribes"}


def _txt(rts): return "".join(x.get("plain_text", "") for x in (rts or []))
def _sel(p, k): return ((p.get(k, {}) or {}).get("select") or {}).get("name")
def _rt(p, k): return _txt((p.get(k, {}) or {}).get("rich_text"))
def _eid(url):
    m = EID_RE.search(url or ""); return m.group(1) if m else None


def parse_variant(name):
    """'LTS Relaunch - Residential - A  "subj"' -> ('LTS Relaunch - Residential', 'A')."""
    m = re.search(r"\s[-–]\s*([A-F])\b", name)
    if m:
        return name[:m.start()].strip(), m.group(1)
    return name, None


def _meta_from(p, ledger_id=None):
    return {"url": (p.get("Hubspot Email", p.get("HubSpot Email", {})) or {}).get("url"),
            "name": _txt(p.get("Email", {}).get("title")), "subject": _rt(p, "Subject"),
            "audience": _sel(p, "Audience"), "engagement": _sel(p, "Engagement"),
            "test_group": _rt(p, "Test Group"), "variant": _sel(p, "Variant"),
            "send_date": (p.get("Send Date", {}).get("date") or {}).get("start"), "ledger_id": ledger_id}


def gather():
    """{hubspot_id: meta} from calendar rows (with a HubSpot link) + existing ledger rows."""
    out = {}
    for r in notion._call("POST", f"/databases/{notion.CALENDAR_DB_ID}/query", {"page_size": 100})["results"]:
        eid = _eid((r["properties"].get("Hubspot Email", {}) or {}).get("url"))
        if eid:
            out[eid] = _meta_from(r["properties"])
    for r in notion._call("POST", f"/databases/{REPORT_DB}/query", {"page_size": 100})["results"]:
        eid = _eid((r["properties"].get("HubSpot Email", {}) or {}).get("url"))
        if eid and eid not in out:
            out[eid] = _meta_from(r["properties"], ledger_id=r["id"])
    return out


def run():
    meta = gather()
    idx = {eid: m["ledger_id"] for eid, m in meta.items() if m.get("ledger_id")}
    tracked = []
    for eid, m in meta.items():
        try:
            email = hs_email(eid)
        except urllib.error.HTTPError:
            print("  HubSpot error:", m["name"]); continue
        stats = stats_to_props(email.get("stats", {}) or {})
        if not stats:
            print("  no sends yet:", m["name"]); continue
        stats = {k: v for k, v in stats.items() if k in KEEP_STATS}   # drop "Audience Size" (Tanner: no 'sends')
        hname = email.get("name") or m["name"]
        tg, var = m.get("test_group"), m.get("variant")
        if not tg or not var:                                          # fall back to parsing the HubSpot name
            ptg, pvar = parse_variant(hname); tg = tg or ptg; var = var or pvar
        props = dict(stats)
        props.update({
            "Email": {"title": [{"type": "text", "text": {"content": (m["name"] or hname)[:200]}}]},
            "Subject": {"rich_text": [{"type": "text", "text": {"content": (m.get("subject") or "")[:200]}}]},
            "Test Group": {"rich_text": [{"type": "text", "text": {"content": (tg or "")[:200]}}]},
            "HubSpot Email": {"url": m["url"]},
        })
        if var: props["Variant"] = {"select": {"name": var}}
        if m.get("audience"): props["Audience"] = {"select": {"name": m["audience"]}}
        if m.get("engagement"): props["Engagement"] = {"select": {"name": m["engagement"]}}
        if m.get("send_date"): props["Send Date"] = {"date": {"start": m["send_date"][:10]}}
        pid = idx.get(eid)
        if pid:
            notion._call("PATCH", f"/pages/{pid}", {"properties": props})
        else:
            pid = notion._call("POST", "/pages", {"parent": {"database_id": REPORT_DB}, "properties": props})["id"]
            idx[eid] = pid
        tracked.append((pid, tg, stats))
        print(f"  ✓ {(m['name'] or hname)[:42]:42} {stats['Recipients']['number']:>5} recip · open {stats['Open Rate']['number']*100:4.1f}%")
    # Winner per Test Group (highest CTR, open-rate tiebreak) once 2+ variants exist
    groups = defaultdict(list)
    for pid, tg, stats in tracked:
        if tg: groups[tg].append((pid, stats.get("CTR", {}).get("number", 0), stats.get("Open Rate", {}).get("number", 0)))
    for tg, members in groups.items():
        win = max(members, key=lambda x: (x[1], x[2]))[0] if len(members) >= 2 else None
        for pid, _, _ in members:
            notion._call("PATCH", f"/pages/{pid}", {"properties": {"Winner": {"checkbox": pid == win}}})
    print(f"ledger: {len(tracked)} sent email(s) tracked → https://www.notion.so/{REPORT_DB.replace('-','')}")


if __name__ == "__main__":
    run()
