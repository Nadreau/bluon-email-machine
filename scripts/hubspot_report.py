"""Keep the Email Reporting DB's HubSpot rows current — discover, refresh, crown.

The Email Reporting DB (38e576a5) is the canonical, normalized store the dashboard
renders from. This script is its HubSpot feeder (the Anevo half is anevo_report.py):

  1. DISCOVER — every Email Calendar row that's Sent via HubSpot (has a "Hubspot Email"
     link) is mirrored into the Reporting DB, keyed by the HubSpot email id. The
     calendar's Test Group is inconsistent across A/B rows, so we NORMALIZE the Test
     to "<Test Stem> · <Audience>" (e.g. "LTS Relaunch · Residential") so variants
     group together. New sends appear automatically; existing rows are updated in place.
  2. REFRESH — pull live stats by email id (reusing reporting.hs_email) and write
     Open Rate / CTR / Recipients / Clicks / Bounce Rate / Unsubscribes (only columns
     this DB actually has — never the calendar-only ones).
  3. CROWN — per Test, highest CTR (open-rate tiebreak) AFTER a 7-day settle; until
     then the test stays winner-less (the dashboard shows the live leaning).

Anevo rows are never touched here.

  python scripts/hubspot_report.py
"""
import sys, re, datetime, tempfile, urllib.request, urllib.error
import notion, mockup, reporting

REPORTING_DB = "38e576a5-c12d-81b7-a5a8-d2e1e2f5433a"
EID = re.compile(r"/edit/(\d+)")
RICH = ("Recipients", "Open Rate", "CTR", "Clicks", "Bounce Rate", "Unsubscribes")  # cols this DB has


def _sel(pr, k): return (pr.get(k, {}).get("select") or {}).get("name")
def _txt(pr, k): return "".join(x.get("plain_text", "") for x in (pr.get(k, {}).get("rich_text") or []))
def _num(pr, k): return (pr.get(k, {}) or {}).get("number") or 0


def _all(db):
    rows, cur = [], None
    while True:
        body = {"page_size": 100}
        if cur:
            body["start_cursor"] = cur
        res = notion._call("POST", f"/databases/{db}/query", body)
        rows += res["results"]
        if not res.get("has_more"):
            break
        cur = res.get("next_cursor")
    return rows


def _reupload(url):
    if not url:
        return None
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        p = tempfile.mktemp(suffix=".png")
        open(p, "wb").write(urllib.request.urlopen(req, timeout=60).read())
        return mockup.upload_png(p, "variant.png")
    except Exception as e:
        print("    img reupload failed:", e)
        return None


def _norm_test(pr):
    """The calendar's Test Group is inconsistent across A/B; rebuild a canonical one."""
    stem = _txt(pr, "Test Stem") or (_sel(pr, "Campaign") or "Email")
    aud = _sel(pr, "Audience") or ""
    return f"{stem} · {aud}".strip(" ·")


def _img_url(pr):
    files = (pr.get("Email Image", {}) or {}).get("files", [])
    if not files:
        return None
    f = files[0]
    return (f.get("file") or {}).get("url") or (f.get("external") or {}).get("url")


