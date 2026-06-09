"""Shared Notion helpers for the Bluon Email Machine.

The draft page IS the editable email: stylized blocks (branded header, blue
headline, hero, body, CTA, footer) that Pete edits directly, a "Formatting &
media notes" section (free-form, use (( )) for styling hints + paste graphics),
and the rendered image mockup that Regenerate refreshes from that current state.
Lean DB holds only metadata + Ready to Go / Regen requested. Read+create only.
"""
import os, json, re, datetime, urllib.request, urllib.error

NV = "2022-06-28"
API = "https://api.notion.com/v1"
TOKEN = os.environ.get("NOTION_TOKEN", "").strip()

GUIDE_PAGE_ID = "379576a5-c12d-8147-af4d-e131c8a1529e"
CALENDAR_DB_ID = "379576a5-c12d-816e-a09a-c7bbd50a4c26"
BLUE = "blue"
FOOTER = ("Bluon, Inc., 9160 Irvine Center Drive, Suite 100, Irvine, CA  ·  "
          "Unsubscribe | Manage preferences")
PAREN = re.compile(r"\(\(.*?\)\)")           # (( styling note )) — stripped from copy
NOTES_HEADING = "Formatting & media notes"
MOCKUP_HEADING = "Mockup"
HERO_HINT = "Hero —"


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
    for b in _call("GET", f"/blocks/{block_id}/children?page_size=100").get("results", []):
        t = b.get("type"); obj = b.get(t, {})
        rt = obj.get("rich_text", []) if isinstance(obj, dict) else []
        txt = "".join(x.get("plain_text", "") for x in rt) if isinstance(rt, list) else ""
        if t == "table":
            for row in _call("GET", f"/blocks/{b['id']}/children?page_size=100").get("results", []):
                cells = row.get("table_row", {}).get("cells", [])
                out.append(("  " * depth) + " | ".join(
                    "".join(c.get("plain_text", "") for c in cell) for cell in cells))
            continue
        pre = {"heading_1": "# ", "heading_2": "## ", "heading_3": "### ",
               "bulleted_list_item": "- ", "numbered_list_item": "1. ",
               "code": "```\n", "quote": "> ", "callout": "> "}.get(t, "")
        suf = "\n```" if t == "code" else ""
        if txt:
            out.append(("  " * depth) + pre + txt + suf)
        if b.get("has_children") and t != "table":
            _blocks_to_text(b["id"], depth + 1, out)
    return out


def get_guide_text():
    return "\n".join(_blocks_to_text(GUIDE_PAGE_ID))


def _p(props, name):
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
    for r in _call("POST", f"/databases/{CALENDAR_DB_ID}/query", {"page_size": 100}).get("results", []):
        pr = r.get("properties", {})
        rows.append({"id": r["id"], "name": _p(pr, "Email"), "audience": _p(pr, "Audience"),
                     "engagement": _p(pr, "Engagement"), "channel": _p(pr, "Channel"),
                     "send_date": _p(pr, "Send Date"), "ready": _p(pr, "Ready to Go"),
                     "regen": _p(pr, "Regen requested")})
    return rows


def archive_row(page_id):
    _call("PATCH", f"/pages/{page_id}", {"archived": True})


def set_checkbox(page_id, name, value):
    _call("PATCH", f"/pages/{page_id}", {"properties": {name: {"checkbox": value}}})


# ---------- stylized email blocks (the editable email) ----------
def _t(content, *, bold=False, italic=False, color=None):
    o = {"type": "text", "text": {"content": content}}
    ann = {}
    if bold: ann["bold"] = True
    if italic: ann["italic"] = True
    if color: ann["color"] = color
    if ann: o["annotations"] = ann
    return o


def _para(text, **kw):
    return {"object": "block", "type": "paragraph", "paragraph": {"rich_text": [_t(text, **kw)]}}


