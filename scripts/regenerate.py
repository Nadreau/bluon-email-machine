"""Regenerate the image mockup for a draft from its CURRENT edited state.

Reads the stylized blocks (subject/body/CTA Pete edited), strips (( )) styling
notes, uses the first pasted image as the hero, re-renders the mockup, and swaps
it in. The stylized blocks (the human-edited copy) are never overwritten — only
the rendered image is refreshed.

Modes:
  python regenerate.py <page_id>   # regenerate one page
  python regenerate.py --flagged   # all rows with 'Regen requested' checked (webhook path), then clear the flag
  python regenerate.py --fill      # all rows missing a mockup image (initial fill after weekly gen)
"""
import sys, datetime
import notion, mockup


PROBLEM_PREFIX = "⚠️ Mockup can't render"


def _clear_problem(page_id):
    try:
        for b in notion._call("GET", f"/blocks/{page_id}/children?page_size=100")["results"]:
            if b["type"] != "callout":
                continue
            tx = "".join(x.get("plain_text", "") for x in b["callout"].get("rich_text", []))
            if tx.startswith(PROBLEM_PREFIX):
                notion._call("PATCH", f"/blocks/{b['id']}", {"archived": True})
    except Exception:
        pass


def _mark_problem(page_id, why):
    """Put the reason a render failed ON the row, so the person who pressed the button
    can fix it themselves instead of messaging Niko."""
    _clear_problem(page_id)
    try:
        notion._call("PATCH", f"/blocks/{page_id}/children", {"children": [
            {"object": "block", "type": "callout", "callout": {
                "rich_text": [{"type": "text", "text": {"content": f"{PROBLEM_PREFIX} — {why}"}}],
                "icon": {"type": "emoji", "emoji": "⚠️"}, "color": "red_background"}}]})
    except Exception as e:
        print("  (problem note failed:", e, ")")


def regen_page(page_id, clear_flag=False):  # clear_flag kept for call-compat; no-op (button-triggered now)
    info = notion.parse_draft_page(page_id)
    # A draft with no headline block is VALID — plenty of real emails open straight into
    # the copy. Render it exactly as written rather than skipping or inventing a headline.
    if not info["subject"]:
        print("  (no headline block — rendering the body as written)")
    _clear_problem(page_id)
    note = ""
    if info["style_notes"]:
        note = "  [styling notes: " + " | ".join(info["style_notes"])[:120] + "]"
    # a body-A/B page renders ONE mockup PER VERSION (labeled); a plain page renders one
    versions = [("", info)]
    if info.get("body_lines_b"):
        versions = [("🅰 Variant A", info), ("🅱 Variant B", notion.variant_b_info(info))]
    children = []
    top_hero = flow = None   # version A's layout, kept for the Email Image snapshot below
    for i, (label, vi) in enumerate(versions):
        vhero, vflow = notion.email_layout(vi)   # image at top (default) or moved inline
        if i == 0:
            top_hero, flow = vhero, vflow        # version A
        fid = mockup.make_mockup_upload(headline=vi["subject"], flow=vflow,
                                        cta=vi["cta"], top_hero=vhero)
        if not fid:
            # Tanner's "0 way to troubleshoot": a genuine render failure used to exist only
            # in a log he can't see. Say it on the row instead.
            _mark_problem(page_id, "the image renderer failed on this draft. Niko has the "
                                   "logs — usually a broken/oversized pasted image.")
            print("RENDER FAILED:", page_id, label)
            return False
        if label:
            children.append({"object": "block", "type": "paragraph", "paragraph": {
                "rich_text": [{"type": "text", "text": {"content": label},
                               "annotations": {"bold": True}}]}})
        children.append({"object": "block", "type": "image",
                         "image": {"type": "file_upload", "file_upload": {"id": fid}}})
    # remove the old renders/placeholders/labels, append the fresh set under the Mockup heading
    for bid in info["mockup_old_ids"]:
        try:
            notion._call("PATCH", f"/blocks/{bid}", {"archived": True})
        except Exception:
            pass
    notion._call("PATCH", f"/blocks/{page_id}/children", {"children": children})
    # Also refresh the "Email Image" file property (the report-time snapshot of how the
    # email sends) so it never goes STALE after a copy edit. It was previously written
    # once at HubSpot-push time only — which is why some sent rows had no image and one
    # showed a deleted-but-baked-in double greeting. This is HubSpot-independent (needs
    # only NOTION_TOKEN + Chrome), so the daily regen self-heals every row's snapshot.
    try:
        png = mockup.make_email_png(headline=info["subject"], flow=flow,
                                    cta=info["cta"], top_hero=top_hero)
        mockup.attach_file_to_property(page_id, "Email Image", png, "email.png")
    except Exception as e:
        print("  email image refresh failed:", e)
    # The Notion "Regenerate Mockup" BUTTON fires the webhook directly with the
    # page id — there is no checkbox flag to clear (the old 'Regen requested'
    # property is gone). Clearing it 400'd and, because _call raises SystemExit,
    # crashed the whole run AFTER the image had already swapped in → false
    # "mockup failed" alerts. So we simply don't.
    # Stamp WHEN this render happened, visible right on the row. Tanner had "0 way to
    # troubleshoot" a button press: now a press that worked moves this timestamp, and one
    # that didn't leaves it stale — self-service diagnosis, no GitHub access needed.
    try:
        notion._call("PATCH", f"/pages/{page_id}",
                     {"properties": {"Mockup updated": {"date": {"start":
                        datetime.datetime.now(datetime.timezone.utc).isoformat()}}}})
    except Exception as e:
        print("  (mockup timestamp skipped:", e, ")")
    print(("regenerated" if not info["hero_url"] else "regenerated (w/ pasted hero)") +
          ":", page_id, ("· " + str(len(info["style_notes"])) + " styling notes" if info["style_notes"] else ""))
    return True


