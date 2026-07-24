"""A/B fan-out: expand ONE approved row into a row per variant.

Two test types:
  Subject test — Testing = "Subject Line", subjects listed in "Subject Variants"
    (one per line). Same body, different subjects. spawn() fans the single row
    into sibling rows tied by a shared Test Group and labelled Variant A/B, so
    each gets its own HubSpot draft + reporting line.
  Body test — Testing = "Header / Hook". NO fan-out: the ONE page holds both full
    bodies under '🅰 Variant A' / '🅱 Variant B' headings (each with text + its own
    mockup); to_hubspot.process builds the native HubSpot A/B straight from it.

Idempotent: the base becomes Variant A; siblings are only created once. Re-running
returns EVERY variant row id (including pre-existing siblings) — to_hubspot.process
relies on that to pair the native HubSpot A/B, so never skip-without-returning.

  python scripts/variants.py <base_page_id>
"""
import sys, re
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


def canonical_group(stem, audience):
    """Deterministic, readable Test Group so the group-by view stays consistent across
    runs: '<Stem> · <Audience>'. (A human-set Test Group still wins, for the rare case
    of two different tests on the same stem + audience.)"""
    return " · ".join(x for x in ((stem or "").strip(), (audience or "").strip()) if x) or "Test"


def _variants(pr, prop="Subject Variants"):
    """Alternate subjects (or body hooks) for a test — accepts one-per-line OR
    pipe-delimited ('A: … | B: …'), stripping any leading 'A:' / 'B.' / 'C)' variant
    label. (The pipe form used to silently parse as a single subject and the test
    never fanned.)"""
    rt = (pr.get(prop, {}) or {}).get("rich_text", [])
    text = "".join(x.get("plain_text", "") for x in rt)
    out = []
    for s in re.split(r"\s*\|\s*|\n", text):
        s = re.sub(r"^[A-Fa-f]\s*[:.)\-]\s*", "", s.strip()).strip()
        if s:
            out.append(s)
    return out


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
    # HubSpot A/B tests support exactly TWO subject versions — never 3+. Cap here so the
    # machine can't fan a 3-way that HubSpot can't run (the Wave-2 "duplicates" bug).
    if len(subjects) > 2:
        print(f"⚠️  {len(subjects)} subjects given, but a HubSpot A/B test is 2 versions max — "
              f"using the first two, dropping: {subjects[2:]}")
        subjects = subjects[:2]
    sel = lambda k: (pr.get(k, {}).get("select") or {}).get("name") or ""
    if len(subjects) < 2:
        if sel("Testing") == "Subject Line":   # MEANT to be a test — don't fail silently
            print("⚠️  SUBJECT TEST but <2 subjects parsed from 'Subject Variants' — NOT fanning out. "
                  "Put one subject per line (or 'A: … | B: …'):", base_id)
        else:
            print("not a multi-subject test (need 2+ in Subject Variants):", base_id)
        return [base_id]

    audience = sel("Audience")
    # Test Stem = the campaign/series name shown first in the title. Read it from the
    # property (don't re-split a title); derive a sensible one if unset.
    stem = "".join(x.get("plain_text", "") for x in (pr.get("Test Stem", {}).get("rich_text") or [])).strip()
    if not stem:
        old = "".join(x["plain_text"] for x in pr["Email"]["title"])
        stem = sel("Campaign") or old.split(" - ")[0].split(" · ")[0].strip() or "Email"
    # Test Group: a human-set one wins (disambiguates two tests on one audience);
    # otherwise a canonical, consistent '<Stem> · <Audience>' — so the group-by view
    # never fragments across runs (no more lowercase slugs / LLM-invented labels).
    named = "".join(x.get("plain_text", "") for x in (pr.get("Test Group", {}).get("rich_text") or []))
    group = named or canonical_group(stem, audience)
    diffs = [_differentiator(s, _common_prefix(subjects)) for s in subjects]
    send_date = (pr.get("Send Date", {}).get("date") or {}).get("start")

    # which subjects already have a row in this group? (idempotency). Map subject →
    # row id so a re-run RETURNS the existing sibling instead of dropping it — the
    # prepare-then-process double-spawn used to return [base] only, so process()
    # never paired the native A/B and B shipped as a second standalone email.
    existing = {}
    for r in notion._call("POST", f"/databases/{notion.CALENDAR_DB_ID}/query", {"page_size": 100})["results"]:
        p = r["properties"]
        if r["id"] != base_id and "".join(x.get("plain_text", "") for x in (p.get("Test Group", {}).get("rich_text") or [])) == group:
            subj_txt = "".join(x.get("plain_text", "") for x in (p.get("Subject", {}).get("rich_text") or []))
            existing[subj_txt] = r["id"]

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
            ids.append(existing[subj])   # already fanned — return it so the A/B still pairs
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
            # inherit the base's approval: if the base is Ready, the whole test is approved → siblings send too
            notion.READY_ID: {"checkbox": notion.ready_checked(pr)},
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


