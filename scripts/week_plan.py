"""Week Plan — the calendar's week-breaker + vision row.

Every week gets one "📋 Week Plan" row in the Email + Text Calendar. It is dated to
the MONDAY of its week, so when the calendar is grouped by week + sorted by Send Date
ascending it sits at the TOP of that week's group, above the sends (which carry their
real later dates — Wed etc. — so they always order after). Opening the row shows a
laid-out plan: this week's focus, the concrete sends, on-deck (1–3 weeks out), standing
vision, and a "from recent calls & chats" section the every-few-days refresh appends to.

It is NOT an email/text send: no heading_2 (so the parser never reads a subject off it),
Type in ("📋 Week Plan","🔮 Vision") so to_hubspot skips it, no Channel.

  python scripts/week_plan.py --show 2026-07-06     # print the current plan row (if any)
The content is passed in code (upsert(monday_iso, content)); the conversational/auto
refresh calls upsert with a fresh dict, replacing the body (hand edits live in Notion —
see the module note in email_machine memory before overwriting a section wholesale).
"""
import sys, datetime
import notion

PLAN_TYPE = "📋 Week Plan"
VISION_TYPE = "🔮 Vision"
RED = "red_background"
PURPLE = "purple_background"
BLUE = "blue_background"
GRAY = "gray_background"


def monday_of(iso):
    d = datetime.date.fromisoformat(iso[:10])
    return (d - datetime.timedelta(days=d.weekday())).isoformat()


def week_label(monday_iso):
    d = datetime.date.fromisoformat(monday_iso)
    return d.strftime("%b %-d")


def _rt(s, **a):
    o = {"type": "text", "text": {"content": s}}
    if a:
        o["annotations"] = a
    return o


def _h1(s):
    return {"object": "block", "type": "heading_1", "heading_1": {"rich_text": [_rt(s)]}}


def _h3(s):
    return {"object": "block", "type": "heading_3", "heading_3": {"rich_text": [_rt(s)]}}


def _callout(rich, color, emoji):
    return {"object": "block", "type": "callout",
            "callout": {"rich_text": rich, "color": color, "icon": {"type": "emoji", "emoji": emoji}}}


def _todo(rich, checked=False):
    return {"object": "block", "type": "to_do", "to_do": {"rich_text": rich, "checked": checked}}


def _bullet(rich):
    return {"object": "block", "type": "bulleted_list_item", "bulleted_list_item": {"rich_text": rich}}


def _divider():
    return {"object": "block", "type": "divider", "divider": {}}


def build_body(monday_iso, content):
    """content keys: focus (str), sending (list of {emoji,title,detail,done}),
    on_deck / vision / from_calls / decisions (lists of str)."""
    lbl = week_label(monday_iso)
    b = [_h1(f"🗓️  Week of {lbl}  —  Plan & Vision")]
    if content.get("focus"):
        b.append(_callout([_rt("Focus:  ", bold=True), _rt(content["focus"])], RED, "🎯"))
    b.append(_divider())

    b.append(_h3("📤  Sending this week"))
    if content.get("sending"):
        for s in content["sending"]:
            head = f"{s.get('emoji','•')}  "
            rich = [_rt(head), _rt(s["title"], bold=True)]
            if s.get("detail"):
                rich.append(_rt("  —  " + s["detail"]))
            b.append(_todo(rich, checked=bool(s.get("done"))))
    else:
        b.append(_bullet([_rt("Nothing scheduled yet.", italic=True)]))

    b.append(_h3("🧊  On deck — next 1–3 weeks"))
    b.append(_callout([_rt("The ideas we want to graduate into a send soon.")], PURPLE, "🧊"))
    for x in content.get("on_deck", []):
        b.append(_bullet([_rt(x)]))

    b.append(_h3("🔮  Vision / standing themes"))
    b.append(_callout([_rt("Longer-horizon directions to keep steering toward.")], PURPLE, "🔮"))
    for x in content.get("vision", []):
        b.append(_bullet([_rt(x)]))

    b.append(_h3("📥  From recent calls & chats"))
    b.append(_callout([_rt("Pulled from Niko's calls + our discussions. Promote anything here into "
                           "On deck / Sending, or delete it.")], GRAY, "📥"))
    for x in content.get("from_calls", []):
        b.append(_bullet([_rt(x)]))

    b.append(_h3("📝  Decisions & notes"))
    for x in content.get("decisions", []):
        b.append(_bullet([_rt(x)]))

    b.append(_divider())
    b.append(_callout([_rt("Refreshed every few days from calls + this chat. Edit any section "
                           "directly — a refresh only appends to “From recent calls & chats.”")],
                      GRAY, "🔁"))
    return b


def _find_plan(monday_iso):
    """Existing plan row for this week = Type '📋 Week Plan' + Send Date == the Monday."""
    for r in notion._call("POST", f"/databases/{notion.CALENDAR_DB_ID}/query", {"page_size": 100})["results"]:
        pr = r["properties"]
        if ((pr.get("Type", {}) or {}).get("select") or {}).get("name") == PLAN_TYPE \
           and ((pr.get("Send Date", {}) or {}).get("date") or {}).get("start", "")[:10] == monday_iso:
            return r["id"]
    return None


def upsert(monday_iso, content):
    monday_iso = monday_of(monday_iso)
    lbl = week_label(monday_iso)
    props = {
        "Email": {"title": [{"type": "text", "text": {"content": f"🗓️ Week of {lbl} — Plan"}}]},
        "Type": {"select": {"name": PLAN_TYPE}},
        "Status": {"select": {"name": "This Week"}},
        "Send Date": {"date": {"start": monday_iso}},
        "Ready to Go": {"checkbox": False},
    }
    body = build_body(monday_iso, content)
    pid = _find_plan(monday_iso)
    if pid:
        for blk in notion._call("GET", f"/blocks/{pid}/children?page_size=100")["results"]:
            if not blk.get("archived"):
                notion._call("PATCH", f"/blocks/{blk['id']}", {"archived": True})
        notion._call("PATCH", f"/pages/{pid}", {"properties": props})
        notion._call("PATCH", f"/blocks/{pid}/children", {"children": body})
        print(f"updated Week Plan: {lbl} ({pid})")
    else:
        page = notion._call("POST", "/pages", {"parent": {"database_id": notion.CALENDAR_DB_ID},
                                               "properties": props, "children": body})
        print(f"created Week Plan: {lbl} ({page['id']})")
    return pid


if __name__ == "__main__":
    if len(sys.argv) > 2 and sys.argv[1] == "--show":
        pid = _find_plan(monday_of(sys.argv[2]))
        print(pid or "no plan row for that week")
