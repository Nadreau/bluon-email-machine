"""Render + attach the HubSpot mockup image to any Email Calendar draft that
doesn't have one yet. Idempotent: rows that already have an image are skipped.

Runs as a dedicated step (in CI after the agent writes the text drafts, and
locally for backfill/debug). Decoupled from the generator so a render hiccup
never blocks the drafts themselves — just re-run this to fill them in.
"""
import notion, mockup


def _text(block):
    t = block.get("type")
    rt = block.get(t, {}).get("rich_text", [])
    return "".join(x.get("plain_text", "") for x in rt)


def parse_page(page_id):
    """Pull subject, body lines, and CTA back out of a draft page."""
    ch = notion._call("GET", f"/blocks/{page_id}/children?page_size=100")["results"]
    subject, cta, body, in_body = "", "Book a Demo", [], False
    fallback_id = None
    has_image = False
    for b in ch:
        t = b["type"]
        if t == "image":
            has_image = True
        txt = _text(b)
        if t == "callout" and txt.startswith("Suggested subject:"):
            subject = txt.split(":", 1)[1].strip()
        elif t == "callout" and "mockup" in txt.lower():
            fallback_id = b["id"]
        elif t == "heading_3" and txt.startswith("Body"):
            in_body = True
        elif t == "heading_3" and "Mockup" in txt:
            in_body = False
        elif t == "paragraph" and txt.startswith("CTA:"):
            in_body = False
            seg = txt[4:].split("·")[0].strip()
            if seg:
                cta = seg
        elif in_body and t == "bulleted_list_item":
            body.append("- " + txt)
        elif in_body and t == "paragraph" and txt:
            body.append(txt)
    return {"subject": subject, "cta": cta, "body": body,
            "fallback_id": fallback_id, "has_image": has_image}


def main():
    rows = notion.get_calendar_rows()
    done = skipped = failed = 0
    for r in rows:
        info = parse_page(r["id"])
        if info["has_image"]:
            skipped += 1
            continue
        if not info["subject"]:
            print("skip (no subject):", r["name"]); skipped += 1; continue
        fid = mockup.make_mockup_upload(headline=info["subject"],
                                        body_lines=info["body"], cta=info["cta"])
        if not fid:
            print("RENDER FAILED:", r["name"]); failed += 1; continue
        # remove the "pending" placeholder, then append the image
        if info["fallback_id"]:
            try:
                notion._call("PATCH", f"/blocks/{info['fallback_id']}", {"archived": True})
            except Exception:
                pass
        notion._call("PATCH", f"/blocks/{r['id']}/children",
                     {"children": [{"object": "block", "type": "image",
                                    "image": {"type": "file_upload",
                                              "file_upload": {"id": fid}}}]})
        print("mockup attached:", r["name"])
        done += 1
    print(f"\nattached={done} skipped={skipped} failed={failed}")


if __name__ == "__main__":
    main()
