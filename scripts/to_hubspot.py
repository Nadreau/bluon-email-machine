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
import os, sys, json, html, urllib.request, urllib.error
import notion

HS_TOKEN = os.environ.get("HUBSPOT_TOKEN", "").strip() or open(
    os.path.expanduser("~/.config/hubspot/api_key")).read().strip()
PORTAL = "6885872"
TEMPLATE_EMAIL_ID = os.environ.get("HS_TEMPLATE_ID", "32023009809")
DEMO_URL = "https://www.bluon.com/demo"
HS = "https://api.hubapi.com"


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


def make_draft(page_id):
    info = notion.parse_draft_page(page_id)
    pr = notion._call("GET", f"/pages/{page_id}")["properties"]
    name = "".join(x.get("plain_text", "") for x in pr.get("Email", {}).get("title", []))

    clone = hs("POST", "/marketing/v3/emails/clone",
               {"id": TEMPLATE_EMAIL_ID, "cloneName": name or "Email Machine draft"})
    eid = clone["id"]

    content = hs("GET", f"/marketing/v3/emails/{eid}")["content"]
    flex = content.get("flexAreas", {})
    try:
        for sec in flex.get("main", {}).get("sections", []):
            for col in sec.get("columns", []):
                col["widgets"] = [w for w in col.get("widgets", [])
                                  if w in ("primary_rich_text_module", "footer_module")]
    except Exception:
        flex = None

    rt = content["widgets"]["primary_rich_text_module"]   # keep full module, change only html
    rt.setdefault("body", {})["html"] = body_html(info)

    patch = {"subject": info["subject"], "name": name,
             "content": {"widgets": {"primary_rich_text_module": rt}}}
    if flex:
        patch["content"]["flexAreas"] = flex
    hs("PATCH", f"/marketing/v3/emails/{eid}", patch)

    url = f"https://app.hubspot.com/email/{PORTAL}/edit/{eid}/content"
    notion._call("PATCH", f"/pages/{page_id}", {"properties": {"Hubspot Email": {"url": url}}})
    print("HubSpot draft created:", url, "| email id:", eid)
    return url


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
