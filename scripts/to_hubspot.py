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
FNAME_TOKEN = '{{ contact.firstname|default("there") }}'


def personalize(escaped):
    """Turn a generic greeting / placeholder in the (already-escaped) copy into the
    HubSpot first-name token. 'Hey there,' -> 'Hey {{ firstname|default(there) }},'.
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

# Smart landing-page defaults — picked by Campaign first, then audience. A url
# already set on the row (manual override) is always respected over these.
# TODO(niko): confirm the real Live Tech Support LP url once Prouty ships it.
LANDING_PAGES = {
    "Live Tech Support": "https://www.bluon.com/live-tech-support",
}
DEFAULT_LP = DEMO_URL


def resolve_landing_page(pr):
    """Existing row url wins; else map by Campaign; else the demo page."""
    existing = (pr.get("Landing Page", {}) or {}).get("url")
    if existing:
        return existing
    camp = (pr.get("Campaign", {}).get("select") or {}).get("name")
    return LANDING_PAGES.get(camp, DEFAULT_LP)


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
            hosted = host_image(it.get("url"), f"body-{uniq}-{n}") or it.get("url")
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


def upload_png(png_path, name="bluon-hero"):
    """Upload a local PNG to HubSpot Files → hosted url (for the rendered banner)."""
    boundary = "----bluonhero88"
    fields = {"folderPath": "/email-machine",
              "options": json.dumps({"access": "PUBLIC_INDEXABLE", "overwrite": True})}
    body = bytearray()
    for k, v in fields.items():
        body += f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode()
    body += (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; '
             f'filename="{name}.png"\r\nContent-Type: image/png\r\n\r\n').encode()
    body += open(png_path, "rb").read()
    body += f"\r\n--{boundary}--\r\n".encode()
    req = urllib.request.Request("https://api.hubapi.com/files/v3/files", data=bytes(body),
        method="POST", headers={"Authorization": f"Bearer {HS_TOKEN}",
                                "Content-Type": f"multipart/form-data; boundary={boundary}"})
    try:
        with urllib.request.urlopen(req, timeout=90) as r:
            return json.load(r).get("url")
    except Exception as e:
        print("upload_png failed:", e)
        return None


def host_top_hero(top_hero, info, uniq):
    """Host the TOP hero slot's image and return (img_src, link). top_hero is
    ("video"|"image"|"default", src, link): video → hosted thumbnail + watch link;
    image → hosted image; default → the rendered branded Bluon banner."""
    kind, src, link = top_hero
    if kind in ("video", "image"):
        return (host_image(src, f"hero-{uniq}") or src, link)
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
    # Landing Page, else the Get Demo page — all UTM-tagged.
    cta_url = utm_link(info.get("cta_dest") or resolve_landing_page(pr), pr)

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

    snapshot(page_id, info, pr)
    return url


def snapshot(page_id, info, pr):
    """Freeze the report-time record on the row: the final email as an image, the
    landing page url (smart default), and a screenshot of that page as it looks now."""
    # 1) the final email mockup, as a file property (same placement as the build)
    try:
        top_hero, flow = notion.email_layout(info)
        png = mockup.make_email_png(headline=info["subject"], flow=flow,
                                    cta=info["cta"], top_hero=top_hero)
        mockup.attach_file_to_property(page_id, "Email Image", png, "email.png")
        print("  email image attached")
    except Exception as e:
        print("  email image failed:", e)
    # 2) landing page url + a screenshot of it at send time
    lp = resolve_landing_page(pr)
    try:
        notion._call("PATCH", f"/pages/{page_id}", {"properties": {"Landing Page": {"url": lp}}})
        shot = mockup.screenshot_url(lp)
        mockup.attach_file_to_property(page_id, "Landing Page Screenshot", shot, "landing-page.png")
        print("  landing page captured:", lp)
    except Exception as e:
        print("  landing page snapshot skipped (", lp, "):", e)


def process(page_id):
    """Approve-once-spawn: a subject-test base expands into its A/B/C variant rows
    first, then each variant (and any plain row) gets its own HubSpot draft."""
    import variants
    ids = variants.spawn(page_id)                 # [base] or [base, B, C] for a subject test
    for pid in ids:
        pr = notion._call("GET", f"/pages/{pid}")["properties"]
        if (pr.get("Hubspot Email", {}) or {}).get("url"):
            continue                              # already drafted — re-fires are no-ops
        make_draft(pid)
    if len(ids) > 1:
        # publish the whole fanned set together so the spawned variants don't need a
        # second manual check; they're already drafted + linked, so the webhook
        # re-fires this triggers just no-op (skip on "already drafted").
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
