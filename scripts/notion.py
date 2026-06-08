"""Shared Notion helpers for the Bluon Email Machine.

Reads the Email Content Intelligence guide and the Email Calendar database,
and creates draft rows whose page body is laid out to LOOK like a real Bluon
email (branded header, blue headline, hero placeholder, benefit bullets, a
button-style CTA, footer). Auth via the NOTION_TOKEN env var. Read + create
only — never sends an email, never deletes.
"""
import os, json, urllib.request, urllib.error

NV = "2022-06-28"
API = "https://api.notion.com/v1"
TOKEN = os.environ.get("NOTION_TOKEN", "").strip()

GUIDE_PAGE_ID = "379576a5-c12d-8147-af4d-e131c8a1529e"   # Email Content Intelligence
CALENDAR_DB_ID = "379576a5-c12d-816e-a09a-c7bbd50a4c26"  # Email Calendar

# Bluon brand (from HubSpot email styleSettings)
BLUE = "blue"            # closest Notion color to Bluon #23496d / #3574E3
FOOTER = ("Bluon, Inc., 9160 Irvine Center Drive, Suite 100, Irvine, CA  ·  "
          "Unsubscribe | Manage preferences")


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


# ---------- guide + calendar reads ----------
def _blocks_to_text(block_id, depth=0, out=None):
    if out is None:
        out = []
    res = _call("GET", f"/blocks/{block_id}/children?page_size=100")
    for b in res.get("results", []):
        t = b.get("type"); obj = b.get(t, {})
        rt = obj.get("rich_text", []) if isinstance(obj, dict) else []
        txt = "".join(x.get("plain_text", "") for x in rt) if isinstance(rt, list) else ""
        if t == "table":
            rows = _call("GET", f"/blocks/{b['id']}/children?page_size=100").get("results", [])
            for row in rows:
                cells = row.get("table_row", {}).get("cells", [])
                out.append(("  " * depth) + " | ".join(
                    "".join(c.get("plain_text", "") for c in cell) for cell in cells))
            continue
        prefix = {"heading_1": "# ", "heading_2": "## ", "heading_3": "### ",
                  "bulleted_list_item": "- ", "numbered_list_item": "1. ",
                  "code": "```\n", "quote": "> ", "callout": "> "}.get(t, "")
        suffix = "\n```" if t == "code" else ""
        if txt:
            out.append(("  " * depth) + prefix + txt + suffix)
        if b.get("has_children") and t != "table":
            _blocks_to_text(b["id"], depth + 1, out)
    return out


def get_guide_text():
    return "\n".join(_blocks_to_text(GUIDE_PAGE_ID))


def _prop_text(props, name):
    p = props.get(name, {}); t = p.get("type")
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
    rows = []
    res = _call("POST", f"/databases/{CALENDAR_DB_ID}/query", {"page_size": 100})
    for r in res.get("results", []):
        pr = r.get("properties", {})
        rows.append({"id": r["id"], "email": _prop_text(pr, "Email"),
                     "status": _prop_text(pr, "Status"), "audience": _prop_text(pr, "Audience"),
                     "engagement": _prop_text(pr, "Engagement"), "send_date": _prop_text(pr, "Send Date"),
                     "subject": _prop_text(pr, "Subject Line")})
    return rows


def archive_row(page_id):
    """Soft-delete (move to Notion trash) — recoverable, not a hard delete."""
    _call("PATCH", f"/pages/{page_id}", {"archived": True})


# ---------- email-styled page body ----------
def _t(content, *, bold=False, italic=False, color=None, link=None):
    txt = {"content": content}
    if link:
        txt["link"] = {"url": link}
    o = {"type": "text", "text": txt}
    ann = {}
    if bold: ann["bold"] = True
    if italic: ann["italic"] = True
    if color: ann["color"] = color
    if ann: o["annotations"] = ann
    return o


def _email_blocks(*, subject, preview, body, cta, cta_url=None):
    """Render an email-looking layout with native Notion blocks."""
    blocks = []
    # branded header bar
    blocks.append({"object": "block", "type": "callout", "callout": {
        "rich_text": [_t("bluon", bold=True, color=BLUE), _t("   FOR BUSINESS", color=BLUE)],
        "icon": {"type": "emoji", "emoji": "📧"}, "color": "blue_background"}})
    # blue headline (the subject as the email H1)
    blocks.append({"object": "block", "type": "heading_2",
                   "heading_2": {"rich_text": [_t(subject, bold=True, color=BLUE)]}})
    # preview text (muted)
    if preview:
        blocks.append({"object": "block", "type": "paragraph",
                       "paragraph": {"rich_text": [_t("Preview: " + preview, italic=True, color="gray")]}})
    # hero placeholder (real emails lead with a hero image / video thumbnail)
    blocks.append({"object": "block", "type": "callout", "callout": {
        "rich_text": [_t("Hero image / video thumbnail goes here", color="gray")],
        "icon": {"type": "emoji", "emoji": "🖼"}, "color": "gray_background"}})
    # body — paragraphs + bullets parsed from the generated body
    for raw in (body or "").split("\n"):
        line = raw.strip()
        if not line:
            continue
        if line[:1] in ("-", "•", "*"):
            blocks.append({"object": "block", "type": "bulleted_list_item",
                           "bulleted_list_item": {"rich_text": [_t(line.lstrip("-•* ").strip(), color=BLUE)]}})
        else:
            # skip a trailing CTA echoed in the body (we render the button below)
            if cta and line.lower().rstrip(" →").endswith(cta.lower().rstrip(" →")):
                continue
            blocks.append({"object": "block", "type": "paragraph",
                           "paragraph": {"rich_text": [_t(line)]}})
    # CTA "button"
    blocks.append({"object": "block", "type": "callout", "callout": {
        "rich_text": [_t("📅  " + (cta or "Book a Demo") + "  →", bold=True, color="blue",
                         link=cta_url)],
        "icon": {"type": "emoji", "emoji": "👉"}, "color": "blue_background"}})
    # footer
    blocks.append({"object": "block", "type": "divider", "divider": {}})
    blocks.append({"object": "block", "type": "paragraph",
                   "paragraph": {"rich_text": [_t(FOOTER, color="gray")]}})
    return blocks


def create_draft(*, subject, preview, body, cta, audience, engagement, channel, goal,
                 feature, subject_formula, send_date=None, status="Ready for Review",
                 notes="", cta_url=None, email=None):
    """Create a draft row whose title leads with Audience · Engagement and whose
    page body is laid out to look like the real email."""
    title = f"{audience} · {engagement} — {subject}"

    def sel(v): return {"select": {"name": v}} if v else {"select": None}
    def rt(v): return {"rich_text": [{"type": "text", "text": {"content": (v or "")[:1990]}}]}

    props = {
        "Email": {"title": [{"type": "text", "text": {"content": title[:200]}}]},
        "Status": sel(status), "Audience": sel(audience), "Engagement": sel(engagement),
        "Channel": sel(channel), "Goal": sel(goal), "Feature": sel(feature),
        "Subject Formula": sel(subject_formula),
        "Subject Line": rt(subject), "Preview Text": rt(preview), "CTA": rt(cta),
        "Body": rt(body), "Notes": rt(notes),
    }
    if send_date:
        props["Send Date"] = {"date": {"start": send_date}}
    page = _call("POST", "/pages", {
        "parent": {"database_id": CALENDAR_DB_ID}, "properties": props,
        "children": _email_blocks(subject=subject, preview=preview, body=body,
                                  cta=cta, cta_url=cta_url),
    })
    return page.get("url", page.get("id"))
