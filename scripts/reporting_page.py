"""Render the "📊 Email Reporting" dashboard page from the Email Reporting DB.

Mirrors the Bluon ads reporting style (the Meta/Google dashboards): one clean page,
broken down into obvious sections with plain-English text between them, grouped
WEEK -> EMAIL -> VARIATION, each variation showing its mockup. The page is fully
WIPED AND REBUILT every run (the Google-ads-dashboard pattern) so the organization
never drifts — edit THIS SCRIPT, not the page.

Source of truth = the Email Reporting DB (one row per sent variant, HubSpot + Anevo).
This script only READS that DB and only WRITES the dashboard page; it never touches
the DB or the calendar.

  python scripts/reporting_page.py
"""
import sys, datetime, tempfile, urllib.request
import notion, mockup

REPORT_PAGE = "38e576a5-c12d-8187-9c21-f82642db1fa1"      # the rendered dashboard page
SOURCE_DB   = "38e576a5-c12d-81b7-a5a8-d2e1e2f5433a"      # Email Reporting — Automated (raw rows)
MAX_WEEKS   = 26                                          # cap visible weeks (newest first); logs if it truncates

GRAY = "gray_background"


# ---------- small helpers ----------
def rt(s, *, bold=False, italic=False, code=False, color=None):
    o = {"type": "text", "text": {"content": s}}
    ann = {}
    if bold: ann["bold"] = True
    if italic: ann["italic"] = True
    if code: ann["code"] = True
    if color: ann["color"] = color
    if ann: o["annotations"] = ann
    return o

def pctf(x):
    return "—" if x is None else f"{x*100:.1f}%"

def comma(n):
    return "—" if n is None else f"{int(n):,}"

def kfmt(n):
    n = n or 0
    return f"{n/1000:.1f}K" if n >= 1000 else str(int(n))


def _num(pr, k):
    return pr.get(k, {}).get("number")

def _sel(pr, k):
    return (pr.get(k, {}).get("select") or {}).get("name")

def _txt(pr, k):
    return "".join(x.get("plain_text", "") for x in (pr.get(k, {}).get("rich_text") or []))

def _img_url(pr):
    files = (pr.get("Email Image", {}) or {}).get("files", [])
    if not files:
        return None
    f = files[0]
    return (f.get("file") or {}).get("url") or (f.get("external") or {}).get("url")


# ---------- data ----------
def load_rows():
    rows, cur = [], None
    while True:
        body = {"page_size": 100}
        if cur:
            body["start_cursor"] = cur
        res = notion._call("POST", f"/databases/{SOURCE_DB}/query", body)
        for r in res["results"]:
            pr = r["properties"]
            sent = (pr.get("Sent", {}).get("date") or {}).get("start")
            rows.append({
                "source": _sel(pr, "Source") or "HubSpot",
                "test": _sel(pr, "Test") or _txt(pr, "Name") or "Email",
                "audience": _sel(pr, "Audience") or "",
                "variant": _sel(pr, "Variant") or "",
                "subject": _txt(pr, "Subject") or _txt(pr, "Name"),
                "open": _num(pr, "Open Rate"),
                "ctr": _num(pr, "CTR"),
                "recipients": _num(pr, "Recipients"),
                "clicks": _num(pr, "Clicks"),
                "replies": _num(pr, "Replies"),
                "leads": _num(pr, "Leads (Interested)"),
                "winner": bool(pr.get("Winner", {}).get("checkbox")),
                "link": pr.get("HubSpot Link", {}).get("url"),
                "img": _img_url(pr),
                "sent": sent,
                "week": notion._week_of(sent) if sent else "Undated",
                "wk_key": sent[:10] if sent else "0000",
            })
        if not res.get("has_more"):
            break
        cur = res.get("next_cursor")
    return rows


# ---------- page blocks ----------
def clear_page(page_id):
    cur = None
    while True:
        q = f"/blocks/{page_id}/children?page_size=100" + (f"&start_cursor={cur}" if cur else "")
        d = notion._call("GET", q)
        for b in d["results"]:
            if not b.get("archived") and not b.get("in_trash"):
                notion._call("PATCH", f"/blocks/{b['id']}", {"archived": True})
        if not d.get("has_more"):
            break
        cur = d.get("next_cursor")


