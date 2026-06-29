"""Pull Anevo send stats from their shared Google Sheet into the Email Reporting DB.

Anevo (our capacity-limited email partner) logs every send in a Google Sheet that's
link-shared, so a CSV export reads with no auth. This mirrors those sends into the
same "Email Reporting — Automated" DB as our HubSpot sends, tagged Source=Anevo, so
all email reporting — ours and Anevo's — lives in one place.

Idempotent: each row is keyed by (Source=Anevo, Subject); re-running updates in place.

METRIC NOTE: Anevo's sheet reports "Click Rate" as click-TO-OPEN (clicks / opens).
We store CTR = clicks / SENT to match HubSpot's clicks/delivered, so the CTR column
means the same thing on every row. (Anevo's own headline % will read much higher than
our CTR column for that reason — they're different denominators.) Each row's page body
records Anevo's native open/click rates verbatim so nothing the sheet says is lost.

  python scripts/anevo_report.py            # pull + upsert rows
  python scripts/anevo_report.py --images   # also render a subject-card thumbnail
"""
import sys, re, csv, io, urllib.request
import notion

REPORTING_DB = "38e576a5-c12d-81b7-a5a8-d2e1e2f5433a"
SHEET_ID = "1mGt8F1KjuEEVZwMqR00fS56wSE76mTmNEElkigOGaYE"
GID = "642705231"
CSV_URL = f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/export?format=csv&gid={GID}"

LETTERS = "ABCDEF"


def _fetch_rows():
    req = urllib.request.Request(CSV_URL, headers={"User-Agent": "Mozilla/5.0"})
    text = urllib.request.urlopen(req, timeout=30).read().decode("utf-8", "replace")
    return [r for r in csv.DictReader(io.StringIO(text))
            if (r.get("Subject Line") or "").strip()]


def _count_pct(cell):
    """'1632 (36.9%)' -> (1632, 0.369). Tolerates a stray ')' and missing pieces."""
    cell = (cell or "").strip()
    m = re.search(r"([\d,]+)", cell)
    count = int(m.group(1).replace(",", "")) if m else None
    p = re.search(r"([\d.]+)\s*%", cell)
    pct = float(p.group(1)) / 100 if p else None
    return count, pct


def _audience(campaign):
    c = (campaign or "").lower()
    for name in ("ServiceTitan", "Residential", "Commercial", "Churned"):
        if name.lower() in c:
            return name
    return None


def _send_date(campaign):
    m = re.search(r"(\d{1,2})/(\d{1,2})/(\d{2,4})", campaign or "")
    if not m:
        return None
    mo, d, y = m.groups()
    y = int(y)
    y = y + 2000 if y < 100 else y
    return f"{y:04d}-{int(mo):02d}-{int(d):02d}"


def _existing_anevo():
    """{subject -> page_id} for rows already in the DB with Source=Anevo (idempotency)."""
    out = {}
    cur = None
    while True:
        body = {"page_size": 100}
        if cur:
            body["start_cursor"] = cur
        res = notion._call("POST", f"/databases/{REPORTING_DB}/query", body)
        for r in res["results"]:
            pr = r["properties"]
            if (pr.get("Source", {}).get("select") or {}).get("name") == "Anevo":
                subj = "".join(x.get("plain_text", "") for x in (pr.get("Subject", {}).get("rich_text") or []))
                out[subj.strip()] = r["id"]
        if not res.get("has_more"):
            break
        cur = res["get_next_cursor"] if "get_next_cursor" in res else res.get("next_cursor")
    return out