def styled_email_blocks(*, subject, preview, body_lines, cta, image_fid=None):
    b = []
    b.append({"object": "block", "type": "callout", "callout": {
        "rich_text": [_t("bluon", bold=True, color=BLUE), _t("   FOR BUSINESS", color=BLUE)],
        "icon": {"type": "emoji", "emoji": "📧"}, "color": "blue_background"}})
    b.append({"object": "block", "type": "heading_2", "heading_2": {"rich_text": [_t(subject, bold=True, color=BLUE)]}})
    b.append(_para("Preview: " + (preview or ""), italic=True, color="gray"))
    b.append({"object": "block", "type": "callout", "callout": {
        "rich_text": [_t(HERO_HINT + " paste your video thumbnail / graphic right below this line", color="gray")],
        "icon": {"type": "emoji", "emoji": "🖼"}, "color": "gray_background"}})
    for ln in body_lines:
        ln = ln.strip()
        if not ln:
            continue
        if ln[:1] in ("-", "•", "*"):
            b.append({"object": "block", "type": "bulleted_list_item",
                      "bulleted_list_item": {"rich_text": [_t(ln.lstrip("-•* ").strip(), color=BLUE)]}})
        else:
            b.append(_para(ln))
    b.append({"object": "block", "type": "callout", "callout": {
        "rich_text": [_t("📅  " + (cta or "Book a Demo") + "  →", bold=True, color=BLUE)],
        "icon": {"type": "emoji", "emoji": "👉"}, "color": "blue_background"}})
    b.append(_para(FOOTER, color="gray"))
    # formatting & media notes
    b.append({"object": "block", "type": "divider", "divider": {}})
    b.append({"object": "block", "type": "heading_3",
              "heading_3": {"rich_text": [_t(NOTES_HEADING + "   ·   use (( )) for styling")]}})
    b.append({"object": "block", "type": "callout", "callout": {
        "rich_text": [_t("e.g. ((hero: use the Peterman testimonial)) · ((move CTA above bullets)) — paste extra graphics here too", color="gray")],
        "icon": {"type": "emoji", "emoji": "✍️"}, "color": "gray_background"}})
    # mockup
    b.append({"object": "block", "type": "divider", "divider": {}})
    b.append({"object": "block", "type": "heading_3",
              "heading_3": {"rich_text": [_t("📧  " + MOCKUP_HEADING + " — press 🔄 Regenerate Mockup after edits")]}})
    if image_fid:
        b.append({"object": "block", "type": "image",
                  "image": {"type": "file_upload", "file_upload": {"id": image_fid}}})
    else:
        b.append({"object": "block", "type": "callout", "callout": {
            "rich_text": [_t("mockup rendering — press Regenerate after editing", color="gray")],
            "icon": {"type": "emoji", "emoji": "🖼"}, "color": "gray_background"}})
    return b


def _week_of(send_date):
    try:
        d = datetime.date.fromisoformat(send_date[:10])  # tolerate datetime (YYYY-MM-DDThh:mm)
        return (d - datetime.timedelta(days=d.weekday())).strftime("%b %-d")
    except Exception:
        return None


def create_draft(*, subject, preview, body, cta, audience, engagement, channel,
                 feature, send_date=None, goal=None, subject_formula=None,
                 status=None, notes=None, cta_url=None, email=None):
    wk = _week_of(send_date) if send_date else None
    title = f"{audience} {engagement}" + (f" — Week of {wk}" if wk else "")

    def sel(v): return {"select": {"name": v}} if v else {"select": None}
    props = {
        "Email": {"title": [{"type": "text", "text": {"content": title[:200]}}]},
        "Audience": sel(audience), "Engagement": sel(engagement),
        "Channel": sel(channel), "Feature": sel(feature),
        "Ready to Go": {"checkbox": False}, "Regen requested": {"checkbox": False},
    }
    if send_date:
        props["Send Date"] = {"date": {"start": send_date}}
    page = _call("POST", "/pages", {
        "parent": {"database_id": CALENDAR_DB_ID}, "properties": props,
        "children": styled_email_blocks(subject=subject, preview=preview,
                    body_lines=(body or "").split("\n"), cta=cta)})
    return page.get("url", page.get("id"))


# ---------- parse an edited draft page (for Regenerate) ----------
def _block_text(b):
    t = b.get("type")
    return "".join(x.get("plain_text", "") for x in b.get(t, {}).get("rich_text", []))


def _image_url(b):
    img = b.get("image", {})
    return (img.get("file") or img.get("external") or {}).get("url", "")


