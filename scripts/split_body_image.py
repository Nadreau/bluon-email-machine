"""Post-build: turn an inline body <img> into a NATIVE dnd image module.

HubSpot's rich-text module does not reliably render an <img> dropped mid-paragraph
(it re-flows/strips complex HTML) — so a graphic placed inline in the copy shows up
"missing" in the editor even though it's in the stored HTML. A NATIVE image module
renders reliably (it's what shows when the graphic is the hero). This splits the body
rich-text module at the inline image into two rich-text modules and drops a native
image module between them, so the graphic renders exactly where it sat in the copy.

  python scripts/split_body_image.py <email_id> [<email_id> ...]
"""
import sys, re, copy, json
import to_hubspot

BODY = to_hubspot.BODY_MODULE           # module_17406888513524 (rich text)
BUTTON = to_hubspot.BUTTON_MODULE       # module_17810258159061
IMG = "module_bodyimg"                  # arbitrary id; module_id comes from the template's image module
IMG_H = None                            # natural height (HubSpot scales to width)


def _image_module(src, template_img_mod):
    m = copy.deepcopy(template_img_mod)
    m["id"] = IMG; m["name"] = IMG
    m.setdefault("body", {})
    m["body"]["img"] = {"src": src, "alt": "", "width": 560, "loading": "disabled"}
    m["body"].pop("link", None)          # not a video thumbnail — no click-through
    m["body"]["hs_wrapper_css"] = {"padding-bottom": "10px", "padding-left": "30px",
                                   "padding-right": "30px", "padding-top": "10px"}
    return m


def split(eid, template_img_mod):
    e = to_hubspot.hs("GET", f"/marketing/v3/emails/{eid}")
    content = e["content"]; widgets = content["widgets"]; fa = content["flexAreas"]["main"]
    if BODY not in widgets:
        print(f"  {eid}: no body module — skip"); return False
    html = widgets[BODY]["body"]["html"]
    m = re.search(r'<img[^>]*src="([^"]+)"[^>]*>', html)
    if not m:
        print(f"  {eid}: no inline <img> in body — skip"); return False
    src = m.group(1)
    part1 = html[:m.start()].rstrip()
    part2 = html[m.end():].lstrip()
    if not part2:
        print(f"  {eid}: image is at the end of the body, no split needed — skip"); return False

    widgets[BODY]["body"]["html"] = part1
    close = copy.deepcopy(widgets[BODY]); close["id"] = "module_bodyclose"; close["name"] = "module_bodyclose"
    close["body"]["html"] = part2
    close["body"]["hs_wrapper_css"] = {"padding-bottom": "0px", "padding-left": "30px",
                                       "padding-right": "30px", "padding-top": "0px"}
    widgets["module_bodyclose"] = close
    widgets[IMG] = _image_module(src, template_img_mod)

    # rebuild the body column's widget order: part1 -> image -> close -> (button); drop
    # any standalone section that only referenced the (now-relocated) image module.
    for sec in fa["sections"]:
        for col in sec["columns"]:
            if BODY in col["widgets"]:
                tail = [w for w in col["widgets"] if w == BUTTON]
                col["widgets"] = [BODY, IMG, "module_bodyclose"] + tail
    fa["sections"] = [s for s in fa["sections"]
                      if not any(IMG in c["widgets"] and BODY not in c["widgets"] for c in s["columns"])]

    to_hubspot.hs("PATCH", f"/marketing/v3/emails/{eid}",
                  {"content": {"widgets": widgets, "flexAreas": content["flexAreas"]}})
    print(f"  {eid}: split OK (part1 {len(part1)}c + image + close {len(part2)}c)")
    return True


def _template_img_mod():
    # the template's known-good native IMAGE module (the hero) is the blueprint;
    # split() re-ids the copy to IMG so it never collides with a real hero.
    t = to_hubspot.hs("GET", f"/marketing/v3/emails/{to_hubspot.TEMPLATE_EMAIL_ID}")
    return t["content"]["widgets"][to_hubspot.HERO_MODULE]


if __name__ == "__main__":
    timg = _template_img_mod()
    for eid in sys.argv[1:]:
        split(eid.strip(), timg)
