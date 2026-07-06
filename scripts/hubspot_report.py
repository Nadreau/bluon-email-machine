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
import sys, re, json, datetime, tempfile, urllib.request, urllib.error
import notion, mockup, reporting

PORTAL = "6885872"

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


def _body_test_extras(page_id):
    """For a page-based body A/B (both versions on one page under Variant A/B
    headings): the first line each version has that the other doesn't (per-variant
    dashboard captions, since the subject is identical) + version B's own mockup
    url from the page's Mockup section. Returns (a_cap, b_cap, b_img) or None."""
    try:
        info = notion.parse_draft_page(page_id)
    except Exception:
        return None
    if not info.get("body_lines_b"):
        return None
    a, b = info["body_lines"], info["body_lines_b"]
    a_only = [x for x in a if x not in b]
    b_only = [y for y in b if y not in a]
    a_cap = a_only[0] if a_only else None
    b_cap = b_only[0] if b_only else None
    b_img, in_mock, after_b = None, False, False
    try:
        for blk in notion._call("GET", f"/blocks/{page_id}/children?page_size=100")["results"]:
            t = blk["type"]
            txt = "".join(x.get("plain_text", "") for x in (blk.get(t, {}).get("rich_text") or []))
            if t == "heading_3" and notion.MOCKUP_HEADING in txt:
                in_mock = True; continue
            if in_mock and t == "paragraph" and "Variant B" in txt:
                after_b = True; continue
            if in_mock and after_b and t == "image":
                f = blk["image"]
                b_img = (f.get("file") or {}).get("url") or (f.get("external") or {}).get("url")
                break
    except Exception:
        pass
    return (a_cap, b_cap, b_img)


def _campaign_map():
    """primaryEmailCampaignId -> email id, so an A/B master can find its variation
    (the variation's primaryEmailCampaignId = the master's + 2; they share a campaign)."""
    out, after = {}, None
    while True:
        u = "https://api.hubapi.com/marketing/v3/emails?limit=100" + (f"&after={after}" if after else "")
        req = urllib.request.Request(u, headers={"Authorization": f"Bearer {reporting.HS_TOKEN}"})
        with urllib.request.urlopen(req, timeout=60) as r:
            d = json.load(r)
        for e in d.get("results", []):
            p = e.get("primaryEmailCampaignId")
            if p is not None:
                out[int(p)] = e["id"]
        after = ((d.get("paging", {}) or {}).get("next", {}) or {}).get("after")
        if not after:
            break
    return out


def _upsert_row(eid, subject, stats, *, test, variant, audience, sent, by_eid, by_tv, img_url=None):
    """Create/refresh one Reporting-DB row for a HubSpot email + variant. Returns
    'new' / 'upd' / None (nothing sent yet)."""
    base = reporting.stats_to_props(stats)
    if not base:
        return None
    props = {k: v for k, v in base.items() if k in RICH}
    props.update({
        "Source": {"select": {"name": "HubSpot"}},
        "Test": {"select": {"name": test}},
        "Variant": {"select": {"name": variant}},
        "Subject": {"rich_text": [{"type": "text", "text": {"content": (subject or "")[:1900]}}]},
        "HubSpot Link": {"url": f"https://app.hubspot.com/email/{PORTAL}/edit/{eid}/content"},
    })
    if audience:
        props["Audience"] = {"select": {"name": audience}}
    if sent:
        props["Sent"] = {"date": {"start": sent[:10]}}
    pid = by_eid.get(str(eid)) or by_tv.get((test, variant))
    if pid:
        notion._call("PATCH", f"/pages/{pid}", {"properties": props})
        return "upd"
    props["Name"] = {"title": [{"type": "text", "text": {"content": f"{test} — {variant}"}}]}
    if img_url:
        fid = _reupload(img_url)
        if fid:
            props["Email Image"] = {"files": [{"type": "file_upload", "name": "email.png", "file_upload": {"id": fid}}]}
    page = notion._call("POST", "/pages", {"parent": {"database_id": REPORTING_DB}, "properties": props})
    by_eid[str(eid)] = page["id"]
    return "new"


