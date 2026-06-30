"""Create a HubSpot draft email from a Notion draft row and write the draft
link back into the row's "Hubspot Email" property.

Fired when a reviewer checks "Ready to Go". Clones a Bluon template email (so the
draft inherits the real logo header + footer + brand styling), swaps in this
row's subject + body, and drops the editor link into Notion.

IMPORTANT: HubSpot's rich-text editor re-flows complex HTML (tables/gradients get
mangled). So the body uses only WYSIWYG-safe elements — headline, paragraphs,
check-bullets, and a single styled button. The video hero is left for HubSpot's
native Video module (how Bluon builds them).

  python to_hubspot.py <PAGE_ID>   # one row
  python to_hubspot.py --ready     # all rows that are Ready to Go but not yet drafted
"""
import os, sys, json, re, html, time, tempfile, urllib.request, urllib.error
import notion, mockup

# HubSpot personalization token for the recipient's first name, with a graceful
# "there" fallback when HubSpot has no first name on file. Inserted AFTER html
# escaping (the quotes/braces must stay raw for HubL to fire).
# HubSpot's canonical contact token. The `|default("there")` HubL form gets SILENTLY
# STRIPPED by HubSpot's email editor (renders "Hey ," — no name, no fallback); the bare
# token is what last week's working sends used. Fallback comes from the property's global
# default, set in HubSpot. (Confirmed against a live send Jun 30 2026.)
FNAME_TOKEN = '{{ contact.firstname }}'


def personalize(escaped):
    """Turn a generic greeting / placeholder in the (already-escaped) copy into the
    HubSpot first-name token. 'Hey there,' -> 'Hey {{ contact.firstname }},'.
    Also honors an explicit {firstname} / {first_name} placeholder Pete can type."""
    escaped = re.sub(r"(?i)\b(hey|hi|hello)([,!]?\s+)there\b",
                     lambda m: m.group(1) + m.group(2) + FNAME_TOKEN, escaped)
    escaped = re.sub(r"\{\{?\s*first[ _]?name\s*\}?\}", FNAME_TOKEN, escaped, flags=re.I)
    return escaped

# Bluon's canonical email layout (the real Non-Anevo Nurture structure), cloned
# into a stable base template: logo -> hero image -> rich text -> button -> footer.
TEMPLATE_EMAIL_ID = os.environ.get("HS_TEMPLATE_ID", "214935082610")
LOGO_MODULE   = "module-0-0-0"
HERO_MODULE   = "module_17389528910191"   # inject the hero image here
BODY_MODULE   = "module_17406888513524"   # rich text body
BUTTON_MODULE = "module_17810258159061"   # native CTA button (text + destination)
FOOTER_MODULE = "module-2-0-0"
KEEP = [LOGO_MODULE, HERO_MODULE, BODY_MODULE, BUTTON_MODULE, FOOTER_MODULE]

HS_TOKEN = os.environ.get("HUBSPOT_TOKEN", "").strip() or open(
    os.path.expanduser("~/.config/hubspot/api_key")).read().strip()
PORTAL = "6885872"
DEMO_URL = "https://www.bluon.com/get-demo"   # default CTA destination (the Get Demo page)
HS = "https://api.hubapi.com"

# Smart landing-page defaults — picked by Campaign, then Audience. A url already set
# on the row (manual override) always wins. Each entry is either a url, or a dict of
# {Audience: url, "_default": url} for per-audience routing.
# The Live Tech Support landing page is bluon.com/live-support (live, confirmed by
# Niko Jun 2026). For ServiceTitan, if a separate standalone-LTS page ever exists use
# it here — NEVER the ServiceTitan-integration page (hard rule: live tech support is
# Bluon's standalone product, not part of the ST integration).
LANDING_PAGES = {
    "Live Tech Support": {"_default": "https://www.bluon.com/live-support"},
}
DEFAULT_LP = DEMO_URL


