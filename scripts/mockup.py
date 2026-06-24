"""Render a Bluon-branded email mockup (HTML -> PNG) and upload it to Notion.

The image shows how the draft will roughly look as a real Bluon HubSpot email
(logo header, hero, blue headline, benefit bullets, rounded CTA button, footer).
Rendering uses headless Chrome (env CHROME_BIN, else common paths); cropping uses
Pillow. Upload uses the Notion File Upload API (NOTION_TOKEN).
"""
import os, re, json, time, subprocess, tempfile, urllib.request, urllib.error, html

# Show the first-name personalization in the PREVIEW the way HubSpot will render it:
# "Hey there," -> "Hey [First Name]," (and any {firstname} placeholder). Display-only —
# mirrors to_hubspot.personalize() so the mockup reflects the personalized greeting.
_GREET_RE = re.compile(r"(?i)\b(hey|hi|hello)([,!]?\s+)there\b")


def _disp_name(t):
    t = _GREET_RE.sub(lambda m: m.group(1) + m.group(2) + "[First Name]", t)
    return re.sub(r"\{\{?\s*first[ _]?name\s*\}?\}", "[First Name]", t, flags=re.I)

NV = "2022-06-28"
API = "https://api.notion.com/v1"
TOKEN = os.environ.get("NOTION_TOKEN", "").strip()
PAGE_BG = "#e9edf2"   # outer background, used for auto-crop

CHROME_CANDIDATES = [
    os.environ.get("CHROME_BIN", ""),
    "google-chrome", "google-chrome-stable", "chromium-browser", "chromium",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
]


def _chrome():
    from shutil import which
    for c in CHROME_CANDIDATES:
        if not c:
            continue
        if os.path.isfile(c) or which(c):
            return c
    raise SystemExit("No Chrome/Chromium found for rendering (set CHROME_BIN).")


def fetch_hero_b64(url):
    """Download a pasted image (Notion/external URL) → data URI for inlining."""
    if not url:
        return None
    try:
        import base64
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=30) as r:
            data = r.read()
            ct = r.headers.get("Content-Type", "image/png").split(";")[0]
        return f"data:{ct};base64," + base64.b64encode(data).decode()
    except Exception as e:
        print("hero fetch failed:", e)
        return None


def _img_html(b64, *, top=False):
    """An inline <img> (data URI) sized for the email column. `top` tightens the
    margin when it's the hero sitting above the headline."""
    if not b64:
        return ""
    margin = "0 0 6px" if top else "14px auto"
    return (f"<img src='{b64}' style='width:100%;max-width:560px;height:auto;"
            f"border-radius:8px;display:block;margin:{margin}'>")


def _banner_html(headline):
    return (
        "<table role='presentation' width='100%' cellpadding='0' cellspacing='0' "
        "style='border-radius:8px;overflow:hidden;margin:0 0 4px'><tr>"
        "<td align='center' style='background:linear-gradient(135deg,#2f6df6,#23496d);"
        "background-color:#2f6df6;padding:46px 26px'>"
        f"<div style='color:#ffffff;font-size:22px;font-weight:800;line-height:1.15'>{html.escape(headline)}</div>"
        "<div style='margin-top:18px'><span style='display:inline-block;background:#e53935;"
        "color:#ffffff;border-radius:10px;padding:6px 16px;font-size:20px'>&#9654;</span></div>"
        "</td></tr></table>")


def inner_email_html(headline, flow, cta, *, top_hero_b64=None, top_is_banner=False,
                     cta_url="https://www.bluon.com/demo"):
    """The shared Bluon email design — top hero (image / banner, or nothing when the
    image sits inline), blue headline, ordered body (paragraphs / check-bullets /
    inline images wherever Pete placed them), gradient CTA button. `flow` items are
    {"kind": "para"|"bullet"|"image", "text"/"_b64": ...}; image items carry a
    pre-fetched data URI in "_b64"."""
    if top_hero_b64:
        hero = _img_html(top_hero_b64, top=True)
    elif top_is_banner:
        hero = _banner_html(headline)
    else:
        hero = ""                      # image was moved down → it renders in the flow
    parts = []
    for it in flow:
        k = it.get("kind")
        if k == "image":
            parts.append(_img_html(it.get("_b64")))
        elif k == "bullet":
            parts.append(
                "<p style='margin:8px 0;color:#23496d;font-weight:600;font-size:15px;line-height:1.4'>"
                f"&#9989;&nbsp;{html.escape(_disp_name(it.get('text', '')))}</p>")
        else:
            parts.append(
                f"<p style='margin:12px 0;color:#222222;font-size:15px;line-height:1.5'>{html.escape(_disp_name(it.get('text', '')))}</p>")
    body_inner = "".join(parts)
    button = (
        "<table role='presentation' align='center' cellpadding='0' cellspacing='0' style='margin:22px auto 6px'>"
        "<tr><td bgcolor='#2f6df6' style='border-radius:8px;"
        "background:linear-gradient(135deg,#5b6bf0,#2f6df6)'>"
        f"<a href='{cta_url}' style='display:inline-block;padding:13px 30px;color:#ffffff;"
        f"font-weight:700;font-size:16px;text-decoration:none'>&#128197;&nbsp;{html.escape(cta)}</a>"
        "</td></tr></table>")
    return (
        f"{hero}"
        "<div style='text-align:center;padding:18px 6px 2px'>"
        f"<div style='font-size:22px;font-weight:800;color:#23496d;line-height:1.2'>{html.escape(headline)}</div></div>"
        f"<div style='padding:4px 10px'>{body_inner}</div>"
        f"{button}")