def sync():
    """Mirror every Sent calendar row into the Reporting DB with TRUE per-version
    stats (A/B masters are split via reporting.ab_breakdown — never the blended
    counters, which mix in the winner-remainder blast and can flatter the wrong
    letter), record HubSpot's own A/B winners, and keep orphaned reporting rows
    refreshing. Returns {test: 'A'|'B'} of HubSpot-decided winners for crown()."""
    # index existing Reporting rows by email id + (Test, Variant). A colliding
    # (Test, Variant) key is DROPPED, not last-write-wins — a wrong fallback match
    # once bled one test's stats onto another test's row.
    by_eid, by_tv, tv_dupes = {}, {}, set()
    for r in _all(REPORTING_DB):
        pr = r["properties"]
        if _sel(pr, "Source") != "HubSpot":
            continue
        m = EID.search(pr.get("HubSpot Link", {}).get("url") or "")
        if m:
            by_eid[m.group(1)] = r["id"]
        t, v = _sel(pr, "Test"), _sel(pr, "Variant")
        if t and v:
            if (t, v) in by_tv:
                tv_dupes.add((t, v))
            else:
                by_tv[(t, v)] = r["id"]
    for k in tv_dupes:
        by_tv.pop(k, None)

    cal_rows = _all(notion.CALENDAR_DB_ID)
    claimed = set()   # eids some calendar row links — a variation pull must not double-anchor
    for r in cal_rows:
        m = EID.search((r["properties"].get("Hubspot Email", {}) or {}).get("url") or "")
        if m:
            claimed.add(m.group(1))

    cmap = _campaign_map()
    hs_decided = {}   # test -> 'A'|'B' (HubSpot's own A/B outcome)
    visited = set()
    n_new = n_upd = 0
    for r in cal_rows:
        pr = r["properties"]
        if _sel(pr, "Status") != "Sent":
            continue
        m = EID.search((pr.get("Hubspot Email", {}) or {}).get("url") or "")
        if not m:
            continue
        eid = m.group(1)
        visited.add(eid)
        test = _norm_test(pr)
        audience = _sel(pr, "Audience")
        sent = (pr.get("Send Date", {}).get("date") or {}).get("start")
        subject = _txt(pr, "Subject") or "".join(x.get("plain_text", "") for x in pr.get("Email", {}).get("title", []))
        # body tests share one subject — caption each variant with the line that
        # actually differs between the page's two versions (+ B's own mockup)
        b_caption = b_img = None
        if _sel(pr, "Testing") == "Header / Hook":
            ex = _body_test_extras(r["id"])
            if ex:
                a_cap, b_caption, b_img = ex
                if a_cap:
                    subject = a_cap
            elif _txt(pr, "Hook"):
                subject = _txt(pr, "Hook")
        cal_variant = _sel(pr, "Variant")
        try:
            email = reporting.hs_email(eid)
        except urllib.error.HTTPError as e:
            print("  HubSpot error", e.code, "for", subject[:40]); continue

        stats = email.get("stats", {}) or {}
        bd = reporting.ab_breakdown(email)
        if bd:
            stats = bd["a"]           # true version-A sample, not the blended master
            if bd["winner"]:
                hs_decided[test] = bd["winner"]

        # A = the linked email (an already-split calendar row keeps its own A/B letter)
        st = _upsert_row(eid, subject, stats, test=test, variant=cal_variant or "A",
                         audience=audience, sent=sent, by_eid=by_eid, by_tv=by_tv, img_url=_img_url(pr))
        if st == "new": n_new += 1
        elif st == "upd": n_upd += 1
        elif st is None: print("  no sends yet:", subject[:40])

        # B = the native A/B variation, pulled straight from HubSpot. Runs for a
        # consolidated row (no variant of its own) and ALSO as a safety net for a
        # fanned Variant-A row whose B sibling never got its own link — otherwise
        # that B email silently never reaches the Reporting DB.
        if email.get("isAb") and email.get("primaryEmailCampaignId") is not None and cal_variant in (None, "A"):
            var = cmap.get(int(email["primaryEmailCampaignId"]) + 2)
            if var and str(var) != eid and (not cal_variant or str(var) not in claimed):
                try:
                    ve = reporting.hs_email(str(var))
                    visited.add(str(var))
                    st2 = _upsert_row(str(var), ve.get("subject", ""), ve.get("stats", {}) or {},
                                      test=test, variant="B", audience=audience, sent=sent,
                                      by_eid=by_eid, by_tv=by_tv, img_url=_img_url(pr))
                    if st2 == "new": n_new += 1; print(f"  + {test} B (A/B variation {var})")
                    elif st2 == "upd": n_upd += 1
                except urllib.error.HTTPError:
                    pass

    # orphaned reporting rows (calendar anchor archived/relinked) — keep their
    # stats live straight from HubSpot instead of freezing them mid-test
    n_orph = 0
    for eid, pid in by_eid.items():
        if eid in visited:
            continue
        try:
            email = reporting.hs_email(eid)
        except urllib.error.HTTPError:
            continue
        stats = email.get("stats", {}) or {}
        bd = reporting.ab_breakdown(email)
        if bd:
            stats = bd["a"]
        base = reporting.stats_to_props(stats)
        if not base:
            continue
        notion._call("PATCH", f"/pages/{pid}",
                     {"properties": {k: v for k, v in base.items() if k in RICH}})
        n_orph += 1
    print(f"HubSpot rows: {n_new} added, {n_upd} refreshed, {n_orph} orphans kept live")
    return hs_decided