def _url_ok(url):
    """True if the url responds < 400 — so we never ship a dead landing page."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status < 400
    except urllib.error.HTTPError as e:
        return e.code < 400
    except Exception:
        return False


def resolve_landing_page(pr):
    """Existing row url wins (manual override, as-is); else map by Campaign (+Audience);
    else the demo page. The auto-mapped url is validity-checked — a non-2xx result falls
    back to the verified default so an unshipped/typo'd campaign URL can't silently route
    a whole audience to a 404 during an unattended run."""
    existing = (pr.get("Landing Page", {}) or {}).get("url")
    if existing:
        return existing
    camp = (pr.get("Campaign", {}).get("select") or {}).get("name")
    aud = (pr.get("Audience", {}).get("select") or {}).get("name")
    entry = LANDING_PAGES.get(camp)
    if isinstance(entry, dict):
        url = entry.get(aud) or entry.get("_default") or DEFAULT_LP
    elif isinstance(entry, str):
        url = entry
    else:
        url = DEFAULT_LP
    if not _url_ok(url):
        print(f"  landing page {url} unreachable — falling back to {DEFAULT_LP}")
        url = DEFAULT_LP
    return url


def hs(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(HS + path, data=data, method=method,
        headers={"Authorization": f"Bearer {HS_TOKEN}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            raw = r.read()
            return json.loads(raw) if raw else {}   # 204 No Content (e.g. DELETE) → {}
    except urllib.error.HTTPError as e:
        raise SystemExit(f"HubSpot {method} {path} failed: {e.code} {e.read().decode()[:300]}")


def utm_link(base, pr):
    """Append UTM tracking to the CTA destination so reporting can attribute the
    click by campaign + audience/engagement (the link gap Imani flagged)."""
    import urllib.parse
    slug = lambda s: (s or "").lower().replace(" ", "-").replace("/", "-") or None
    camp = slug((pr.get("Campaign", {}).get("select") or {}).get("name")) or "email-machine"
    aud = slug((pr.get("Audience", {}).get("select") or {}).get("name")) or "all"
    eng = slug((pr.get("Engagement", {}).get("select") or {}).get("name")) or "all"
    q = {"utm_source": "bluon-email", "utm_medium": "email",
         "utm_campaign": camp, "utm_content": f"{aud}-{eng}"}
    sep = "&" if "?" in base else "?"
    return base + sep + urllib.parse.urlencode(q)


def body_html(info, flow, uniq=""):
    """WYSIWYG-safe Bluon body: headline + the ordered flow (paragraphs, check-
    bullets, and any image Pete moved INTO the copy — hosted + inlined right where
    he placed it). The CTA is the template's native button module, so it's not here."""
    out = [f'<h2 style="color:#23496d;text-align:center;font-weight:800;font-size:22px;'
           f'margin:0 0 16px">{html.escape(info["subject"])}</h2>']
    n = 0
    for it in flow:
        k = it.get("kind")
        if k == "image":
            n += 1
            hosted = optimize_and_host(it.get("url"), f"body-{uniq}-{n}") or it.get("url")
            if hosted:
                out.append(f'<img src="{hosted}" style="width:100%;max-width:560px;height:auto;'
                           f'border-radius:8px;display:block;margin:16px auto" alt="">')
        elif k == "bullet":
            out.append(f'<p style="color:#23496d;font-weight:600;font-size:15px;margin:8px 0">'
                       f'&#9989;&nbsp;{personalize(html.escape(it.get("text", "")))}</p>')
        else:
            out.append(f'<p style="color:#222222;font-size:15px;line-height:1.5;margin:12px 0">'
                       f'{personalize(html.escape(it.get("text", "")))}</p>')
    return "".join(out)


def host_image(url, name="hero"):
    """Import a (Notion-hosted, expiring) image into HubSpot files → permanent url."""
    try:
        task = hs("POST", "/files/v3/files/import-from-url/async",
                  {"url": url, "folderPath": "/email-machine", "access": "PUBLIC_INDEXABLE",
                   "name": name, "overwrite": False, "duplicateValidationStrategy": "NONE",
                   "duplicateValidationScope": "ENTIRE_PORTAL"})
        tid = task["id"]
        for _ in range(15):
            st = hs("GET", f"/files/v3/files/import-from-url/async/tasks/{tid}/status")
            if st.get("status") == "COMPLETE":
                return (st.get("result") or {}).get("url")
            time.sleep(2)
    except Exception as e:
        print("host_image failed:", e)
    return None


def _upload_local(path, filename, content_type):
    """Upload a local file to HubSpot Files (public) → hosted url."""
    boundary = "----bluonupload88"
    fields = {"folderPath": "/email-machine",
              "options": json.dumps({"access": "PUBLIC_INDEXABLE", "overwrite": True})}
    body = bytearray()
    for k, v in fields.items():
        body += f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode()
    body += (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; '
             f'filename="{filename}"\r\nContent-Type: {content_type}\r\n\r\n').encode()
    body += open(path, "rb").read()
    body += f"\r\n--{boundary}--\r\n".encode()
    req = urllib.request.Request("https://api.hubapi.com/files/v3/files", data=bytes(body),
        method="POST", headers={"Authorization": f"Bearer {HS_TOKEN}",
                                "Content-Type": f"multipart/form-data; boundary={boundary}"})
    try:
        with urllib.request.urlopen(req, timeout=90) as r:
            return json.load(r).get("url")
    except Exception as e:
        print("upload failed:", e)
        return None


def upload_png(png_path, name="bluon-hero"):
    """Upload a local PNG to HubSpot Files → hosted url (for the rendered banner)."""
    return _upload_local(png_path, f"{name}.png", "image/png")


def optimize_and_host(url, name, max_w=1200, quality=85):
    """Download an image, cap it at max_w px wide and JPEG-compress, then host it.
    Email clients break on huge/heavy images — Gmail's proxy and Outlook (which won't
    render images wider than ~1728px) show a BROKEN ICON for a full-res multi-MB PNG.
    So heroes / inline graphics ship as a resized, compressed JPEG (~100KB), never the
    full-size original. Falls back to a straight import if optimization isn't possible."""
    try:
        import io
        from PIL import Image
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        raw = urllib.request.urlopen(req, timeout=60).read()
        im = Image.open(io.BytesIO(raw)).convert("RGB")
        if im.width > max_w:
            im = im.resize((max_w, round(im.height * max_w / im.width)), Image.LANCZOS)
        tmp = tempfile.mktemp(suffix=".jpg")
        im.save(tmp, "JPEG", quality=quality, optimize=True, progressive=True)
        return _upload_local(tmp, f"{name}.jpg", "image/jpeg")
    except Exception as e:
        print("optimize_and_host failed, importing original instead:", e)
        return host_image(url, name)


