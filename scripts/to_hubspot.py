"""Create a HubSpot draft email from a Notion draft row and write the draft
link back into the row's "Hubspot Email" property.

Fired when a reviewer checks "Ready to Go": clones a Bluon template email (so the
draft inherits the real logo header, footer, and brand styling), swaps in this
row's subject + body, trims the layout to just our content, and drops the editor
link into Notion so they can click straight through to finish/send in HubSpot.

  python to_hubspot.py <PAGE_ID>
"""
import os, sys, json, urllib.request, urllib.error
import notion, mockup

HS_TOKEN = os.environ.get("HUBSPOT_TOKEN", "").strip() or open(
    os.path.expanduser("~/.config/hubspot/api_key")).read().strip()
PORTAL = "6885872"
TEMPLATE_EMAIL_ID = os.environ.get("HS_TEMPLATE_ID", "32023009809")  # clean Bluon base to clone
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


def main():
    page_id = sys.argv[1].strip()
    info = notion.parse_draft_page(page_id)
    pr = notion._call("GET", f"/pages/{page_id}")["properties"]
    name = "".join(x.get("plain_text", "") for x in pr.get("Email", {}).get("title", []))

    # 1) clone the template (inherits logo/footer/brand style)
    clone = hs("POST", "/marketing/v3/emails/clone",
               {"id": TEMPLATE_EMAIL_ID, "cloneName": name or "Email Machine draft"})
    eid = clone["id"]

    # 2) trim the cloned layout to just our rich-text + footer, then fill it.
    #    IMPORTANT: change ONLY body.html on the existing module — preserve
    #    type/path(@hubspot/rich_text)/module_id/order so HubSpot can render it.
    content = hs("GET", f"/marketing/v3/emails/{eid}")["content"]
    flex = content.get("flexAreas", {})
    try:
        for sec in flex.get("main", {}).get("sections", []):
            for col in sec.get("columns", []):
                col["widgets"] = [w for w in col.get("widgets", [])
                                  if w in ("primary_rich_text_module", "footer_module")]
    except Exception:
        flex = None

    rt = content["widgets"]["primary_rich_text_module"]   # full module object
    rt.setdefault("body", {})["html"] = mockup.inner_email_html(
        info["subject"], info["body_lines"], info["cta"])

    patch = {"subject": info["subject"], "name": name,
             "content": {"widgets": {"primary_rich_text_module": rt}}}
    if flex:
        patch["content"]["flexAreas"] = flex
    hs("PATCH", f"/marketing/v3/emails/{eid}", patch)

    # 3) write the editor link back into Notion's "Hubspot Email" property
    url = f"https://app.hubspot.com/email/{PORTAL}/edit/{eid}/content"
    notion._call("PATCH", f"/pages/{page_id}", {"properties": {"Hubspot Email": {"url": url}}})
    print("HubSpot draft created:", url, "| email id:", eid)


if __name__ == "__main__":
    main()
