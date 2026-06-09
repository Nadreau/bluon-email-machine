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


def regen_page(page_id, clear_flag=False):
    info = notion.parse_draft_page(page_id)
    if not info["subject"]:
        print("skip (no subject):", page_id)
        return False
    note = ""
    if info["style_notes"]:
        note = "  [styling notes: " + " | ".join(info["style_notes"])[:120] + "]"
    fid = mockup.make_mockup_upload(headline=info["subject"], body_lines=info["body_lines"],
                                    cta=info["cta"], hero_url=info["hero_url"] or None)
    if not fid:
        print("RENDER FAILED:", page_id)
        return False
    # remove the old render/placeholder, append the fresh image under the Mockup heading
    for bid in info["mockup_old_ids"]:
        try:
            notion._call("PATCH", f"/blocks/{bid}", {"archived": True})
        except Exception:
            pass
    notion._call("PATCH", f"/blocks/{page_id}/children",
                 {"children": [{"object": "block", "type": "image",
                                "image": {"type": "file_upload", "file_upload": {"id": fid}}}]})
    if clear_flag:
        try:
            notion.set_checkbox(page_id, "Regen requested", False)
        except Exception:
            pass
    print(("regenerated" if not info["hero_url"] else "regenerated (w/ pasted hero)") +
          ":", page_id, ("· " + str(len(info["style_notes"])) + " styling notes" if info["style_notes"] else ""))
    return True


def main():
    arg = sys.argv[1] if len(sys.argv) > 1 else "--flagged"
    if arg == "--flagged":
        rows = [r for r in notion.get_calendar_rows() if r["regen"]]
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
    else:
        regen_page(arg, clear_flag=True)


if __name__ == "__main__":
    main()