def host_top_hero(top_hero, info, uniq):
    """Host the TOP hero slot's image and return (img_src, link). top_hero is
    ("video"|"image"|"default", src, link): video → hosted thumbnail + watch link;
    image → hosted image; default → the rendered branded Bluon banner."""
    kind, src, link = top_hero
    if kind == "video":
        return (host_image(src, f"hero-{uniq}") or src, link)   # small YT thumbnail, keep as-is
    if kind == "image":
        return (optimize_and_host(src, f"hero-{uniq}") or src, link)   # resize+compress for email
    try:
        png = mockup.render_png(mockup.hero_banner_html(info["subject"]),
                                tempfile.mktemp(suffix=".png"))
        return (upload_png(png, f"bluon-hero-{uniq}"), "")
    except Exception as e:
        print("banner render failed:", e)
        return (None, "")


def make_draft(page_id):
    info = notion.parse_draft_page(page_id)
    pr = notion._call("GET", f"/pages/{page_id}")["properties"]
    name = "".join(x.get("plain_text", "") for x in pr.get("Email", {}).get("title", []))

    clone = hs("POST", "/marketing/v3/emails/clone",
               {"id": TEMPLATE_EMAIL_ID, "cloneName": name or "Email Machine draft"})
    eid = clone["id"]

    content = hs("GET", f"/marketing/v3/emails/{eid}")["content"]
    widgets = content["widgets"]   # ALL modules — we mutate in place + send the whole dict
    # CTA destination: the (( url )) set next to the CTA wins, else the row's
    # Landing Page, else the Get Demo page — all UTM-tagged. base_lp is the exact
    # destination the button uses (pre-UTM); snapshot() records THAT as the Landing
    # Page so the property never drifts from where the button actually points.
    base_lp = info.get("cta_dest") or resolve_landing_page(pr)
    cta_url = utm_link(base_lp, pr)

    # image placement: a leading/video/no image → native top hero module (default);
    # an image Pete dragged below copy → top_hero is None and it's inlined in the body.
    top_hero, flow = notion.email_layout(info)

    # body: rich text module (headline + flow, with any moved image inlined in place)
    widgets[BODY_MODULE].setdefault("body", {})["html"] = body_html(info, flow, eid)

    # CTA: the native button module (text + tracked destination)
    btn = widgets[BUTTON_MODULE].setdefault("body", {})
    btn["text"] = info["cta"] or "Book a Demo"
    btn["destination"] = cta_url

    # top hero module: populate it when the image belongs on top; otherwise REMOVE it
    # so the template's default image doesn't show above an inline-placed graphic.
    if top_hero is None:
        widgets.pop(HERO_MODULE, None)
        hero_kind = "inline (moved into body)"
    else:
        src, link = host_top_hero(top_hero, info, eid)
        hero_kind = {"video": "video thumbnail", "image": "top image",
                     "default": "default Bluon banner"}[top_hero[0]]
        if src and HERO_MODULE in widgets:
            hero = widgets[HERO_MODULE].setdefault("body", {})
            hero["img"] = {"src": src, "alt": info["subject"], "width": 600}
            hero["alignment"] = "center"
            if link:
                hero["link"] = link

    # Send the FULL widgets dict (logo + footer untouched) so the footer /
    # unsubscribe survive — patching only the 3 changed modules dropped them.
    patch = {"subject": info["subject"], "name": name,
             "content": {"widgets": widgets}}
    hs("PATCH", f"/marketing/v3/emails/{eid}", patch)
    print("  hero:", hero_kind)

    url = f"https://app.hubspot.com/email/{PORTAL}/edit/{eid}/content"
    notion._call("PATCH", f"/pages/{page_id}", {"properties": {"Hubspot Email": {"url": url}}})
    print("HubSpot draft created:", url, "| email id:", eid)

    snapshot(page_id, info, pr, landing_url=base_lp)
    return url


