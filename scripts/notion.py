"""Shared Notion helpers for the Bluon Email Machine.

Lean machine: the database holds only stable metadata (Audience, Engagement,
Channel, Feature, Send Date, Approved, Done). The editable email itself —
suggested subject, the plain body text Pete can rewrite freely, and a rendered
mockup of how it'll look as a Bluon HubSpot email — lives in the page body.
Auth via NOTION_TOKEN. Read + create only; never sends, never hard-deletes.
"""
import os, json, datetime, urllib.request, urllib.error

NV = "2022-06-28"
API = "https://api.notion.com/v1"
TOKEN = os.environ.get("NOTION_TOKEN", "").strip()

GUIDE_PAGE_ID = "379576a5-c12d-8147-af4d-e131c8a1529e"   # Email Content Intelligence
CALENDAR_DB_ID = "379576a5-c12d-816e-a09a-c7bbd50a4c26"  # Email Calendar
BLUE = "blue"


def _call(method, path, body=None):
    if not TOKEN:
        raise SystemExit("NOTION_TOKEN env var is not set.")
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(API + path, data=data, method=method,
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


def _prop(props, name):
    p = props.get(name, {}); t = p.get("type")
    if t == "title":
        return "".join(x.get("plain_text", "") for x in p["title"])
    if t == "select":
        return (p["select"] or {}).get("name", "")
    if t == "date":
        return (p["date"] or {}).get("start", "")
    if t == "checkbox":
        return p["checkbox"]
    return ""


def get_calendar_rows():
    rows = []
    res = _call("POST", f"/databases/{CALENDAR_DB_ID}/query", {"page_size": 100})
    for r in res.get("results", []):
        pr = r.get("properties", {})
        rows.append({"id": r["id"], "name": _prop(pr, "Email"),
                     "audience": _prop(pr, "Audience"), "engagement": _prop(pr, "Engagement"),
                     "channel": _prop(pr, "Channel"), "send_date": _prop(pr, "Send Date"),
                     "approved": _prop(pr, "Approved"), "done": _prop(pr, "Done")})
    return rows


def archive_row(page_id):
    _call("PATCH", f"/pages/{page_id}", {"archived": True})


# ---------- page body (editable email) ----------
def _t(content, *, bold=False, italic=False, color=None):
    o = {"type": "text", "text": {"content": content}}
    ann = {}
    if bold: ann["bold"] = True
    if italic: ann["italic"] = True
    if color: ann["color"] = color
    if ann: o["annotations"] = ann
    return o


def _week_of(send_date):
    try:
        d = datetime.date.fromisoformat(send_date)
        monday = d - datetime.timedelta(days=d.weekday())
        return monday.strftime("%b %-d")
    except Exception:
        return None


def _draft_page_blocks(*, subject, preview, body, cta, feature, image_fid):
    b = []
    # suggested subject (editable starting point)
    b.append({"object": "block", "type": "callout", "callout": {
        "rich_text": [_t("Suggested subject:  ", bold=True), _t(subject)],
        "icon": {"type": "emoji", "emoji": "✏️"}, "color": "gray_background"}})
    if preview:
        b.append({"object": "block", "type": "paragraph",
                  "paragraph": {"rich_text": [_t("Preview: " + preview, italic=True, color="gray")]}})
    b.append({"object": "block", "type": "heading_3",
              "heading_3": {"rich_text": [_t("Body  (edit freely — this is a first draft)")]}})
    # the actual email body, plain editable text + bullets
    for raw in (body or "").split("\n"):
        line = raw.strip()
        if not line:
            continue
        if line[:1] in ("-", "•", "*"):
            b.append({"object": "block", "type": "bulleted_list_item",
                      "bulleted_list_item": {"rich_text": [_t(line.lstrip("-•* ").strip())]}})
        else:
            b.append({"object": "block", "type": "paragraph",
                      "paragraph": {"rich_text": [_t(line)]}})
    b.append({"object": "block", "type": "paragraph", "paragraph": {"rich_text": [
        _t("CTA: ", bold=True, color="gray"), _t(cta or "", color="gray"),
        _t(f"     ·  Suggested feature: {feature}", color="gray")]}})
    # mockup
    b.append({"object": "block", "type": "divider", "divider": {}})
    b.append({"object": "block", "type": "heading_3",
              "heading_3": {"rich_text": [_t("📧  Mockup — roughly how it'll look in HubSpot")]}})
    if image_fid:
        b.append({"object": "block", "type": "image",
                  "image": {"type": "file_upload", "file_upload": {"id": image_fid}}})
    else:
        b.append({"object": "block", "type": "callout", "callout": {
            "rich_text": [_t("(mockup image unavailable for this run)", color="gray")],
            "icon": {"type": "emoji", "emoji": "🖼"}, "color": "gray_background"}})
    return b


def create_draft(*, subject, preview, body, cta, audience, engagement, channel,
                 feature, send_date=None, goal=None, subject_formula=None,
                 status=None, notes=None, cta_url=None, email=None):
    """Create a lean draft row. Title = 'Audience Engagement — Week of <Mon>'."""
    wk = _week_of(send_date) if send_date else None
    title = f"{audience} {engagement}" + (f" — Week of {wk}" if wk else "")

    # render + upload the mockup image (best-effort)
    image_fid = None
    try:
        import mockup
        image_fid = mockup.make_mockup_upload(headline=subject,
            body_lines=(body or "").split("\n"), cta=cta or "Book a Demo")
    except Exception as e:
        print("mockup skipped:", e)

    def sel(v): return {"select": {"name": v}} if v else {"select": None}
    props = {
        "Email": {"title": [{"type": "text", "text": {"content": title[:200]}}]},
        "Audience": sel(audience), "Engagement": sel(engagement),
        "Channel": sel(channel), "Feature": sel(feature),
        "Approved": {"checkbox": False}, "Done": {"checkbox": False},
    }
    if send_date:
        props["Send Date"] = {"date": {"start": send_date}}
    page = _call("POST", "/pages", {
        "parent": {"database_id": CALENDAR_DB_ID}, "properties": props,
        "children": _draft_page_blocks(subject=subject, preview=preview, body=body,
                                       cta=cta, feature=feature, image_fid=image_fid)})
    return page.get("url", page.get("id"))
