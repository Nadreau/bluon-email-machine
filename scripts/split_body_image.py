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
    pieces = re.split(r'<img[^>]*src="([^"]+)"[^>]*>', html)
    if len(pieces) < 3:
        print(f"  {eid}: no inline <img> in body — skip"); return False
    # pieces alternates [text, src, text, src, ..., text] — EVERY inline image
    # becomes its own native module (a page can carry several body graphics;
    # splitting only the first left image #2+ invisible, which Tanner hit live).
    widgets[BODY]["body"]["html"] = pieces[0].rstrip()
    order = [BODY]
    n_img = 0
    for i in range(1, len(pieces), 2):
        n_img += 1
        img_id = IMG if n_img == 1 else f"{IMG}{n_img}"
        mod = _image_module(pieces[i], template_img_mod)
        mod["id"] = img_id; mod["name"] = img_id
        widgets[img_id] = mod
        order.append(img_id)
        tail = pieces[i + 1].strip()
        if tail:   # copy continues after this image → its own close module
            close_id = "module_bodyclose" if n_img == 1 else f"module_bodyclose{n_img}"
            close = copy.deepcopy(widgets[BODY]); close["id"] = close_id; close["name"] = close_id
            close["body"]["html"] = tail
            close["body"]["hs_wrapper_css"] = {"padding-bottom": "0px", "padding-left": "30px",
                                               "padding-right": "30px", "padding-top": "0px"}
            widgets[close_id] = close
            order.append(close_id)
    # rebuild the body column's widget order (image at the END of the body is a real
    # case too — e.g. a headshot under the signature — and still must become a native
    # module: an inline <img> doesn't render wherever it sits); drop any standalone
    # section that only referenced the (now-relocated) image module.
    for sec in fa["sections"]:
        for col in sec["columns"]:
            if BODY in col["widgets"]:
                tail = [w for w in col["widgets"] if w == BUTTON]
                col["widgets"] = order + tail
    fa["sections"] = [s for s in fa["sections"]
                      if not any(IMG in c["widgets"] and BODY not in c["widgets"] for c in s["columns"])]

    to_hubspot.hs("PATCH", f"/marketing/v3/emails/{eid}",
                  {"content": {"widgets": widgets, "flexAreas": content["flexAreas"]}})
    print(f"  {eid}: split OK ({n_img} image(s) → native, {len(order)} body modules)")
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
