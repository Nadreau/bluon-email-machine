"""Shared Notion helpers for the Bluon Email Machine.

Reads the Email Content Intelligence guide and the Email Calendar database,
and creates draft rows. Auth via the NOTION_TOKEN env var (a Notion internal
integration token with access to the Email Machine pages). Read + create only —
this never sends an email and never deletes anything.
"""
import os, json, urllib.request, urllib.error

NV = "2022-06-28"
API = "https://api.notion.com/v1"
TOKEN = os.environ.get("NOTION_TOKEN", "").strip()

# Hard-wired IDs for the Bluon "Email Machine" workspace (Intelligence > Email Machine)
GUIDE_PAGE_ID = "379576a5-c12d-8147-af4d-e131c8a1529e"   # Email Content Intelligence
CALENDAR_DB_ID = "379576a5-c12d-816e-a09a-c7bbd50a4c26"  # Email Calendar


def _call(method, path, body=None):
    if not TOKEN:
        raise SystemExit("NOTION_TOKEN env var is not set.")
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        API + path, data=data, method=method,
        headers={"Authorization": f"Bearer {TOKEN}", "Notion-Version": NV,
                 "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        raise SystemExit(f"Notion {method} {path} failed: {e.code} {e.read().decode()[:400]}")


def _blocks_to_text(block_id, depth=0, out=None):
    if out is None:
        out = []
    res = _call("GET", f"/blocks/{block_id}/children?page_size=100")
    for b in res.get("results", []):
        t = b.get("type")
        obj = b.get(t, {})
        rt = obj.get("rich_text", []) if isinstance(obj, dict) else []
        txt = "".join(x.get("plain_text", "") for x in rt) if isinstance(rt, list) else ""
        if t == "table":
            # render table rows
            rows = _call("GET", f"/blocks/{b['id']}/children?page_size=100").get("results", [])
            for row in rows:
                cells = row.get("table_row", {}).get("cells", [])
                line = " | ".join("".join(c.get("plain_text", "") for c in cell) for cell in cells)
                out.append(("  " * depth) + line)
            continue
        prefix = {"heading_1": "# ", "heading_2": "## ", "heading_3": "### ",
                  "bulleted_list_item": "- ", "numbered_list_item": "1. ",
                  "code": "```\n", "quote": "> ", "callout": "> "}.get(t, "")
        suffix = "\n```" if t == "code" else ""
        if txt:
            out.append(("  " * depth) + prefix + txt + suffix)
        if b.get("has_children") and t not in ("table",):
            _blocks_to_text(b["id"], depth + 1, out)
    return out


def get_guide_text():
    """Return the full Email Content Intelligence guide as plain text."""
    return "\n".join(_blocks_to_text(GUIDE_PAGE_ID))


def _prop_text(props, name):
    p = props.get(name, {})
    t = p.get("type")
    if t == "title":
        return "".join(x.get("plain_text", "") for x in p["title"])
    if t == "rich_text":
        return "".join(x.get("plain_text", "") for x in p["rich_text"])
    if t == "select":
        return (p["select"] or {}).get("name", "")
    if t == "date":
        return (p["date"] or {}).get("start", "")
    return ""


def get_calendar_rows():
    """Return existing rows (to avoid duplicates / find planned slots)."""
    rows = []
    payload = {"page_size": 100}
    res = _call("POST", f"/databases/{CALENDAR_DB_ID}/query", payload)
    for r in res.get("results", []):
        pr = r.get("properties", {})
        rows.append({
            "id": r["id"],
            "email": _prop_text(pr, "Email"),
            "status": _prop_text(pr, "Status"),
            "audience": _prop_text(pr, "Audience"),
            "engagement": _prop_text(pr, "Engagement"),
            "send_date": _prop_text(pr, "Send Date"),
            "subject": _prop_text(pr, "Subject Line"),
        })
    return rows


def create_draft(*, email, subject, preview, body, cta, audience, engagement,
                 channel, goal, feature, subject_formula, send_date=None,
                 status="Ready for Review", notes=""):
    """Create a new draft row in the Email Calendar. Returns the new page URL."""
    def sel(v):
        return {"select": {"name": v}} if v else {"select": None}
    def rt(v):
        return {"rich_text": [{"type": "text", "text": {"content": (v or "")[:1990]}}]}
    props = {
        "Email": {"title": [{"type": "text", "text": {"content": email[:200]}}]},
        "Status": sel(status),
        "Audience": sel(audience),
        "Engagement": sel(engagement),
        "Channel": sel(channel),
        "Goal": sel(goal),
        "Feature": sel(feature),
        "Subject Formula": sel(subject_formula),
        "Subject Line": rt(subject),
        "Preview Text": rt(preview),
        "CTA": rt(cta),
        "Body": rt(body),
        "Notes": rt(notes),
    }
    if send_date:
        props["Send Date"] = {"date": {"start": send_date}}
    page = _call("POST", "/pages", {
        "parent": {"database_id": CALENDAR_DB_ID},
        "properties": props,
        # also drop the full body into the page content for easy reading/editing
        "children": [
            {"object": "block", "type": "heading_3",
             "heading_3": {"rich_text": [{"type": "text", "text": {"content": "Draft body (for Pete review)"}}]}},
            {"object": "block", "type": "paragraph",
             "paragraph": {"rich_text": [{"type": "text", "text": {"content": (body or "")[:1990]}}]}},
        ],
    })
    return page.get("url", page.get("id"))
