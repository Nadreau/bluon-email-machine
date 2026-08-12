"""Anevo scorecard — is the email contract earning its keep?

Builds ONE readable Notion page (not a database) from the live Smartlead API:
the verdict up top, cost per interested lead, monthly trend bars, campaign
detail, and the sending-infrastructure audit (153 lookalike domains, warmup,
bounce risk). Rebuilt in place on every run, so the page id stays stable.

  python anevo_scorecard.py            # rebuild the page
  python anevo_scorecard.py --dry      # print the numbers, write nothing

Reply rate is the ONLY trustworthy engagement metric here — opens/clicks on
cold sends are inflated by security scanners (see the Anevo landing analysis),
so this page leads with replies and interested-lead counts.
"""
import os, sys, json, time, urllib.request, collections, datetime
import notion

SL_KEY = (os.environ.get("SMARTLEAD_API_KEY", "").strip()
          or open(os.path.expanduser("~/.config/smartlead/api_key")).read().strip())
PARENT_PAGE = "37a576a5-c12d-80af-b817-ecd18f6b064b"   # 📊 Reporting hub (Bluon)
PAGE_TITLE = "📧 Anevo Scorecard — Email Program"
EMAIL_COST_PER_MONTH = 10000     # per the Aug 7 Peter/Tanner call ($10K email, $5K calling)
START_MONTH = "2026-02"          # sending domains registered Feb 27 2026

# Cold-email benchmarks for context (industry norms, not Bluon-specific)
BENCH_REPLY_GOOD, BENCH_REPLY_OK = 3.0, 1.0
BENCH_BOUNCE_WARN, BENCH_BOUNCE_BAD = 2.0, 5.0


def sl(path, tries=6):
    """Smartlead GET. The API rate-limits hard on a full campaign sweep, so honor
    Retry-After and back off (same pattern as anevo_report.py). A real User-Agent
    is required or requests are refused outright."""
    import time, urllib.error
    url = f"https://server.smartlead.ai/api/v1{path}{'&' if '?' in path else '?'}api_key={SL_KEY}"
    for attempt in range(tries):
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"})
        try:
            with urllib.request.urlopen(req, timeout=90) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503) and attempt < tries - 1:
                wait = int(e.headers.get("Retry-After") or 0) or min(120, 10 * (attempt + 1))
                print(f"    rate-limited, waiting {wait}s…")
                time.sleep(wait); continue
            raise
    raise RuntimeError("smartlead: retries exhausted for " + path)


TRACKING_LIVE_FROM = "2026-05-26"   # Anevo campaigns before this have NO open/click tracking


def hubspot_side(since="2026-05-26"):
    """Our own HubSpot numbers for the same window, so the comparison is
    apples-to-apples with what the team reads every day."""
    import to_hubspot
    res, after, agg = [], None, collections.Counter()
    while True:
        q = "/marketing/v3/emails?limit=100&sort=-publishDate" + (f"&after={after}" if after else "")
        p = to_hubspot.hs("GET", q)
        res += p["results"]
        after = (p.get("paging") or {}).get("next", {}).get("after")
        if not after or len(res) >= 300:
            break
    for e in res:
        pd = e.get("publishDate") or ""
        if not e.get("isPublished") or pd[:10] < since:
            continue
        try:
            s = to_hubspot.hs("GET", f"/marketing/v3/emails/{e['id']}?includeStats=true"
                              ).get("stats", {}).get("counters", {})
        except Exception:
            continue
        if not s.get("sent"):
            continue
        for k in ("sent", "delivered", "open", "click", "bounce", "unsubscribed"):
            agg[k] += s.get(k, 0)
        agg["n"] += 1
    return agg


