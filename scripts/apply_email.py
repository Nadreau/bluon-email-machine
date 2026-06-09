"""Apply an AI-updated version of a draft's email in place, then re-render.

Updates the headline, body, and CTA of the editable stylized email (preserving
the page structure + any pasted hero), clears the instruction notes that were
just actioned, and regenerates the mockup. Called by the AI regen step after it
reads the current email + notes and decides the new copy.

  python apply_email.py <PAGE_ID> --subject "..." --cta "..." \
      --body $'Hook line.\n\n- bullet one\n- bullet two\n\nProof line.'
"""
import argparse
import notion
import regenerate


def main():
    p = argparse.ArgumentParser()
    p.add_argument("page_id")
    p.add_argument("--subject", default=None)
    p.add_argument("--cta", default=None)
    p.add_argument("--body", default=None)
    p.add_argument("--keep-notes", action="store_true")
    a = p.parse_args()
    pid = a.page_id.strip()

    s = notion.parse_structure(pid)

    if a.subject and s["subject_id"]:
        notion.update_block_text(s["subject_id"], "heading_2",
                                 [notion._t(a.subject, bold=True, color=notion.BLUE)])
    if a.cta and s["cta_id"]:
        notion.update_block_text(s["cta_id"], "callout",
                                 [notion._t("📅  " + a.cta + "  →", bold=True, color=notion.BLUE)])

    if a.body is not None:
        # build fresh body blocks (blue bullets, like the rest of the email)
        new_blocks = []
        for ln in a.body.split("\n"):
            ln = ln.strip()
            if not ln:
                continue
            if ln[:1] in ("-", "•", "*"):
                new_blocks.append({"object": "block", "type": "bulleted_list_item",
                    "bulleted_list_item": {"rich_text": [notion._t(ln.lstrip("-•* ").strip(), color=notion.BLUE)]}})
            else:
                new_blocks.append({"object": "block", "type": "paragraph",
                    "paragraph": {"rich_text": [notion._t(ln)]}})
        # archive the old body, insert the new body after the hero anchor
        for bid in s["body_ids"]:
            try:
                notion._call("PATCH", f"/blocks/{bid}", {"archived": True})
            except Exception:
                pass
        if new_blocks and s["hero_anchor_id"]:
            notion.insert_after(pid, s["hero_anchor_id"], new_blocks)

    # clear the instruction notes we just actioned (so they don't re-apply)
    if not a.keep_notes:
        for bid in s["note_ids"]:
            try:
                notion._call("PATCH", f"/blocks/{bid}", {"archived": True})
            except Exception:
                pass

    # re-render the mockup from the now-updated email
    regenerate.regen_page(pid, clear_flag=True)
    print("applied + re-rendered:", pid)


if __name__ == "__main__":
    main()