def normalize_tests():
    """A/B variants of one test must share a Test value so the dashboard pairs them.
    The calendar's Test Stem is inconsistent across A/B rows (one gets "LTS Relaunch",
    the other falls back to "Email"), which splits a test into singletons. Unify per
    (audience, send DATE) — a month-wide key merged DIFFERENT tests whenever two sends
    to the same audience landed in one calendar month (e.g. Wave 2 Jul 1 + the next
    test Jul 8), collapsing them into one bogus 4-variant group. A/B siblings always
    share the exact send date, so the date key pairs them and nothing else. The stem
    pick is majority-vote (deterministic), not first-row-wins."""
    groups = {}
    for r in _all(REPORTING_DB):
        pr = r["properties"]
        if _sel(pr, "Source") != "HubSpot":
            continue
        aud = _sel(pr, "Audience") or ""
        day = ((pr.get("Sent", {}).get("date") or {}).get("start") or "")[:10]
        groups.setdefault((aud, day), []).append((r["id"], _sel(pr, "Test") or ""))
    for (aud, _day), members in groups.items():
        stems = [t.split(" · ")[0] for _, t in members if t and not t.startswith("Email")]
        stem = max(set(stems), key=lambda s: (stems.count(s), s)) if stems else "Email"
        target = f"{stem} · {aud}".strip(" ·")
        for pid, test in members:
            if test != target:
                notion._call("PATCH", f"/pages/{pid}", {"properties": {"Test": {"select": {"name": target}}}})
                print(f"  = unified Test → {target}")


def crown(hs_decided=None):
    """Winner per Test: HubSpot's own A/B decision when it exists (it already drove
    the remainder blast — no reason to wait or re-derive), else highest CTR
    (open-rate tiebreak) after a 7-day settle."""
    hs_decided = hs_decided or {}
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
        pick = hs_decided.get(test)
        if pick:
            lettered = {_sel(p, "Variant"): pid for pid, p in rows}
            winner = lettered.get(pick)
            if winner:
                for pid, _ in rows:
                    notion._call("PATCH", f"/pages/{pid}", {"properties": {"Winner": {"checkbox": pid == winner}}})
                print(f"  🏆 '{test}': {pick} (HubSpot's A/B decision)")
                continue
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
    decided = sync()
    normalize_tests()
    crown(decided)
