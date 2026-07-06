"""Anevo email reporting → the "Email Reporting — Automated" DB, LIVE from the Smartlead API.

Anevo (our cold-email partner) runs on Smartlead. This pulls each campaign's real analytics
(sent / open / click / reply / bounce / unsub / interested-leads) and upserts one row per
campaign tagged Source=Anevo, so HubSpot + Anevo email reporting live in one DB. Replaces the
old manual-Google-Sheet source now that we have API access.

Campaigns are NOT tied to a send date — one campaign drips over multiple weeks (Tanner,
7/6 call). So each row also carries:
  Campaign Status  Running / Paused / Completed — "Running" = ACTIVE, or paused mid-flight
                   but still sending in the last 14 days (Anevo abandons finished campaigns
                   as PAUSED, so status alone can't be trusted)
  Progress         leads fully worked (completed+blocked+stopped) / total leads
  A/B Tests        per-subject-variant stats (Anevo A/B = spintax subjects; each send logs
                   the exact subject used), aggregated for running campaigns only

Each Smartlead "campaign" is split per inbox provider ([... (Gmail)] / (Outlook) / (Others));
we combine those splits into one logical campaign. "Standard Subsequence" (DRAFTED) follow-ups
are skipped. Stale Anevo rows from the old sheet era are archived.

Smartlead is behind Cloudflare — MUST send a User-Agent or it 403s (error 1010). Rate limit
60 req/min. Key from SMARTLEAD_API_KEY env (CI) or ~/.config/smartlead/api_key.

  python scripts/anevo_report.py
"""
import os, re, json, urllib.request, urllib.error, time, datetime
import notion

REPORTING_DB = "38e576a5-c12d-81b7-a5a8-d2e1e2f5433a"
KEY = (os.environ.get("SMARTLEAD_API_KEY") or open(os.path.expanduser("~/.config/smartlead/api_key")).read()).strip()
BASE = "https://server.smartlead.ai/api/v1"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"

RECENT_DAYS = 14          # paused campaign that sent within this window = still in flight
DONE_AT = 0.995           # lead-progress at/above this = the campaign is finished
MAX_STAT_ROWS = 20000     # per split — A/B pull safety cap (logs if it truncates)


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


def _ensure_props():
    """Add the status/progress/AB columns to the Reporting DB if they're not there yet."""
    db = notion._call("GET", f"/databases/{REPORTING_DB}")
    have = db.get("properties", {})
    want = {
        "Campaign Status": {"select": {"options": [
            {"name": "Running", "color": "green"},
            {"name": "Paused", "color": "yellow"},
            {"name": "Completed", "color": "gray"}]}},
        "Progress": {"number": {"format": "percent"}},
        "A/B Tests": {"rich_text": {}},
    }
    missing = {k: v for k, v in want.items() if k not in have}
    if missing:
        notion._call("PATCH", f"/databases/{REPORTING_DB}", {"properties": missing})
        print(f"added DB columns: {', '.join(missing)}")


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


def _recent_sent(splits):
    """Emails sent across the splits in the last RECENT_DAYS (analytics-by-date)."""
    today = datetime.date.today()
    since = today - datetime.timedelta(days=RECENT_DAYS)
    n = 0
    for c in splits:
        a = sl(f"/campaigns/{c['id']}/analytics-by-date?start_date={since}&end_date={today}")
        if isinstance(a, dict):
            n += int(float(a.get("sent_count", 0) or 0))
    return n


def _ab_summary(splits):
    """Anevo's A/B = spintax subjects; every /statistics row logs the exact subject sent.
    Aggregate sent/open/reply per (step, subject) across the splits and describe any step
    that actually has 2+ subjects. Returns '' when there's no test."""
    agg = {}
    for c in splits:
        off = 0
        while True:
            r = sl(f"/campaigns/{c['id']}/statistics?limit=1000&offset={off}")
            rows = (r or {}).get("data") or []
            for row in rows:
                key = (row.get("sequence_number") or 1, (row.get("email_subject") or "").strip())
                g = agg.setdefault(key, {"sent": 0, "open": 0, "reply": 0})
                g["sent"] += 1
                g["open"] += 1 if row.get("open_time") else 0
                g["reply"] += 1 if row.get("reply_time") else 0
            off += len(rows)
            if len(rows) < 1000:
                break
            if off >= MAX_STAT_ROWS:
                print(f"    NOTE: A/B pull capped at {MAX_STAT_ROWS} rows for campaign {c['id']}")
                break
    steps = {}
    for (step, subj), g in agg.items():
        if subj:
            steps.setdefault(step, []).append((subj, g))
    parts = []
    for step in sorted(steps):
        variants = sorted(steps[step], key=lambda x: -x[1]["sent"])
        if len(variants) < 2:
            continue
        vs = []
        for i, (subj, g) in enumerate(variants):
            s = subj if len(subj) <= 60 else subj[:57] + "…"
            vs.append(f"{chr(65 + i)} “{s}” — {g['sent']:,} sent · {g['open'] / g['sent'] * 100:.0f}% open · {g['reply']} repl")
        label = f"Step {step} subject test: " if len(steps) > 1 else "Subject test: "
        parts.append(label + "  |  ".join(vs))
    return "\n".join(parts)[:1900]