def _prep_flow(flow):
    """Fetch each inline image to a data URI so render_png can inline it."""
    out = []
    for it in (flow or []):
        it = dict(it)
        if it.get("kind") == "image":
            it["_b64"] = fetch_hero_b64(it.get("url"))
        out.append(it)
    return out


def _top_hero_pieces(top_hero):
    """(kind, src, link) | None  ->  (top_hero_b64, top_is_banner) for rendering."""
    if not top_hero:
        return (None, False)
    kind, src, link = top_hero
    if kind in ("video", "image"):
        return (fetch_hero_b64(src), False)
    if kind == "default":
        return (None, True)
    return (None, False)


def hero_banner_html(headline):
    """Standalone Bluon gradient banner (the mockup's default hero). Background is
    PAGE_BG so render_png crops tight to the banner (no dead white space)."""
    return f"""<!doctype html><html><head><meta charset='utf-8'></head>
<body style="margin:0;background:{PAGE_BG};font-family:Arial,Helvetica,sans-serif">
  <table role='presentation' width='100%' cellpadding='0' cellspacing='0'><tr>
  <td align='center' style='background:linear-gradient(135deg,#2f6df6,#23496d);background-color:#2f6df6;padding:38px 32px'>
    <div style='color:#ffffff;font-size:24px;font-weight:800;line-height:1.15'>{html.escape(headline)}</div>
    <div style='margin-top:18px'><span style='display:inline-block;background:#e53935;color:#ffffff;
      border-radius:12px;padding:7px 18px;font-size:24px'>&#9654;</span></div>
  </td></tr></table>
</body></html>"""


def build_html(*, headline, flow, cta, top_hero_b64=None, top_is_banner=False,
               cta_url="https://www.bluon.com/demo"):
    inner = inner_email_html(headline, flow, cta, top_hero_b64=top_hero_b64,
                             top_is_banner=top_is_banner, cta_url=cta_url)
    return f"""<!doctype html><html><head><meta charset='utf-8'></head>
<body style="margin:0;background:{PAGE_BG};font-family:Arial,Helvetica,sans-serif">
  <div style="width:600px;margin:0 auto;background:#fff;border:1px solid #e3e3e3;padding:0 20px 18px">
    <div style="text-align:center;padding:22px 0 12px">
      <span style="font-size:26px;font-weight:800;color:#2f6df6;letter-spacing:-1px">bluon</span>
      <span style="font-size:12px;font-weight:700;color:#23496d;letter-spacing:2px;vertical-align:middle">&nbsp;FOR BUSINESS</span>
    </div>
    {inner}
  </div>
  <div style="width:600px;margin:10px auto 24px;text-align:center;color:#7a8aa0;font-size:11px;line-height:1.6">
    Bluon, Inc., 9160 Irvine Center Drive, Suite 100, Irvine, CA<br>
    <span style="color:#3574E3;text-decoration:underline">Unsubscribe</span> | Manage preferences
  </div>
</body></html>"""


