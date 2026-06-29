"""Anevo email reporting → the "Email Reporting — Automated" DB, LIVE from the Smartlead API.

Anevo (our cold-email partner) runs on Smartlead. This pulls each campaign's real analytics
(sent / open / click / reply / bounce / unsub / interested-leads) and upserts one row per
campaign tagged Source=Anevo, so HubSpot + Anevo email reporting live in one DB. Replaces the
old manual-Google-Sheet source now that we have API access.

Each Smartlead "campaign" is split per inbox provider ([... (Gmail)] / (Outlook) / (Others));
we combine those splits into one logical campaign. "Standard Subsequence" (DRAFTED) follow-ups
are skipped. Stale Anevo rows from the old sheet era are archived.

Smartlead is behind Cloudflare — MUST send a User-Agent or it 403s (error 1010). Rate limit
60 req/min. Key from SMARTLEAD_API_KEY env (CI) or ~/.config/smartlead/api_key.

  python scripts/anevo_report.py
"""
import os, re, json, urllib.request, urllib.error, time
import notion

REPORTING_DB = "38e576a5-c12d-81b7-a5a8-d2e1e2f5433a"
KEY = (os.environ.get("SMARTLEAD_API_KEY") or open(os.path.expanduser("~/.config/smartlead/api_key")).read()).strip()
BASE = "https://server.smartlead.ai/api/v1"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"


def sl(path):
    sep = "&" if "?" in path else "?"
    req = urllib.request.Request(f"{BASE}{path}{sep}api_key={KEY}", headers={"User-Agent": UA})
    for _ in range(3):
        try:
            with urllib.request.urlopen(req, timeout=45) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code == 429:
                time.sleep(2); continue
            raise
    return None


def _audience(name):
    n = (name or "").lower()
    for a in ("ServiceTitan", "Residential", "Commercial", "Churned", "Texas"):
        if a.lower() in n:
            return a
    return None


def _send_date(name):
    m = re.search(r"(\d{1,2})/(\d{1,2})/(\d{2,4})", name or "")
    if not m:
        return None
    mo, d, y = m.groups(); y = int(y); y = y + 2000 if y < 100 else y
    return f"{y:04d}-{int(mo):02d}-{int(d):02d}"


def _base_name(name):
    """Combine the per-inbox splits: strip a trailing '(Gmail)' / '(Outlook)' / '(Others)'."""
    return re.sub(r"\s*\((Gmail|Outlook|Others)\)\s*$", "", (name or "").strip())


def _existing_anevo():
    out = {}; cur = None
    while True:
        body = {"page_size": 100}
        if cur:
            body["start_cursor"] = cur
        res = notion._call("POST", f"/databases/{REPORTING_DB}/query", body)
        for r in res["results"]:
            pr = r["properties"]
            if (pr.get("Source", {}).get("select") or {}).get("name") == "Anevo":
                subj = "".join(x.get("plain_text", "") for x in (pr.get("Subject", {}).get("rich_text") or []))
                out[subj.strip()] = r["id"]
        if not res.get("has_more"):
            break
        cur = res.get("next_cursor")
    return out


def run():
    camps = sl("/campaigns")
    camps = camps if isinstance(camps, list) else (camps or {}).get("data", [])
    real = [c for c in camps if c.get("status") in ("ACTIVE", "COMPLETED")
            and "subsequence" not in str(c.get("name", "")).lower()]
    print(f"Smartlead: {len(real)} active/completed campaigns")

    agg = {}
    for c in real:
        a = sl(f"/campaigns/{c['id']}/analytics")
        if not isinstance(a, dict):
            continue
        iv = lambda k: int(float(a.get(k, 0) or 0))
        base = _base_name(a.get("name") or c.get("name"))
        g = agg.setdefault(base, {"sent": 0, "open": 0, "click": 0, "reply": 0, "bounce": 0, "unsub": 0, "interested": 0})
        g["sent"] += iv("sent_count"); g["open"] += iv("open_count"); g["click"] += iv("click_count")
        g["reply"] += iv("reply_count"); g["bounce"] += iv("bounce_count"); g["unsub"] += iv("unsubscribed_count")
        g["interested"] += int((a.get("campaign_lead_stats") or {}).get("interested", 0) or 0)

    existing = _existing_anevo()
    keep = set()
    for base, g in sorted(agg.items(), key=lambda kv: -kv[1]["sent"]):
        sent = g["sent"]
        if sent <= 0:
            continue
        aud = _audience(base); date = _send_date(base)
        props = {
            "Name": {"title": [{"type": "text", "text": {"content": ("Anevo — " + base.replace("[BLUON] ", ""))[:200]}}]},
            "Source": {"select": {"name": "Anevo"}},
            "Test": {"select": {"name": f"Anevo · {aud or 'Send'}"}},
            "Subject": {"rich_text": [{"type": "text", "text": {"content": base[:1900]}}]},
            "Recipients": {"number": sent},
            "Open Rate": {"number": round(g["open"] / sent, 4)},
            "CTR": {"number": round(g["click"] / sent, 4)},
            "Clicks": {"number": g["click"]},
            "Replies": {"number": g["reply"]},
            "Leads (Interested)": {"number": g["interested"]},
            "Bounce Rate": {"number": round(g["bounce"] / sent, 4)},
            "Unsubscribes": {"number": g["unsub"]},
        }
        if aud:
            props["Audience"] = {"select": {"name": aud}}
        if date:
            props["Sent"] = {"date": {"start": date}}
        key = base.strip(); keep.add(key)
        if key in existing:
            notion._call("PATCH", f"/pages/{existing[key]}", {"properties": props})
            print(f"  ~ {base[:48]:50} sent {sent:>6}  reply {g['reply']:>3}  leads {g['interested']:>2}")
        else:
            notion._call("POST", "/pages", {"parent": {"database_id": REPORTING_DB}, "properties": props})
            print(f"  + {base[:48]:50} sent {sent:>6}  reply {g['reply']:>3}  leads {g['interested']:>2}")

    # archive stale Anevo rows left over from the old manual-sheet source
    for subj, pid in existing.items():
        if subj not in keep:
            notion._call("PATCH", f"/pages/{pid}", {"archived": True})
            print(f"  - archived stale (sheet-era): {subj[:50]}")
    print(f"Anevo reporting refreshed: {len(keep)} live campaigns")


if __name__ == "__main__":
    run()