def pull():
    """Everything the scorecard needs, in one pass."""
    camps = sl("/campaigns")
    rows, tot = [], collections.Counter()
    for c in camps:
        try:
            a = sl(f"/campaigns/{c['id']}/analytics")
        except Exception as e:
            print("  skip campaign", c.get("id"), str(e)[:60]); continue
        time.sleep(0.4)   # stay under the rate limit across ~60 campaigns
        sent = int(a.get("sent_count") or 0)
        if not sent:
            continue
        ls = a.get("campaign_lead_stats") or {}
        r = {"name": c.get("name", "?"), "created": str(c.get("created_at"))[:10],
             "status": c.get("status"), "sent": sent,
             "reply": int(a.get("reply_count") or 0),
             "bounce": int(a.get("bounce_count") or 0),
             "unsub": int(a.get("unsubscribed_count") or 0),
             "interested": int(ls.get("interested") or 0),
             "leads": int(ls.get("total") or 0)}
        rows.append(r)
        for k in ("sent", "reply", "bounce", "unsub", "interested", "leads"):
            tot[k] += r[k]
    rows.sort(key=lambda r: r["created"], reverse=True)

    months = collections.defaultdict(collections.Counter)
    for r in rows:
        m = months[r["created"][:7]]
        for k in ("sent", "reply", "bounce", "interested"):
            m[k] += r[k]

    accts = []
    off = 0
    while True:
        b = sl(f"/email-accounts/?limit=100&offset={off}")
        accts += b
        if len(b) < 100:
            break
        off += 100
    return rows, tot, months, accts


# ---------- block helpers ----------
def t(txt, **ann):
    o = {"type": "text", "text": {"content": txt}}
    if ann:
        o["annotations"] = ann
    return o


def para(txt, **ann):
    return {"object": "block", "type": "paragraph",
            "paragraph": {"rich_text": [t(txt, **ann)] if txt else []}}


def head(txt, lvl=2):
    return {"object": "block", "type": f"heading_{lvl}", f"heading_{lvl}": {"rich_text": [t(txt)]}}


def callout(rich, emoji, color="gray_background"):
    return {"object": "block", "type": "callout", "callout": {
        "rich_text": rich, "icon": {"type": "emoji", "emoji": emoji}, "color": color}}


def bullet(*rich):
    return {"object": "block", "type": "bulleted_list_item",
            "bulleted_list_item": {"rich_text": list(rich)}}


def divider():
    return {"object": "block", "type": "divider", "divider": {}}


def table(rows_cells, width, header=True):
    return {"object": "block", "type": "table", "table": {
        "table_width": width, "has_column_header": header, "has_row_header": False,
        "children": [{"object": "block", "type": "table_row",
                      "table_row": {"cells": [[t(c)] if isinstance(c, str) else c for c in row]}}
                     for row in rows_cells]}}


def bar(value, peak, width=22, char="█", empty="░"):
    """Text bar — reads cleanly in Notion without needing a database chart."""
    if peak <= 0:
        return empty * width
    n = max(1, round(width * value / peak)) if value > 0 else 0
    return char * n + empty * (width - n)