def render_png(html_str, out_png):
    with tempfile.NamedTemporaryFile("w", suffix=".html", delete=False) as f:
        f.write(html_str); html_path = f.name
    profile = tempfile.mkdtemp(prefix="chrome-mockup-")
    # Launch detached and poll for the screenshot — headless Chrome often LINGERS
    # after writing the file (GCM/zygote), so waiting on its exit would hang.
    proc = subprocess.Popen(
        [_chrome(), "--headless=new", "--disable-gpu", "--no-sandbox",
         "--disable-dev-shm-usage", f"--user-data-dir={profile}", "--hide-scrollbars",
         "--window-size=620,1600", f"--screenshot={out_png}", "file://" + html_path],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    waited = 0.0
    while waited < 40:
        if os.path.exists(out_png) and os.path.getsize(out_png) > 1500:
            break
        time.sleep(0.5); waited += 0.5
    try:
        proc.terminate(); proc.wait(timeout=5)
    except Exception:
        proc.kill()
    if not (os.path.exists(out_png) and os.path.getsize(out_png) > 1500):
        raise RuntimeError("Chrome produced no screenshot")
    # auto-crop trailing background
    try:
        from PIL import Image, ImageChops
        im = Image.open(out_png).convert("RGB")
        bg = Image.new("RGB", im.size, PAGE_BG)
        diff = ImageChops.difference(im, bg)
        bbox = diff.getbbox()
        if bbox:
            im.crop((0, 0, im.size[0], min(im.size[1], bbox[3] + 12))).save(out_png)
    except Exception:
        pass
    return out_png


# ---------- Notion file upload ----------
def _api(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(API + path, data=data, method=method,
        headers={"Authorization": f"Bearer {TOKEN}", "Notion-Version": NV,
                 "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


def upload_png(png_path, filename="mockup.png"):
    up = _api("POST", "/file_uploads", {"filename": filename, "content_type": "image/png"})
    upload_url, fid = up["upload_url"], up["id"]
    # multipart/form-data with the file part
    boundary = "----bluonmockup7c1d"
    body = bytearray()
    body += f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{filename}\"\r\nContent-Type: image/png\r\n\r\n".encode()
    body += open(png_path, "rb").read()
    body += f"\r\n--{boundary}--\r\n".encode()
    req = urllib.request.Request(upload_url, data=bytes(body), method="POST",
        headers={"Authorization": f"Bearer {TOKEN}", "Notion-Version": NV,
                 "Content-Type": f"multipart/form-data; boundary={boundary}"})
    with urllib.request.urlopen(req, timeout=120) as r:
        json.load(r)
    return fid   # attach via {"type":"image","image":{"type":"file_upload","file_upload":{"id":fid}}}


def make_mockup_upload(*, headline, flow, cta, top_hero=None,
                       cta_url="https://www.bluon.com/demo", filename="mockup.png"):
    """Render + upload; return a Notion file_upload id, or None on failure.
    flow: ordered body items (paras/bullets/inline images). top_hero: ("image"|
    "video"|"default", src, link) for the top slot, or None when the image is inline."""
    try:
        b64, banner = _top_hero_pieces(top_hero)
        html_str = build_html(headline=headline, flow=_prep_flow(flow), cta=cta,
                              top_hero_b64=b64, top_is_banner=banner, cta_url=cta_url)
        png = render_png(html_str, tempfile.mktemp(suffix=".png"))
        return upload_png(png, filename)
    except Exception as e:
        print("mockup failed:", e)
        return None


def make_email_png(*, headline, flow, cta, top_hero=None,
                   cta_url="https://www.bluon.com/demo", out_png=None):
    """Render the full email mockup to a local PNG and return its path."""
    b64, banner = _top_hero_pieces(top_hero)
    html_str = build_html(headline=headline, flow=_prep_flow(flow), cta=cta,
                          top_hero_b64=b64, top_is_banner=banner, cta_url=cta_url)
    return render_png(html_str, out_png or tempfile.mktemp(suffix=".png"))


def screenshot_url(url, out_png=None, window="1280,2200"):
    """Headless-Chrome screenshot of a LIVE url (e.g. a landing page) → PNG path.
    Used to freeze how a landing page looked at send time."""
    out_png = out_png or tempfile.mktemp(suffix=".png")
    profile = tempfile.mkdtemp(prefix="chrome-shot-")
    proc = subprocess.Popen(
        [_chrome(), "--headless=new", "--disable-gpu", "--no-sandbox",
         "--disable-dev-shm-usage", f"--user-data-dir={profile}", "--hide-scrollbars",
         f"--window-size={window}", "--virtual-time-budget=8000",
         f"--screenshot={out_png}", url],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    waited = 0.0
    while waited < 45:
        if os.path.exists(out_png) and os.path.getsize(out_png) > 1500:
            break
        time.sleep(0.5); waited += 0.5
    try:
        proc.terminate(); proc.wait(timeout=5)
    except Exception:
        proc.kill()
    if not (os.path.exists(out_png) and os.path.getsize(out_png) > 1500):
        raise RuntimeError("Chrome produced no landing-page screenshot")
    return out_png


def attach_file_to_property(page_id, prop_name, png_path, filename):
    """Upload a PNG and set it as the value of a Notion *files* property."""
    fid = upload_png(png_path, filename)
    _api("PATCH", f"/pages/{page_id}", {"properties": {prop_name: {
        "files": [{"type": "file_upload", "name": filename, "file_upload": {"id": fid}}]}}})
    return fid
