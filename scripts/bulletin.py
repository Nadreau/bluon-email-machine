"""Marketing Bulletin — the sales-alignment page (Taylor's ask, Aug 7 standup).

Sales kept getting caught off guard by marketing sends ("we had three different
deals going at the same time"). This page is the fix: what's going out, to whom,
with what CTA — headline-level, no build detail.

This script rebuilds ONLY the "📤 Going out (this week)" section from the
calendar: every non-template, non-idea row with a Send Date from 3 days back to
10 days out (past sends get a ✅). Everything below that section — offers, rules
of the road, heads-ups — is human-maintained and never touched.

Runs weekday mornings via GitHub Actions (bulletin.yml).
"""
import datetime, re
import notion

BULLETIN_PAGE_ID = "3b5576a5-c12d-8103-b6f2-fb7bb9dc2173"
AUTO_HEADING = "Going out"
ICON = {"Email": "✉️", "Text": "💬", "Push": "🔔"}


def _t(c, **ann):
    o = {"type": "text", "text": {"content": c}}
    if ann:
        o["annotations"] = ann
    return o


def week_rows():
    today = datetime.date.today()
    lo, hi = today - datetime.timedelta(days=7), today + datetime.timedelta(days=10)
    out = []
    res = notion._call("POST", f"/databases/{notion.CALENDAR_DB_ID}/query", {"page_size": 100})
    for r in res.get("results", []):
        pr = r["properties"]
        if notion.is_template(pr):
            continue
        status = ((pr.get("Status", {}) or {}).get("select") or {}).get("name") or ""
        if status in ("Idea", "Unused", "🗄 Shelved"):
            continue
        ty = ((pr.get("Type", {}) or {}).get("select") or {}).get("name") or ""
        if ty in ("📋 Week Plan", "🔮 Vision", "Template"):
            continue
        sd = ((pr.get("Send Date", {}) or {}).get("date") or {}).get("start")
        if not sd:
            continue
        d = datetime.date.fromisoformat(sd[:10])
        if not (lo <= d <= hi):
            continue
        name = "".join(x.get("plain_text", "") for x in (pr.get("Email", {}).get("title") or []))
        subject = "".join(x.get("plain_text", "") for x in (pr.get("Subject", {}).get("rich_text") or []))
        aud = ", ".join(o["name"] for o in (pr.get("Audience", {}) or {}).get("multi_select") or [])
        eng = ""
        lp = (pr.get("Landing Page", {}) or {}).get("url") or ""
        camp = ((pr.get("Campaign", {}) or {}).get("select") or {}).get("name") or ""
        out.append({"date": d, "fmt": notion.format_of(pr), "name": name, "subject": subject,
                    "audience": aud, "engagement": eng, "lp": lp, "campaign": camp,
                    "page_id": r["id"],
                    "sent": d < datetime.date.today() or status == "Sent"})
    return sorted(out, key=lambda r: r["date"])


def line_blocks(rows):
    """One tight table: Day | What | To | The message. Salesperson-glanceable —
    the message cell links to the calendar row for anyone who wants detail."""
    if not rows:
        return [{"object": "block", "type": "paragraph", "paragraph": {"rich_text": [
            _t("Nothing scheduled this week.", italic=True, color="gray")]}}]
    def cell(*rich): return list(rich)
    header = {"object": "block", "type": "table_row", "table_row": {"cells": [
        cell(_t("Day", bold=True)), cell(_t("What", bold=True)),
        cell(_t("To", bold=True)), cell(_t("The message", bold=True))]}}
    trs = [header]
    for r in rows:
        day = ("✅ " if r["sent"] else "") + r["date"].strftime("%a %-m/%-d")
        to = r["audience"] + (f" · {r['engagement']}" if r["engagement"] else "")
        # subject = what recipients actually see. Falling back to the internal row
        # name, scrub machine-speak — "[Tanner, direct]", "(FIRST PASS)", ✦ marks —
        # sales reads this table, not the build annotations.
        hook = r["subject"].strip()
        if not hook:
            hook = re.sub(r"\s*[\[(][^\])]*[\])]", "", r["name"])
            hook = re.sub(r"^[✦🏕📐💡\s]+", "", hook).strip(" —-")
        url = f"https://www.notion.so/{r['page_id'].replace('-','')}"
        trs.append({"object": "block", "type": "table_row", "table_row": {"cells": [
            cell(_t(day)),
            cell(_t(f"{ICON.get(r['fmt'], '')} {r['fmt']}")),
            cell(_t(to)),
            cell({"type": "text", "text": {"content": hook[:80], "link": {"url": url}}})]}})
    stamp = datetime.datetime.now().strftime("%b %-d")
    return [
        {"object": "block", "type": "table", "table": {
            "table_width": 4, "has_column_header": True, "has_row_header": False,
            "children": trs}},
        {"object": "block", "type": "paragraph", "paragraph": {"rich_text": [
            _t(f"refreshed {stamp}", italic=True, color="gray")]}},
    ]