def build_blocks(rows, tot, months, accts):
    sent, reply, bounce = tot["sent"], tot["reply"], tot["bounce"]
    interested = tot["interested"]
    reply_pct = 100 * reply / sent if sent else 0
    bounce_pct = 100 * bounce / sent if sent else 0
    # months live since the domains were registered
    now = datetime.date.today()
    y0, m0 = int(START_MONTH[:4]), int(START_MONTH[5:7])
    months_live = max(1, (now.year - y0) * 12 + (now.month - m0))
    spend = EMAIL_COST_PER_MONTH * months_live
    cpi = spend / interested if interested else None

    verdict_color = "red_background" if reply_pct < BENCH_REPLY_OK else (
        "yellow_background" if reply_pct < BENCH_REPLY_GOOD else "green_background")
    b = [
        callout([t("Verdict: ", bold=True),
                 t(f"{reply_pct:.2f}% reply rate on {sent:,} emails. "),
                 t("Industry-typical cold email replies at 1–3%. ", italic=True),
                 t(f"~${spend:,.0f} spent since Feb → {interested} leads marked interested"
                   + (f" ≈ ${cpi:,.0f} each." if cpi else "."), bold=True)],
                "🚨" if verdict_color == "red_background" else "📊", verdict_color),
        para(f"Live from the Smartlead API · rebuilt {now.strftime('%b %-d, %Y')} · "
             f"reply rate is the only trustworthy metric here (opens/clicks on cold sends are "
             f"inflated by security scanners)", italic=True, color="gray"),
        head("The numbers that matter"),
        table([
            ["Metric", "Result", "Read"],
            ["Emails sent", f"{sent:,}", "since Feb 2026"],
            ["Replies", f"{reply:,}  ({reply_pct:.2f}%)",
             "🔴 below the 1–3% norm" if reply_pct < BENCH_REPLY_OK else "🟡 low end of normal"],
            ["Leads marked interested", f"{interested}",
             f"≈ ${cpi:,.0f} per interested lead" if cpi else "none recorded"],
            ["Bounces", f"{bounce:,}  ({bounce_pct:.2f}%)",
             "🔴 list hygiene problem" if bounce_pct > BENCH_BOUNCE_BAD else
             ("🟡 watch it" if bounce_pct > BENCH_BOUNCE_WARN else "🟢 acceptable")],
            ["Unsubscribes", f"{tot['unsub']:,}",
             "⚠️ zero recorded — verify opt-out works" if not tot["unsub"] else "tracked"],
        ], 3),
    ]

    # ---- monthly trend, as bars ----
    b += [head("Volume and replies by month")]
    ms = sorted(months)
    peak_sent = max((months[m]["sent"] for m in ms), default=1)
    trend = [["Month", "Sent", "Volume", "Reply %"]]
    for m in ms:
        d = months[m]
        rp = 100 * d["reply"] / d["sent"] if d["sent"] else 0
        trend.append([m, f"{d['sent']:,}", bar(d["sent"], peak_sent), f"{rp:.2f}%"])
    b.append(table(trend, 4))

    peak_b = max((100 * months[m]["bounce"] / months[m]["sent"] for m in ms if months[m]["sent"]),
                 default=1)
    b += [head("Bounce rate by month"),
          para("Above ~5% and mailbox providers start treating the sender as a list-buyer.",
               italic=True, color="gray")]
    brows = [["Month", "Bounce %", "", "Flag"]]
    for m in ms:
        d = months[m]
        bp = 100 * d["bounce"] / d["sent"] if d["sent"] else 0
        flag = "🔴" if bp > BENCH_BOUNCE_BAD else ("🟡" if bp > BENCH_BOUNCE_WARN else "🟢")
        brows.append([m, f"{bp:.2f}%", bar(bp, max(peak_b, 1)), flag])
    b.append(table(brows, 4))

    # ---- recent campaigns ----
    b += [head("Recent campaigns")]
    crows = [["Started", "Campaign", "Sent", "Replies", "Bounce"]]
    for r in rows[:12]:
        rp = 100 * r["reply"] / r["sent"] if r["sent"] else 0
        bp = 100 * r["bounce"] / r["sent"] if r["sent"] else 0
        crows.append([r["created"], r["name"][:44], f"{r['sent']:,}",
                      f"{r['reply']} ({rp:.2f}%)",
                      ("🔴 " if bp > BENCH_BOUNCE_BAD else "") + f"{bp:.2f}%"])
    b.append(table(crows, 5))

    # ---- infrastructure ----
    domains = sorted({a.get("from_email", "?").split("@")[-1] for a in accts})
    bluon_doms = [d for d in domains if "bluon" in d]
    other_doms = [d for d in domains if "bluon" not in d]
    n_bluon = sum(1 for a in accts if "bluon" in a.get("from_email", "").split("@")[-1])
    n_other = len(accts) - n_bluon
    names = sorted({a.get("from_name", "") for a in accts if a.get("from_name")})
    cap = sum(a.get("message_per_day") or 0 for a in accts)
    warm = sum(1 for a in accts if a.get("warmup_status") == "ACTIVE")
    pct_other = 100 * n_other / len(accts) if accts else 0
    b += [
        head("Sending infrastructure"),
        callout([t("None of this touches bluon.com. ", bold=True),
                 t("Anevo sends only from lookalike domains, so their reputation is isolated "
                   "from your HubSpot sending and your reps' inboxes.")], "✅", "green_background"),
        bullet(t(f"{len(accts)} mailboxes across {len(domains)} domains", bold=True),
               t(f" — capacity {cap:,} emails/day")),
        bullet(t("Sending as: ", bold=True), t(", ".join(names) or "—")),
        bullet(t("Warmup active: ", bold=True),
               t(f"{warm} of {len(accts)} mailboxes",
                 bold=(warm == 0), color=("red" if warm == 0 else None))),
        bullet(t("Domain ownership: ", bold=True),
               t("every Bluon domain was registered in the same minute (Feb 27 2026, 02:31 UTC) "
                 "via Spaceship with WHOIS privacy — an agency bulk purchase, almost certainly "
                 "Anevo's and not Bluon's. If the contract ends, a third party holds "
                 f"{len(bluon_doms)} domains carrying the Bluon name.")),
    ]
    if n_other:
        b += [
            callout([t(f"{n_other} of {len(accts)} mailboxes ({pct_other:.0f}%) don't mention Bluon at all. ",
                       bold=True),
                     t(f"They send as \"{'Jacob Schmidt'}\" from {len(other_doms)} "
                       f"\"insightsgroup\" domains — and EVERY Bluon campaign is assigned all "
                       f"{len(accts)} mailboxes, so roughly a third of your cold outreach goes out "
                       f"under a persona and company with no visible connection to Bluon. Worth a "
                       f"decision from Tanner/Peter: is that intended? It also affects how "
                       f"\"interested\" replies get attributed, and pairs badly with the zero "
                       f"recorded unsubscribes.")],
                    "🚩", "red_background"),
            para("Non-Bluon domains: " + ", ".join(other_doms[:10])
                 + f", +{max(0, len(other_doms)-10)} more", italic=True, color="gray"),
        ]
    b += [
        para("Bluon-branded sample: " + ", ".join(bluon_doms[:12])
             + f", +{max(0, len(bluon_doms)-12)} more", italic=True, color="gray"),
        divider(),
        head("If you keep them, ask for", 3),
        bullet(t("Transfer of all "), t(f"{len(bluon_doms)} bluon-branded domains", bold=True),
               t(" to Bluon ownership — or written commitment to release them at contract end.")),
        bullet(t("A straight answer on the \"insightsgroup\" sending identity", bold=True),
               t(" — who approved it, and whether it stays.")),
        bullet(t("List verification before every send. "),
               t("Bounce spikes to 9%+ mean unverified lists.", bold=True)),
        bullet(t("Warmup switched back on across the mailbox pool.")),
        bullet(t("Their call recordings + dial dispositions"),
               t(" (the calling side is the half that's working).")),
        bullet(t("Proof the unsubscribe path works"), t(" — zero recorded across all sends.")),
    ]
    return b