def snapshot(page_id, info, pr, landing_url=None):
    """Freeze the report-time record on the row: the final email as an image, the
    landing page url (the button's actual destination), and a screenshot of that page
    as it looks now."""
    # 1) the final email mockup, as a file property (same placement as the build)
    try:
        top_hero, flow = notion.email_layout(info)
        png = mockup.make_email_png(headline=info["subject"], flow=flow,
                                    cta=info["cta"], top_hero=top_hero)
        mockup.attach_file_to_property(page_id, "Email Image", png, "email.png")
        print("  email image attached")
    except Exception as e:
        print("  email image failed:", e)
    # 2) landing page url (what the button actually points to) + a screenshot at send time
    lp = landing_url or resolve_landing_page(pr)
    try:
        notion._call("PATCH", f"/pages/{page_id}", {"properties": {"Landing Page": {"url": lp}}})
        shot = mockup.screenshot_url(lp)
        mockup.attach_file_to_property(page_id, "Landing Page Screenshot", shot, "landing-page.png")
        print("  landing page captured:", lp)
    except Exception as e:
        print("  landing page snapshot skipped (", lp, "):", e)


def make_ab_variation(base_page, b_page):
    """Subject A/B test: make the 2nd row a NATIVE HubSpot A/B variation of the base's
    email (same body, different subject) — ONE A/B test, not two separate emails. HubSpot
    A/B is 2 versions max; recipients stay a UI step (the API can't set them on an A/B
    email). Falls back to a standalone draft if the variation can't be created."""
    base_pr = notion._call("GET", f"/pages/{base_page}")["properties"]
    m = re.search(r"/edit/(\d+)", (base_pr.get("Hubspot Email", {}) or {}).get("url") or "")
    if not m:
        print("  base email missing — standalone draft for B"); return make_draft(b_page)
    b_pr = notion._call("GET", f"/pages/{b_page}")["properties"]
    b_subject = "".join(x.get("plain_text", "") for x in (b_pr.get("Subject", {}).get("rich_text") or [])) \
        or "".join(x.get("plain_text", "") for x in b_pr.get("Email", {}).get("title", []))
    b_name = "".join(x.get("plain_text", "") for x in b_pr.get("Email", {}).get("title", []))
    try:
        var = hs("POST", "/marketing/v3/emails/ab-test/create-variation",
                 {"contentId": m.group(1), "variationName": "B"})
    except SystemExit as e:
        print("  create-variation failed — standalone draft for B:", e); return make_draft(b_page)
    vid = var.get("id")
    if not vid:
        print("  no variation id — standalone draft for B"); return make_draft(b_page)
    hs("PATCH", f"/marketing/v3/emails/{vid}", {"subject": b_subject, "name": b_name})
    url = f"https://app.hubspot.com/email/{PORTAL}/edit/{vid}/content"
    notion._call("PATCH", f"/pages/{b_page}", {"properties": {"Hubspot Email": {"url": url}}})
    print(f"  native A/B variation B (subject {b_subject!r}): {url}")
    try:
        snapshot(b_page, notion.parse_draft_page(b_page), b_pr)
    except Exception as e:
        print("  B snapshot skipped:", e)
    return url