def _reupload(url):
    """Notion-hosted file URLs are signed/expiring — re-host the mockup so it
    survives the daily wipe. Returns a file_upload id, or None on failure."""
    if not url:
        return None
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        data = urllib.request.urlopen(req, timeout=15).read()
        p = tempfile.mktemp(suffix=".png")
        open(p, "wb").write(data)
        return mockup.upload_png(p, "variant.png")
    except Exception as e:
        print("    img reupload failed:", e)
        return None


def _email_callout(variants):
    """HubSpot A/B read — both versions sent, which is doing better (no winner/contest
    framing). Once HubSpot's own test window decided (Winner checkbox), say so plainly:
    that version also went to the rest of the list, which is why the per-version numbers
    below are test-window sends, not the whole blast. HubSpot only; Anevo cold-email is
    rendered as its own table."""
    fmt = lambda v: f"{v['variant']} {pctf(v['ctr'])} CTR"
    keyf = lambda v: (v["ctr"] or 0, v["open"] or 0)
    ranked = sorted(variants, key=keyf, reverse=True)
    lead = "Both versions sent.  " if len(variants) == 2 else "All versions sent.  "
    parts = [rt(lead, bold=True)]
    crowned = next((v for v in variants if v.get("winner")), None)
    if crowned and len(variants) >= 2:
        parts += [rt(f"{crowned['variant']} did better in the test window and went out to the rest of the list — "),
                  rt(" vs ".join(fmt(v) for v in ranked))]
    elif len(variants) >= 2 and keyf(ranked[0]) != keyf(ranked[1]):
        parts += [rt(f"{ranked[0]['variant']} is doing better so far — "),
                  rt(" vs ".join(fmt(v) for v in ranked))]
    else:
        parts += [rt("performing about the same — "),
                  rt(" · ".join(fmt(v) for v in variants))]
    return {"object": "block", "type": "callout",
            "callout": {"icon": {"emoji": "📊"}, "color": GRAY, "rich_text": parts}}


def _cell(text, **ann):
    return [rt(str(text), **ann)]


def _anevo_table(rows):
    """Cold-email campaigns as a compact table — Campaign | Sent | Open | Replies |
    Interested. Replies + interested leads are the real cold-email KPIs (opens are noisy)."""
    rows = sorted(rows, key=lambda r: r["wk_key"], reverse=True)
    trs = [{"type": "table_row", "table_row": {"cells": [
        _cell("Campaign", bold=True), _cell("Sent", bold=True), _cell("Open", bold=True),
        _cell("Replies", bold=True), _cell("Interested", bold=True)]}}]
    for r in rows:
        trs.append({"type": "table_row", "table_row": {"cells": [
            _cell((r["subject"] or "—")[:60]),
            _cell(comma(r["recipients"])),
            _cell(pctf(r["open"])),
            _cell(int(r["replies"] or 0)),
            _cell(int(r["leads"] or 0)),
        ]}})
    return {"object": "block", "type": "table",
            "table": {"table_width": 5, "has_column_header": True, "has_row_header": False, "children": trs}}


def _variant_caption(v):
    head = [rt(f"{v['variant']}", bold=True), rt(f"  {v['subject']}")]
    stats = rt(f"\n{pctf(v['open'])} open · {pctf(v['ctr'])} CTR · {comma(v['recipients'])} sent · {v['clicks'] or 0} clicks", color="gray")
    return {"object": "block", "type": "paragraph", "paragraph": {"rich_text": head + [stats]}}