def parse_draft_page(page_id):
    """Pull the current editable email back out of the page for re-rendering.
    Returns subject, body_lines (with (( )) stripped), cta, style_notes (the
    (( )) hints + notes text), hero_url (first image pasted in the email area),
    and mockup_image_id (existing render to replace)."""
    blocks = _call("GET", f"/blocks/{page_id}/children?page_size=100")["results"]
    section = "email"        # email -> notes -> mockup
    subject, cta, body, notes, style_notes = "", "Book a Demo", [], [], []
    hero_url, mockup_old_ids = "", []
    for b in blocks:
        t = b["type"]; txt = _block_text(b)
        if t == "heading_3" and NOTES_HEADING in txt:
            section = "notes"; continue
        if t == "heading_3" and MOCKUP_HEADING in txt:
            section = "mockup"; continue
        if t == "heading_2" and not subject:
            subject = txt.strip(); continue
        if t == "callout" and txt.startswith(HERO_HINT):
            continue
        if section == "mockup" and t in ("image", "callout"):
            mockup_old_ids.append(b["id"]); continue   # old render/placeholder to replace
        if t == "image":
            if not hero_url:          # first pasted image in email/notes = hero
                hero_url = _image_url(b)
            continue
        if txt.startswith("e.g."):
            continue          # the example placeholder callout — ignore entirely
        # collect (( )) styling notes from anywhere
        for m in PAREN.findall(txt):
            style_notes.append(m.strip("()").strip())
        clean = PAREN.sub("", txt).strip()
        if section == "email":
            if txt.startswith("Preview:") or txt.startswith("bluon"):
                continue
            if t == "callout" and ("📅" in txt or "→" in txt):
                cta = clean.replace("📅", "").replace("→", "").strip() or cta
                continue
            if t == "bulleted_list_item" and clean:
                body.append("- " + clean)
            elif t == "paragraph" and clean and "Bluon, Inc." not in clean:
                body.append(clean)
        elif section == "notes":
            if clean and not clean.startswith("e.g. (("):
                notes.append(clean)
    return {"subject": subject, "body_lines": body, "cta": cta,
            "style_notes": style_notes + notes, "hero_url": hero_url,
            "mockup_old_ids": mockup_old_ids}


YOUTUBE_RE = re.compile(r"(?:youtube\.com/watch\?v=|youtu\.be/|youtube\.com/embed/)([\w-]{11})")


def detect_hero(info):
    """Decide the hero for a draft from its Notion content. Returns (kind, src, link):
    a YouTube link → ('video', thumbnail, watch_url); a pasted image → ('image', url, '');
    nothing identified → ('default', None, '') = the branded Bluon banner."""
    text = " ".join(info.get("body_lines", []) + info.get("style_notes", []))
    m = YOUTUBE_RE.search(text)
    if m:
        vid = m.group(1)
        return ("video", f"https://img.youtube.com/vi/{vid}/hqdefault.jpg",
                f"https://www.youtube.com/watch?v={vid}")
    if info.get("hero_url"):
        return ("image", info["hero_url"], "")
    return ("default", None, "")


def parse_structure(page_id):
    """Return block IDs by role so an editor can update copy in place."""
    blocks = _call("GET", f"/blocks/{page_id}/children?page_size=100")["results"]
    section = "email"
    out = {"subject_id": None, "hero_anchor_id": None, "cta_id": None,
           "body_ids": [], "note_ids": [], "mockup_old_ids": []}
    for b in blocks:
        t = b["type"]; txt = _block_text(b); bid = b["id"]
        if t == "heading_3" and NOTES_HEADING in txt:
            section = "notes"; continue
        if t == "heading_3" and MOCKUP_HEADING in txt:
            section = "mockup"; continue
        if t == "heading_2" and out["subject_id"] is None:
            out["subject_id"] = bid; continue
        if t == "callout" and txt.startswith(HERO_HINT):
            out["hero_anchor_id"] = bid; continue
        if section == "mockup" and t in ("image", "callout"):
            out["mockup_old_ids"].append(bid); continue
        if section == "email":
            if t == "image":
                out["hero_anchor_id"] = bid          # pasted hero = insert body after it
            elif t == "callout" and ("📅" in txt or "→" in txt):
                out["cta_id"] = bid
            elif t == "paragraph" and txt.startswith("Preview:"):
                pass
            elif t == "paragraph" and "Bluon, Inc." in txt:
                pass
            elif t in ("paragraph", "bulleted_list_item"):
                out["body_ids"].append(bid)
        elif section == "notes":
            if txt and not txt.startswith("e.g."):
                out["note_ids"].append(bid)
    return out


def update_block_text(block_id, block_type, rich_text):
    _call("PATCH", f"/blocks/{block_id}", {block_type: {"rich_text": rich_text}})


def insert_after(page_id, after_id, children):
    _call("PATCH", f"/blocks/{page_id}/children", {"children": children, "after": after_id})
