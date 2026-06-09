"""Print one draft's current email + the edits/notes left on it, for the AI
regen step to read before deciding the new copy.

  python page_state.py <PAGE_ID>
"""
import sys
import notion

pid = sys.argv[1].strip()
info = notion.parse_draft_page(pid)
print("PAGE_ID:", pid)
print("SUBJECT:", info["subject"])
print("CTA:", info["cta"])
print("HERO IMAGE PASTED:", "yes" if info["hero_url"] else "no")
print("\nBODY (current copy, as edited):")
for ln in info["body_lines"]:
    print("  " + ln)
print("\nINSTRUCTION NOTES to act on (from (( )) and the notes section):")
if info["style_notes"]:
    for n in info["style_notes"]:
        print("  - " + n)
else:
    print("  (none)")
