"""Subject-line A/B: expand ONE approved row into a row per subject variant.

A subject test = same email, different subjects. The reviewer writes one email,
sets Testing = "Subject Line", and lists the subjects in the "Subject Variants"
property (one per line). spawn() turns that single row into N sibling rows — same
body / CTA / hero, each a different subject — tied by a shared Test Group and
labelled Variant A/B/C, so each gets its own HubSpot draft + reporting line.

Idempotent: the base becomes Variant A; siblings are only created once (re-running
skips subjects that already have a row in the group).

  python scripts/variants.py <base_page_id>
"""
import sys
import notion, mockup

CARRY = ["Audience", "Engagement", "Channel", "Feature", "Type", "Campaign",
         "Status", "Vibe", "Landing Page"]


def _common_prefix(strs):
    if not strs:
        return ""
    p = strs[0]
    for s in strs[1:]:
        while p and not s.startswith(p):
            p = p[:-1]
    return p


def _differentiator(subj, prefix, limit=45):
    """The part of a subject that DIFFERS from its siblings — for a self-describing
    row title. (The FULL subject still lives in the Subject property + reporting.)"""
    d = subj[len(prefix):] if prefix and subj.startswith(prefix) else subj
    d = d.strip(" -–—:!?,.\t").strip() or subj.strip()
    return (d[:limit].rstrip() + "…") if len(d) > limit else d


def _title(stem, audience, letter, diff):
    """Self-describing variant title: '<Stem> - <Audience> - <Letter>  "<diff>"'.
    Built only from structured fields (never by re-splitting an existing title), so
    it's idempotent on re-runs. NO winner/trophy is ever written here — that's the
    AB Tag formula's job."""
    head = " - ".join(x for x in (stem, audience, letter) if x)
    return f'{head}  "{diff}"'[:200]


def _variants(pr):
    rt = (pr.get("Subject Variants", {}) or {}).get("rich_text", [])
    text = "".join(x.get("plain_text", "") for x in rt)
    return [s.strip() for s in text.splitlines() if s.strip()]


def _carry_props(pr):
    out = {}
    for k in CARRY:
        v = pr.get(k, {})
        if "select" in v and v.get("select"):
            out[k] = {"select": {"name": v["select"]["name"]}}
        elif v.get("url"):
            out[k] = {"url": v["url"]}
    return out


def spawn(base_id):
    info = notion.parse_draft_page(base_id)
    pr = notion._call("GET", f"/pages/{base_id}")["properties"]
    subjects = _variants(pr)
    if len(subjects) < 2:
        print("not a multi-subject test (need 2+ in Subject Variants):", base_id)
        return [base_id]

    sel = lambda k: (pr.get(k, {}).get("select") or {}).get("name") or ""
    # use the row's named Test Group if set (so two tests in the same audience/
    # engagement — e.g. the two winbacks — don't collide on an auto-derived name)
    named = "".join(x.get("plain_text", "") for x in (pr.get("Test Group", {}).get("rich_text") or []))
    group = named or f"{sel('Audience')}-{sel('Engagement')}-subj".lower().replace(" ", "")
    audience = sel("Audience")
    # Test Stem = the campaign/series name shown first in the title. Read it from the
    # property (don't re-split a title); derive + persist a sensible one if unset.
    stem = "".join(x.get("plain_text", "") for x in (pr.get("Test Stem", {}).get("rich_text") or [])).strip()
    if not stem:
        old = "".join(x["plain_text"] for x in pr["Email"]["title"])
        stem = sel("Campaign") or old.split(" - ")[0].split(" · ")[0].strip() or "Email"
    diffs = [_differentiator(s, _common_prefix(subjects)) for s in subjects]
    send_date = (pr.get("Send Date", {}).get("date") or {}).get("start")

    # which subjects already have a row in this group? (idempotency)
    existing = set()
    for r in notion._call("POST", f"/databases/{notion.CALENDAR_DB_ID}/query", {"page_size": 100})["results"]:
        p = r["properties"]
        if "".join(x.get("plain_text", "") for x in (p.get("Test Group", {}).get("rich_text") or [])) == group:
            existing.add("".join(x.get("plain_text", "") for x in (p.get("Subject", {}).get("rich_text") or [])))

    # base row -> Variant A (first subject)
    notion._call("PATCH", f"/pages/{base_id}", {"properties": {
        "Testing": {"select": {"name": "Subject Line"}},
        "Variant": {"select": {"name": "A"}},
        "Test Group": {"rich_text": [{"type": "text", "text": {"content": group}}]},
        "Test Stem": {"rich_text": [{"type": "text", "text": {"content": stem[:200]}}]},
        "Subject": {"rich_text": [{"type": "text", "text": {"content": subjects[0][:200]}}]},
        "Email": {"title": [{"type": "text", "text": {"content": _title(stem, audience, "A", diffs[0])}}]},
    }})
    # keep the base's on-page subject heading in sync with Variant A
    st = notion.parse_structure(base_id)
    if st.get("subject_id"):
        notion.update_block_text(st["subject_id"], "heading_2", [{"type": "text", "text": {"content": subjects[0]}}])

    ids = [base_id]
    for i, subj in enumerate(subjects[1:], start=1):
        if subj in existing:
            continue
        letter = "ABCDEF"[i]
        props = _carry_props(pr)
        props.update({
            "Email": {"title": [{"type": "text", "text": {"content": _title(stem, audience, letter, diffs[i])}}]},
            "Subject": {"rich_text": [{"type": "text", "text": {"content": subj[:200]}}]},
            "Testing": {"select": {"name": "Subject Line"}},
            "Variant": {"select": {"name": letter}},
            "Test Group": {"rich_text": [{"type": "text", "text": {"content": group}}]},
            "Test Stem": {"rich_text": [{"type": "text", "text": {"content": stem[:200]}}]},
            "Ready to Go": {"checkbox": False},
        })
        if send_date:
            props["Send Date"] = {"date": {"start": send_date}}
        page = notion._call("POST", "/pages", {
            "parent": {"database_id": notion.CALENDAR_DB_ID}, "properties": props,
            "children": notion.styled_email_blocks(subject=subj, preview="",
                        body_lines=info["body_lines"], cta=info["cta"])})
        nid = page["id"]
        # render the mockup + email image for the new variant (same image placement)
        try:
            top_hero, flow = notion.email_layout(info)
            fid = mockup.make_mockup_upload(headline=subj, flow=flow, cta=info["cta"], top_hero=top_hero)
            if fid:
                notion._call("PATCH", f"/blocks/{nid}/children", {"children": [{"object": "block",
                    "type": "image", "image": {"type": "file_upload", "file_upload": {"id": fid}}}]})
            png = mockup.make_email_png(headline=subj, flow=flow, cta=info["cta"], top_hero=top_hero)
            mockup.attach_file_to_property(nid, "Email Image", png, "email.png")
        except Exception as e:
            print("  mockup failed for", letter, e)
        ids.append(nid)
        print(f"  + Variant {letter}: {subj}")
    print(f"subject test '{group}': {len(ids)} variants")
    return ids


if __name__ == "__main__":
    spawn(sys.argv[1].strip())