def route(base_id):
    """Fan a row by its declared test type. Anything the machine can't build fans
    to nothing — loudly. Body (Header/Hook) tests never fan rows: both versions
    live on the ONE page under 'Variant A' / 'Variant B' headings, and
    to_hubspot.process builds the native A/B from there BEFORE calling route —
    reaching this point means the page has no Variant B section."""
    pr = notion._call("GET", f"/pages/{base_id}")["properties"]
    testing = ((pr.get("Testing", {}) or {}).get("select") or {}).get("name") or ""
    if testing == "Header / Hook":
        print("⚠️  BODY TEST but the page has no '🅱 Variant B' section — drafting ONE plain email. "
              "Add the Variant A/B sections to the page for a real A/B:", base_id)
        return [base_id]
    if testing in ("Landing Page", "Feature", "Offer", "Vibe"):
        print(f"⚠️  Testing='{testing}' has no automated A/B build path — drafting ONE plain email. "
              "Build the variation manually in HubSpot if this is meant to be a test:", base_id)
    return spawn(base_id)


def prepare():
    """Pre-send sweep so marking a test's base 'Ready to Go' sends the WHOLE A/B test,
    not just Variant A: (1) fan any un-fanned, Ready subject/body test; (2) once a group's
    Variant A is Ready, mark every sibling Ready too. Acts ONLY on approved (Ready) tests;
    idempotent."""
    def q():
        return notion._call("POST", f"/databases/{notion.CALENDAR_DB_ID}/query", {"page_size": 100})["results"]
    rdy = notion.ready_checked
    snm = lambda p, k: ((p.get(k, {}) or {}).get("select") or {}).get("name", "")
    grp = lambda p: "".join(x.get("plain_text", "") for x in (p.get("Test Group", {}).get("rich_text") or []))
    # 1) fan un-fanned, Ready subject tests — but NOT one already pushed to HubSpot.
    # A row that already has a Hubspot Email link is a built A/B test (it represents the
    # whole test via its Subject Variants); re-fanning it would duplicate the HubSpot send.
    linked = lambda p: bool((p.get("Hubspot Email", {}) or {}).get("url"))
    for r in q():
        p = r["properties"]
        if snm(p, "Variant") or not rdy(p) or linked(p):
            continue
        if snm(p, "Testing") == "Subject Line" and len(_variants(p)) >= 2:
            print("  prepare: fanning ready subject test", r["id"]); spawn(r["id"])
        # body (Header/Hook) tests don't fan — one page holds both versions and
        # to_hubspot.process builds the native A/B from it directly
    # 2) ready-sync: if a group's Variant A is Ready, ready every sibling too
    groups = {}
    for r in q():
        p = r["properties"]
        if snm(p, "Testing") in ("Subject Line", "Header / Hook") and grp(p):
            groups.setdefault(grp(p), []).append((r["id"], p))
    for tg, members in groups.items():
        if any(rdy(p) for _, p in members if snm(p, "Variant") == "A"):
            for pid, p in members:
                if not rdy(p):
                    notion.set_ready(pid, True)
                    print(f"  prepare: readied sibling in '{tg}'")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--prepare":
        prepare()
    else:
        spawn(sys.argv[1].strip())