def _variants_block(variants):
    """A column per variant: mockup image + caption. Falls back to stacked blocks
    for a single variant (column_list needs >=2 columns)."""
    cols = []
    for v in variants:
        kids = []
        fid = _reupload(v["img"])
        if fid:
            kids.append({"object": "block", "type": "image",
                         "image": {"type": "file_upload", "file_upload": {"id": fid}}})
        kids.append(_variant_caption(v))
        cols.append({"object": "block", "type": "column", "column": {"children": kids}})
    if len(cols) >= 2:
        return [{"object": "block", "type": "column_list", "column_list": {"children": cols}}]
    # single variant — no column_list
    return cols[0]["column"]["children"]


def build():
    rows = load_rows()
    if not rows:
        print("no rows in source DB; nothing to render")
        return
    # group EVERYTHING by week; within a week, HubSpot A/B tests (by test) + Anevo list
    weeks = {}
    for r in rows:
        w = weeks.setdefault(r["week"], {"wk_key": r["wk_key"], "hs": {}, "an": []})
        w["wk_key"] = max(w["wk_key"], r["wk_key"])
        if r["source"] == "Anevo":
            w["an"].append(r)
        else:
            w["hs"].setdefault(r["test"], []).append(r)
    for w in weeks.values():
        for vs in w["hs"].values():
            vs.sort(key=lambda r: r["variant"] or "Z")
    order = sorted(weeks.items(), key=lambda kv: kv[1]["wk_key"], reverse=True)
    if len(order) > MAX_WEEKS:
        print(f"NOTE: showing newest {MAX_WEEKS} of {len(order)} weeks (older live in the source DB).")
        order = order[:MAX_WEEKS]

    n_hs = sum(len(wk["hs"]) for _, wk in order)
    n_an = sum(len(wk["an"]) for _, wk in order)
    ts = datetime.datetime.now().strftime("%b %-d")

    clear_page(REPORT_PAGE)
    notion._call("PATCH", f"/blocks/{REPORT_PAGE}/children", {"children": [
        {"object": "block", "type": "callout",
         "callout": {"icon": {"emoji": "📬"}, "color": "blue_background", "rich_text": [
             rt("Every send, newest week first — HubSpot A/B tests and Anevo cold-email campaigns, together by week."),
             rt(f"\n{n_hs} HubSpot tests · {n_an} Anevo campaigns · {len(order)} weeks · updated {ts}", color="gray"),
         ]}},
    ]})

    for week, wk in order:
        hs, an = wk["hs"], wk["an"]
        bits = ([f"{len(hs)} HubSpot"] if hs else []) + ([f"{len(an)} Anevo"] if an else [])
        label = "Earlier sends (no date on record)" if week == "Undated" else f"Week of {week}"
        notion._call("PATCH", f"/blocks/{REPORT_PAGE}/children", {"children": [
            {"object": "block", "type": "divider", "divider": {}},
            {"object": "block", "type": "heading_1", "heading_1": {"rich_text": [
                rt(label), rt("      " + " · ".join(bits), color="gray")]}},
        ]})
        # HubSpot A/B galleries for this week
        for test, variants in sorted(hs.items()):
            notion._call("PATCH", f"/blocks/{REPORT_PAGE}/children", {"children": [
                {"object": "block", "type": "heading_3", "heading_3": {"rich_text": [
                    rt(test), rt("      ✉️ HubSpot", color="gray")]}},
                _email_callout(variants),
            ]})
            notion._call("PATCH", f"/blocks/{REPORT_PAGE}/children", {"children": _variants_block(variants)})
        # Anevo campaigns for this week (compact table)
        if an:
            notion._call("PATCH", f"/blocks/{REPORT_PAGE}/children", {"children": [
                {"object": "block", "type": "heading_3", "heading_3": {"rich_text": [
                    rt("📬 Anevo — Cold Email"),
                    rt(f"      {len(an)} campaign{'s' if len(an) != 1 else ''} · replies include auto-replies and out-of-office", color="gray")]}},
            ]})
            notion._call("PATCH", f"/blocks/{REPORT_PAGE}/children", {"children": [_anevo_table(an)]})
        print(f"  Week of {week}: {len(hs)} HubSpot, {len(an)} Anevo")
    print("dashboard rebuilt:", REPORT_PAGE)


if __name__ == "__main__":
    build()
