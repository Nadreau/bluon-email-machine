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
import os, sys, json, html, time, tempfile, urllib.request, urllib.error
import notion, mockup

IMG_MODULE = "module_16043839347002"   # the template's hero image module

HS_TOKEN = os.environ.get("HUBSPOT_TOKEN", "").strip() or open(
    os.path.expanduser("~/.config/hubspot/api_key")).read().strip()
PORTAL = "6885872"
TEMPLATE_EMAIL_ID = os.environ.get("HS_TEMPLATE_ID", "32023009809")
DEMO_URL = "https://www.bluon.com/demo"
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
            return json.load(r)
    except urllib.error.HTTPError as e:
        raise SystemExit(f"HubSpot {method} {path} failed: {e.code} {e.read().decode()[:300]}")


def body_html(info):
    """WYSIWYG-safe Bluon body — only elements HubSpot's editor keeps intact."""
    out = [f'<h2 style="color:#23496d;text-align:center;font-weight:800;font-size:22px;'
           f'margin:0 0 16px">{html.escape(info["subject"])}</h2>']
    for ln in info["body_lines"]:
        ln = ln.strip()
        if not ln:
            continue
        if ln[:1] in ("-", "•", "*"):
            out.append(f'<p style="color:#23496d;font-weight:600;font-size:15px;margin:8px 0">'
                       f'&#9989;&nbsp;{html.escape(ln.lstrip("-•* ").strip())}</p>')
        else:
            out.append(f'<p style="color:#222222;font-size:15px;line-height:1.5;margin:12px 0">'
                       f'{html.escape(ln)}</p>')
    cta = html.escape(info["cta"] or "Book a Demo")
    out.append(f'<p style="text-align:center;margin:26px 0 6px">'
               f'<a href="{DEMO_URL}" style="background-color:#2f6df6;color:#ffffff;'
               f'font-weight:700;font-size:16px;padding:13px 30px;border-radius:8px;'
               f'text-decoration:none;display:inline-block">&#128197; {cta}</a></p>')
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


def resolve_hero(info, uniq):
    """Return (img_src, link) for the hero, hosting whatever's needed:
    video → hosted thumbnail + link; pasted image → hosted image; else → the
    rendered Bluon banner (default placeholder, replaceable). `uniq` keeps each
    email's hosted banner a distinct file."""
    kind, src, link = notion.detect_hero(info)
    if kind in ("video", "image"):
        return (host_image(src, f"hero-{uniq}") or src, link)
    # default → render the branded Bluon banner with this email's headline + host it
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
    rt = content["widgets"]["primary_rich_text_module"]   # keep full module, change only html
    rt.setdefault("body", {})["html"] = body_html(info)
    widgets_patch = {"primary_rich_text_module": rt}
    keep = ["primary_rich_text_module", "footer_module"]

    # hero: always place an image module at the top — real video thumbnail / pasted
    # image when identified, otherwise the branded Bluon banner (replaceable).
    src, link = resolve_hero(info, eid)
    hero_kind = "default Bluon banner" if not link and "youtube" not in str(link) else "media"
    if src and IMG_MODULE in content["widgets"]:
        imgmod = content["widgets"][IMG_MODULE]
        imgmod.setdefault("body", {})["img"] = {"src": src, "alt": info["subject"], "width": 600}
        imgmod["body"]["alignment"] = "center"
        if link:
            imgmod["body"]["link"] = link
            hero_kind = "video thumbnail"
        widgets_patch[IMG_MODULE] = imgmod
        keep = [IMG_MODULE, "primary_rich_text_module", "footer_module"]

    flex = content.get("flexAreas", {})
    try:
        for sec in flex.get("main", {}).get("sections", []):
            for col in sec.get("columns", []):
                col["widgets"] = keep
    except Exception:
        flex = None

    patch = {"subject": info["subject"], "name": name,
             "content": {"widgets": widgets_patch}}
    if flex:
        patch["content"]["flexAreas"] = flex
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
    # 1) the final email mockup, as a file property
    try:
        kind, hsrc, _ = notion.detect_hero(info)
        png = mockup.make_email_png(headline=info["subject"], body_lines=info["body_lines"],
                                    cta=info["cta"], hero_url=hsrc if kind in ("video", "image") else None)
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
            make_draft(pid)
    else:
        make_draft(arg)


if __name__ == "__main__":
    main()