def process(page_id):
    """Approve-once: a subject-test base expands into its A/B variant row, then becomes ONE
    native HubSpot A/B test (base = version A, the sibling = variation B) — not two separate
    emails. A plain (non-test) row just gets its own draft."""
    import variants
    ids = variants.spawn(page_id)                 # [base] or [base, B] (capped at 2)
    base = ids[0]
    base_pr = notion._call("GET", f"/pages/{base}")["properties"]
    if not (base_pr.get("Hubspot Email", {}) or {}).get("url"):
        make_draft(base)                          # version A (or a standalone email)
    for b in ids[1:]:                             # subject-test sibling → native A/B variation
        b_pr = notion._call("GET", f"/pages/{b}")["properties"]
        if (b_pr.get("Hubspot Email", {}) or {}).get("url"):
            continue                              # already drafted — re-fires no-op
        make_ab_variation(base, b)
    if len(ids) > 1:
        # publish the whole set together so the spawned variant doesn't need a second
        # manual check; already drafted + linked, so the re-fired webhook just no-ops.
        for pid in ids:
            try:
                notion.set_checkbox(pid, "Ready to Go", True)
            except Exception:
                pass


def main():
    arg = sys.argv[1].strip() if len(sys.argv) > 1 else "--ready"
    if arg == "--ready":
        # find rows that are Ready to Go but don't have a HubSpot draft yet
        import time
        targets = []
        for attempt in range(4):
            targets = []
            for r in notion.get_calendar_rows():
                if not r["ready"]:
                    continue
                pr = notion._call("GET", f"/pages/{r['id']}")["properties"]
                if not (pr.get("Hubspot Email", {}) or {}).get("url"):
                    targets.append(r["id"])
            if targets:
                break
            if attempt < 3:
                print(f"none ready yet (attempt {attempt+1}) — waiting for Notion…"); time.sleep(12)
        print(f"{len(targets)} row(s) Ready to Go without a draft")
        for pid in targets:
            process(pid)
    else:
        process(arg)


if __name__ == "__main__":
    main()