def _newest_mockup_time(page_id):
    """When the current mockup was rendered (newest image under the Mockup heading)."""
    newest, in_mock = None, False
    for b in notion._call("GET", f"/blocks/{page_id}/children?page_size=100")["results"]:
        t = b["type"]
        txt = "".join(x.get("plain_text", "") for x in (b.get(t, {}).get("rich_text") or []))
        if t == "heading_3" and notion.MOCKUP_HEADING in txt:
            in_mock = True
            continue
        if in_mock and t == "image":
            ts = b.get("created_time") or b.get("last_edited_time")
            if ts and (newest is None or ts > newest):
                newest = ts
    return newest


def stale_rows(buffer_s=180):
    """Drafts whose copy changed AFTER the mockup was rendered. This is the safety net
    that makes the Notion button non-critical: if the button's webhook is dropped (relay
    down, laptop asleep), the edit is still picked up on the next scheduled sweep.
    buffer_s keeps a render from re-triggering itself — regenerating edits the page."""
    out = []
    res = notion._call("POST", f"/databases/{notion.CALENDAR_DB_ID}/query", {"page_size": 100})
    for r in res.get("results", []):
        if r.get("archived"):
            continue
        pr = r["properties"]
        name = "".join(x.get("plain_text", "") for x in (pr.get("Email", {}).get("title") or []))
        edited = r.get("last_edited_time")
        made = ((pr.get("Mockup updated", {}) or {}).get("date") or {}).get("start")
        if not made:
            made = _newest_mockup_time(r["id"])   # first pass only, before the stamp exists
        if not edited or not made:
            continue          # never rendered yet -> that's --fill's job, not ours
        de = datetime.datetime.fromisoformat(edited.replace("Z", "+00:00"))
        dm = datetime.datetime.fromisoformat(made.replace("Z", "+00:00"))
        if (de - dm).total_seconds() > buffer_s:
            out.append((r["id"], name, int((de - dm).total_seconds() // 60)))
    return out


def main():
    arg = sys.argv[1] if len(sys.argv) > 1 else "--stale"
    if arg == "--stale":
        rows = stale_rows()
        print(f"{len(rows)} draft(s) with copy newer than their mockup")
        for pid, name, mins in rows:
            print(f"  ↻ {name[:52]} (edited {mins} min after last render)")
            regen_page(pid)
    elif arg == "--flagged":
        # The webhook means a box was JUST checked; Notion's query index can lag
        # a few seconds behind that write, so retry a couple times before giving up.
        import time
        rows = []
        for attempt in range(4):
            rows = [r for r in notion.get_calendar_rows() if r["regen"]]
            if rows:
                break
            if attempt < 3:
                print(f"none flagged yet (attempt {attempt+1}) — waiting for Notion to catch up…")
                time.sleep(12)
        print(f"{len(rows)} row(s) flagged for regenerate")
        for r in rows:
            regen_page(r["id"], clear_flag=True)
    elif arg == "--fill":
        n = 0
        for r in notion.get_calendar_rows():
            blocks = notion._call("GET", f"/blocks/{r['id']}/children?page_size=100")["results"]
            if any(b["type"] == "image" for b in blocks):
                continue   # already has a mockup
            if regen_page(r["id"]):
                n += 1
        print(f"filled {n} mockups")
    elif arg == "--fill-images":
        # self-heal the "Email Image" snapshot for any row missing it (e.g. emails
        # cloned/sent outside the machine). regen_page also refreshes it, so a row that
        # already has one is left to the regen path. Runs daily in the rolling cron.
        n = 0
        for r in notion.get_calendar_rows():
            pr = notion._call("GET", f"/pages/{r['id']}")["properties"]
            if (pr.get("Email Image", {}) or {}).get("files"):
                continue
            if regen_page(r["id"]):
                n += 1
        print(f"backfilled {n} Email Image(s)")
    else:
        # a specific page id from the webhook — validate it's a Notion UUID
        import re
        pid = arg.strip().replace("-", "")
        if not re.fullmatch(r"[0-9a-fA-F]{32}", pid):
            print("ignoring non-UUID page id:", arg[:40]); return
        # A button press names ONE page. If it can't render, exit non-zero so the Actions
        # run goes RED instead of reporting success on a total no-op — a green run on a
        # skipped render is exactly what kept "regenerate is broken" invisible. (--stale
        # deliberately does NOT do this: one bad draft shouldn't fail a whole sweep.)
        if not regen_page(arg.strip(), clear_flag=True):
            sys.exit(1)


if __name__ == "__main__":
    main()