def run():
    _ensure_props()
    camps = sl("/campaigns")
    camps = camps if isinstance(camps, list) else (camps or {}).get("data", [])
    # keep EVERY non-subsequence campaign regardless of status — Anevo routinely
    # PAUSES finished campaigns instead of completing them (verified Jul 1 2026:
    # status-filtering to ACTIVE/COMPLETED hid 105K sent / 52 interested leads,
    # 58% of all real volume). Zero-send campaigns (incl. drafts) drop out at the
    # sent<=0 guard below.
    real = [c for c in camps if "subsequence" not in str(c.get("name", "")).lower()]
    by_status = {}
    for c in real:
        by_status[c.get("status")] = by_status.get(c.get("status"), 0) + 1
    print(f"Smartlead: {len(real)} campaigns ({by_status})")

    groups = {}
    for c in real:
        groups.setdefault(_base_name(c.get("name")), []).append(c)

    agg = {}
    for base, splits in groups.items():
        g = {"sent": 0, "open": 0, "click": 0, "reply": 0, "bounce": 0, "unsub": 0,
             "interested": 0, "total": 0, "done": 0, "splits": splits,
             "statuses": {c.get("status") for c in splits}}
        for c in splits:
            a = sl(f"/campaigns/{c['id']}/analytics")
            if not isinstance(a, dict):
                continue
            iv = lambda k: int(float(a.get(k, 0) or 0))
            g["sent"] += iv("sent_count"); g["open"] += iv("open_count"); g["click"] += iv("click_count")
            g["reply"] += iv("reply_count"); g["bounce"] += iv("bounce_count"); g["unsub"] += iv("unsubscribed_count")
            ls = a.get("campaign_lead_stats") or {}
            g["interested"] += int(ls.get("interested", 0) or 0)
            g["total"] += int(ls.get("total", 0) or 0)
            g["done"] += sum(int(ls.get(k, 0) or 0) for k in ("completed", "blocked", "stopped"))
        agg[base] = g

    existing = _existing_anevo()
    keep = set()
    for base, g in sorted(agg.items(), key=lambda kv: -kv[1]["sent"]):
        sent = g["sent"]
        if sent <= 0:
            continue
        progress = (g["done"] / g["total"]) if g["total"] else None
        # Running = ACTIVE, or paused mid-flight but with sends inside the window
        if "ACTIVE" in g["statuses"]:
            status = "Running"
        elif progress is not None and progress >= DONE_AT:
            status = "Completed"
        elif "COMPLETED" in g["statuses"]:
            status = "Completed"
        elif _recent_sent(g["splits"]) > 0:
            status = "Running"
        else:
            status = "Paused"
        ab = _ab_summary(g["splits"]) if status == "Running" else ""

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
            "Campaign Status": {"select": {"name": status}},
            "Progress": {"number": round(progress, 4) if progress is not None else None},
            "A/B Tests": {"rich_text": ([{"type": "text", "text": {"content": ab}}] if ab else [])},
        }
        if aud:
            props["Audience"] = {"select": {"name": aud}}
        if date:
            props["Sent"] = {"date": {"start": date}}   # campaign START date (from the name)
        key = base.strip(); keep.add(key)
        pct = f"{progress * 100:3.0f}%" if progress is not None else "  —"
        if key in existing:
            notion._call("PATCH", f"/pages/{existing[key]}", {"properties": props})
            print(f"  ~ {base[:44]:46} {status:<9} {pct}  sent {sent:>6}  reply {g['reply']:>3}  leads {g['interested']:>2}")
        else:
            notion._call("POST", "/pages", {"parent": {"database_id": REPORTING_DB}, "properties": props})
            print(f"  + {base[:44]:46} {status:<9} {pct}  sent {sent:>6}  reply {g['reply']:>3}  leads {g['interested']:>2}")

    # archive ONLY true sheet-era leftovers: rows whose name matches NO Smartlead
    # campaign at all (any status). Never archive on status alone — a paused
    # campaign's history must survive on the dashboard.
    all_names = {_base_name(c.get("name")) for c in camps}
    for subj, pid in existing.items():
        if subj not in keep and subj not in all_names:
            notion._call("PATCH", f"/pages/{pid}", {"archived": True})
            print(f"  - archived stale (sheet-era): {subj[:50]}")
    print(f"Anevo reporting refreshed: {len(keep)} campaigns")


if __name__ == "__main__":
    run()