def run(images=False):
    rows = _fetch_rows()
    if not rows:
        print("Anevo sheet returned no rows.")
        return
    # group sends that belong to the same test (same campaign + email name) so the
    # variants share a Test value and get A/B letters in sheet order
    groups = {}
    for r in rows:
        key = ((r.get("Campaign") or "").strip(), (r.get("Email") or "").strip())
        groups.setdefault(key, []).append(r)

    existing = _existing_anevo()
    for (campaign, email_name), members in groups.items():
        aud = _audience(campaign)
        date = _send_date(campaign)
        md = re.search(r"(\d{1,2}/\d{1,2})", campaign or "")
        test = f"Anevo · {aud or 'Send'}" + (f" {md.group(1)}" if md else "")
        multi = len(members) > 1
        for i, r in enumerate(members):
            subj = (r.get("Subject Line") or "").strip()
            sent, _ = _count_pct(r.get("Total # Sent"))
            sent = sent or _count_pct(r.get("Total # Sent") or "")[0]
            opens, open_pct = _count_pct(r.get("Open Rate"))
            clicks, click_pct = _count_pct(r.get("Click Rate"))  # click_pct = Anevo CTOR
            open_rate = (opens / sent) if (opens and sent) else open_pct
            ctr = (clicks / sent) if (clicks and sent) else None
            letter = LETTERS[i] if multi else None

            props = {
                "Name": {"title": [{"type": "text", "text": {
                    "content": f"Anevo — {aud or 'Send'}" + (f" — {letter}" if letter else "")}}]},
                "Source": {"select": {"name": "Anevo"}},
                "Test": {"select": {"name": test}},
                "Subject": {"rich_text": [{"type": "text", "text": {"content": subj[:1900]}}]},
            }
            if letter:
                props["Variant"] = {"select": {"name": letter}}
            if aud:
                props["Audience"] = {"select": {"name": aud}}
            if sent is not None:
                props["Recipients"] = {"number": sent}
            if open_rate is not None:
                props["Open Rate"] = {"number": round(open_rate, 4)}
            if ctr is not None:
                props["CTR"] = {"number": round(ctr, 4)}
            if clicks is not None:
                props["Clicks"] = {"number": clicks}
            if date:
                props["Sent"] = {"date": {"start": date}}

            pid = existing.get(subj.strip())
            if pid:
                notion._call("PATCH", f"/pages/{pid}", {"properties": props})
                print(f"  ~ updated  [{test} {letter or ''}] {subj[:50]}")
            else:
                page = notion._call("POST", "/pages",
                                    {"parent": {"database_id": REPORTING_DB}, "properties": props})
                pid = page["id"]
                # preserve Anevo's native numbers verbatim (their click rate = CTOR)
                note = (f"Anevo native — Sent {sent}, Open {opens or '?'} "
                        f"({round((open_pct or 0)*100,1)}%), Click {clicks or '?'} "
                        f"({round((click_pct or 0)*100,1)}% click-to-open). "
                        f"CTR column = clicks/sent = {round((ctr or 0)*100,2)}%.")
                notion._call("PATCH", f"/blocks/{pid}/children", {"children": [{
                    "object": "block", "type": "callout",
                    "callout": {"icon": {"emoji": "📬"},
                                "rich_text": [{"type": "text", "text": {"content": note}}]}}]})
                print(f"  + added    [{test} {letter or ''}] {subj[:50]}")
                existing[subj.strip()] = pid

            if images:
                try:
                    import mockup
                    png = mockup.make_email_png(
                        headline=subj,
                        flow=[{"kind": "para", "text": f"Sent by Anevo to the {aud or 'Bluon'} segment "
                                                       f"({(r.get('Audience/Segment') or '').strip()})."}],
                        cta="Bluon for Business",
                        cta_url="https://www.bluon.com")
                    mockup.attach_file_to_property(pid, "Email Image", png, "anevo.png")
                    print("      · thumbnail rendered")
                except Exception as e:
                    print("      · thumbnail skipped:", e)

        # report (don't auto-crown — the winner is metric-dependent for Anevo)
        if multi:
            cmp = [(LETTERS[i], (_count_pct(m.get('Click Rate'))[0] or 0)) for i, m in enumerate(members)]
            print(f"  · {test}: clicks " + ", ".join(f"{l}={c}" for l, c in cmp)
                  + "  (winner left unset — Anevo's sheet judges click-to-open, not CTR)")


if __name__ == "__main__":
    run(images="--images" in sys.argv)
