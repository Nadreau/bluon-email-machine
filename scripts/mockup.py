"""Render a Bluon-branded email mockup (HTML -> PNG) and upload it to Notion.

The image shows how the draft will roughly look as a real Bluon HubSpot email
(logo header, hero, blue headline, benefit bullets, rounded CTA button, footer).
Rendering uses headless Chrome (env CHROME_BIN, else common paths); cropping uses
Pillow. Upload uses the Notion File Upload API (NOTION_TOKEN).
"""
import os, json, time, subprocess, tempfile, urllib.request, urllib.error, html

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


def build_html(*, headline, body_lines, cta, hero_b64=None):
    bullets, paras = [], []
    for ln in body_lines:
        ln = ln.strip()
        if not ln:
            continue
        if ln[:1] in ("-", "•", "*"):
            bullets.append(html.escape(ln.lstrip("-•* ").strip()))
        else:
            paras.append(html.escape(ln))
    bullets_html = ("<ul style='margin:18px 0;padding-left:0;list-style:none'>" +
                    "".join(f"<li style=\"margin:8px 0;padding-left:26px;position:relative;"
                            f"color:#23496d;font-weight:600\"><span style='position:absolute;left:0'>✅</span>{b}</li>"
                            for b in bullets) + "</ul>") if bullets else ""
    paras_html = "".join(f"<p style='margin:12px 0;color:#222;font-size:15px;line-height:1.5'>{p}</p>"
                         for p in paras)
    if hero_b64:
        hero = (f"<div style='margin:0 20px'><img src='{hero_b64}' "
                f"style='width:100%;border-radius:8px;display:block'></div>")
    else:
        hero = (f"<div style=\"margin:0 20px;height:230px;border-radius:8px;"
                f"background:linear-gradient(135deg,#2f6df6,#23496d);position:relative;overflow:hidden\">"
                f"<div style='position:absolute;top:24px;left:24px;right:24px;color:#fff;font-size:22px;"
                f"font-weight:800;text-shadow:0 1px 3px rgba(0,0,0,.4);line-height:1.15'>{html.escape(headline)}</div>"
                f"<div style='position:absolute;top:50%;left:50%;transform:translate(-50%,-50%);width:64px;"
                f"height:64px;background:rgba(255,0,0,.85);border-radius:14px;display:flex;align-items:center;"
                f"justify-content:center'><span style='color:#fff;font-size:26px'>&#9654;</span></div></div>")
    return f"""<!doctype html><html><head><meta charset='utf-8'></head>
<body style="margin:0;background:{PAGE_BG};font-family:Arial,Helvetica,sans-serif">
  <div style="width:600px;margin:0 auto;background:#fff;border:1px solid #e3e3e3">
    <div style="text-align:center;padding:22px 0 10px">
      <span style="font-size:26px;font-weight:800;color:#2f6df6;letter-spacing:-1px">bluon</span>
      <span style="font-size:12px;font-weight:700;color:#23496d;letter-spacing:2px;vertical-align:middle">&nbsp;FOR BUSINESS</span>
    </div>
    {hero}
    <div style="padding:22px 28px 8px;text-align:center">
      <div style="font-size:22px;font-weight:800;color:#23496d;line-height:1.2">{html.escape(headline)}</div>
    </div>
    <div style="padding:4px 32px 8px">{paras_html}{bullets_html}</div>
    <div style="text-align:center;padding:8px 0 26px">
      <span style="display:inline-block;background:linear-gradient(135deg,#5b6bf0,#2f6df6);color:#fff;
                   font-weight:700;font-size:16px;padding:13px 30px;border-radius:8px">&#128197;&nbsp;{html.escape(cta)}</span>
    </div>
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
            im.crop((0, 0, im.size[0], min(im.size[1], bbox[3] + 40))).save(out_png)
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


def make_mockup_upload(*, headline, body_lines, cta, hero_url=None, filename="mockup.png"):
    """Render + upload; return a Notion file_upload id, or None on failure.
    hero_url: a pasted image to use as the hero (else gradient video placeholder)."""
    try:
        hero_b64 = fetch_hero_b64(hero_url) if hero_url else None
        html_str = build_html(headline=headline, body_lines=body_lines, cta=cta, hero_b64=hero_b64)
        png = render_png(html_str, tempfile.mktemp(suffix=".png"))
        return upload_png(png, filename)
    except Exception as e:
        print("mockup failed:", e)
        return None
