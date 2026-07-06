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
import sys
import notion, mockup


def regen_page(page_id, clear_flag=False):  # clear_flag kept for call-compat; no-op (button-triggered now)
    info = notion.parse_draft_page(page_id)
    if not info["subject"]:
        print("skip (no subject):", page_id)
        return False
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
    print(("regenerated" if not info["hero_url"] else "regenerated (w/ pasted hero)") +
          ":", page_id, ("· " + str(len(info["style_notes"])) + " styling notes" if info["style_notes"] else ""))
    return True


def main():
    arg = sys.argv[1] if len(sys.argv) > 1 else "--flagged"
    if arg == "--flagged":
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
        regen_page(arg.strip(), clear_flag=True)


if __name__ == "__main__":
    main()