def meta_new_ads(days=7):
    """Ads that went LIVE in the last N days, grouped by campaign — '3 new Live
    Tech Support ads' style lines for the sales board. Reads the Meta token from
    META_ACCESS_TOKEN or ~/.config/meta/access_token; returns [] when neither is
    available or the API errors (the section just shows nothing rather than
    failing the whole refresh)."""
    import os, json, time, urllib.request, urllib.parse
    tok = os.environ.get("META_ACCESS_TOKEN", "").strip()
    if not tok:
        try:
            tok = open(os.path.expanduser("~/.config/meta/access_token")).read().strip()
        except Exception:
            return []
    try:
        params = urllib.parse.urlencode({
            "fields": "name,created_time,effective_status,campaign{name}",
            "limit": 200, "access_token": tok})
        url = f"https://graph.facebook.com/v19.0/act_273818233239864/ads?{params}"
        with urllib.request.urlopen(url, timeout=60) as r:
            ads = json.load(r).get("data", [])
    except Exception as e:
        print("meta ads fetch skipped:", str(e)[:120])
        return []
    cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days)
    groups = {}
    for a in ads:
        if a.get("effective_status") not in ("ACTIVE", "PENDING_REVIEW", "IN_PROCESS"):
            continue
        # Meta returns '-0700' style offsets, which fromisoformat rejects pre-3.11
        ct = datetime.datetime.strptime(a["created_time"], "%Y-%m-%dT%H:%M:%S%z")
        if ct < cutoff:
            continue
        camp = (a.get("campaign") or {}).get("name", "?")
        groups.setdefault(camp, []).append(a["name"])
    lines = []
    for camp, names in sorted(groups.items(), key=lambda kv: -len(kv[1])):
        sample = ", ".join(n[:40] for n in names[:3]) + (", …" if len(names) > 3 else "")
        lines.append((f"{len(names)} new ad{'s' if len(names) != 1 else ''} live — {camp}", sample))
    return lines


def ad_blocks(lines):
    if not lines:
        return [{"object": "block", "type": "paragraph", "paragraph": {"rich_text": [
            _t("No new ads launched in the last 7 days.", italic=True, color="gray")]}}]
    out = []
    for head, sample in lines:
        out.append({"object": "block", "type": "bulleted_list_item", "bulleted_list_item": {
            "rich_text": [_t(head, bold=True), _t(f"  ({sample})", color="gray")]}})
    return out


def replace_section(blocks, heading_contains, new_children):
    """Swap everything between the matching heading_2 and the next heading/divider."""
    anchor, old, in_sec = None, [], False
    for b in blocks:
        t = b["type"]
        txt = "".join(x.get("plain_text", "") for x in (b.get(t, {}).get("rich_text") or []))
        if t == "heading_2" and heading_contains in txt:
            anchor, in_sec = b["id"], True
            continue
        if in_sec and t in ("heading_1", "heading_2", "divider"):
            in_sec = False
        if in_sec:
            old.append(b["id"])
    if not anchor:
        raise RuntimeError(f"bulletin page is missing the '{heading_contains}' heading — restore it")
    for bid in old:
        try:
            notion._call("PATCH", f"/blocks/{bid}", {"archived": True})
        except Exception:
            pass
    notion._call("PATCH", f"/blocks/{BULLETIN_PAGE_ID}/children",
                 {"children": new_children, "after": anchor})


def rebuild():
    blocks = notion._call("GET", f"/blocks/{BULLETIN_PAGE_ID}/children?page_size=100")["results"]
    replace_section(blocks, AUTO_HEADING, line_blocks(week_rows()))
    blocks = notion._call("GET", f"/blocks/{BULLETIN_PAGE_ID}/children?page_size=100")["results"]
    replace_section(blocks, "New ads", ad_blocks(meta_new_ads()))
    print("bulletin refreshed")


if __name__ == "__main__":
    rebuild()
