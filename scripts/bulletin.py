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
    lo, hi = today - datetime.timedelta(days=3), today + datetime.timedelta(days=10)
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
        aud = ((pr.get("Audience", {}) or {}).get("select") or {}).get("name") or ""
        eng = ", ".join(o["name"] for o in (pr.get("Engagement", {}) or {}).get("multi_select") or [])
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


def rebuild():
    blocks = notion._call("GET", f"/blocks/{BULLETIN_PAGE_ID}/children?page_size=100")["results"]
    anchor, old = None, []
    in_auto = False
    for b in blocks:
        t = b["type"]
        txt = "".join(x.get("plain_text", "") for x in (b.get(t, {}).get("rich_text") or []))
        if t == "heading_2" and AUTO_HEADING in txt:
            anchor, in_auto = b["id"], True
            continue
        if in_auto and t in ("heading_1", "heading_2", "divider"):
            in_auto = False
        if in_auto:
            old.append(b["id"])
    if not anchor:
        raise RuntimeError("bulletin page is missing the 'Going out' heading — restore it")
    for bid in old:
        try:
            notion._call("PATCH", f"/blocks/{bid}", {"archived": True})
        except Exception:
            pass
    notion._call("PATCH", f"/blocks/{BULLETIN_PAGE_ID}/children",
                 {"children": line_blocks(week_rows()), "after": anchor})
    print("bulletin refreshed")


if __name__ == "__main__":
    rebuild()