def sync():
    # index existing Reporting rows: by email id, and by (Test, Variant) as a fallback
    by_eid, by_tv = {}, {}
    for r in _all(REPORTING_DB):
        pr = r["properties"]
        if _sel(pr, "Source") != "HubSpot":
            continue
        url = pr.get("HubSpot Link", {}).get("url") or ""
        m = EID.search(url)
        if m:
            by_eid[m.group(1)] = r["id"]
        t, v = _sel(pr, "Test"), _sel(pr, "Variant")
        if t and v:
            by_tv[(t, v)] = r["id"]

    n_new = n_upd = 0
    for r in _all(notion.CALENDAR_DB_ID):
        pr = r["properties"]
        if _sel(pr, "Status") != "Sent":
            continue
        url = (pr.get("Hubspot Email", {}) or {}).get("url")
        if not url:
            continue
        m = EID.search(url)
        if not m:
            continue
        eid = m.group(1)
        test = _norm_test(pr)
        variant = _sel(pr, "Variant") or "A"
        subject = _txt(pr, "Subject") or "".join(x.get("plain_text", "") for x in pr.get("Email", {}).get("title", []))
        audience = _sel(pr, "Audience")
        sent = (pr.get("Send Date", {}).get("date") or {}).get("start")
        link = url if "/content" in url else url.rstrip("/") + "/content"

        # live stats from HubSpot (freshest source of truth)
        try:
            stats = reporting.hs_email(eid).get("stats", {}) or {}
        except urllib.error.HTTPError as e:
            print("  HubSpot error", e.code, "for", subject[:40]); continue
        props = reporting.stats_to_props(stats)
        if not props:
            print("  no sends yet:", subject[:40]); continue
        props = {k: v for k, v in props.items() if k in RICH}  # drop calendar-only cols
        props.update({
            "Source": {"select": {"name": "HubSpot"}},
            "Test": {"select": {"name": test}},
            "Variant": {"select": {"name": variant}},
            "Subject": {"rich_text": [{"type": "text", "text": {"content": subject[:1900]}}]},
            "HubSpot Link": {"url": link},
        })
        if audience:
            props["Audience"] = {"select": {"name": audience}}
        if sent:
            props["Sent"] = {"date": {"start": sent[:10]}}

        pid = by_eid.get(eid) or by_tv.get((test, variant))
        if pid:
            notion._call("PATCH", f"/pages/{pid}", {"properties": props})
            n_upd += 1
            print(f"  ~ {test} {variant}  open {props['Open Rate']['number']*100:.1f}% ctr {props['CTR']['number']*100:.2f}%")
        else:
            props["Name"] = {"title": [{"type": "text", "text": {"content": f"{test} — {variant}"}}]}
            fid = _reupload(_img_url(pr))
            if fid:
                props["Email Image"] = {"files": [{"type": "file_upload", "name": "email.png", "file_upload": {"id": fid}}]}
            page = notion._call("POST", "/pages", {"parent": {"database_id": REPORTING_DB}, "properties": props})
            by_eid[eid] = page["id"]
            n_new += 1
            print(f"  + {test} {variant}  (new)")
    print(f"HubSpot rows: {n_new} added, {n_upd} refreshed")


def crown():
    """Winner per Test = highest CTR (open-rate tiebreak) after a 7-day settle."""
    today = datetime.date.today()
    groups = {}
    for r in _all(REPORTING_DB):
        pr = r["properties"]
        if _sel(pr, "Source") != "HubSpot":      # Anevo left winner-less by design
            continue
        t = _sel(pr, "Test")
        if t:
            groups.setdefault(t, []).append((r["id"], pr))
    for test, rows in groups.items():
        scored = [(pid, p) for pid, p in rows if (p.get("Open Rate", {}) or {}).get("number") is not None]
        winner = None
        days = None
        if len(scored) >= 2:
            sent_days = []
            for _, p in scored:
                sd = (p.get("Sent", {}).get("date") or {}).get("start")
                if sd:
                    try:
                        sent_days.append(datetime.date.fromisoformat(sd[:10]))
                    except ValueError:
                        pass
            if sent_days:
                days = (today - max(sent_days)).days
                if days >= reporting.SETTLE_DAYS:
                    winner = max(scored, key=lambda x: (_num(x[1], "CTR"), _num(x[1], "Open Rate")))[0]
        for pid, _ in rows:
            notion._call("PATCH", f"/pages/{pid}", {"properties": {"Winner": {"checkbox": pid == winner}}})
        if winner:
            print(f"  🏆 '{test}' crowned")
        elif len(scored) >= 2 and days is not None:
            print(f"  '{test}' pending — settles in {max(0, reporting.SETTLE_DAYS - days)} day(s)")


if __name__ == "__main__":
    sync()
    crown()