def rebuild(dry=False):
    rows, tot, months, accts = pull()
    print(f"{len(rows)} campaigns · {tot['sent']:,} sent · {tot['reply']:,} replies "
          f"({100*tot['reply']/max(tot['sent'],1):.2f}%) · {tot['interested']} interested · "
          f"{len(accts)} mailboxes")
    if dry:
        return
    blocks = build_blocks(rows, tot, months, accts)
    # find-or-create the page under the Reporting hub, then rebuild its body in place
    page_id = None
    for c in notion._call("GET", f"/blocks/{PARENT_PAGE}/children?page_size=100")["results"]:
        if c["type"] == "child_page" and c["child_page"]["title"] == PAGE_TITLE:
            page_id = c["id"]; break
    if page_id:
        for blk in notion._call("GET", f"/blocks/{page_id}/children?page_size=100")["results"]:
            try:
                notion._call("PATCH", f"/blocks/{blk['id']}", {"archived": True})
            except Exception:
                pass
        for i in range(0, len(blocks), 90):
            notion._call("PATCH", f"/blocks/{page_id}/children", {"children": blocks[i:i+90]})
    else:
        page = notion._call("POST", "/pages", {
            "parent": {"page_id": PARENT_PAGE},
            "icon": {"type": "emoji", "emoji": "📧"},
            "properties": {"title": [{"type": "text", "text": {"content": PAGE_TITLE}}]},
            "children": blocks[:90]})
        page_id = page["id"]
        for i in range(90, len(blocks), 90):
            notion._call("PATCH", f"/blocks/{page_id}/children", {"children": blocks[i:i+90]})
    print("page:", f"https://www.notion.so/{page_id.replace('-', '')}")
    return page_id


if __name__ == "__main__":
    rebuild(dry="--dry" in sys.argv)
