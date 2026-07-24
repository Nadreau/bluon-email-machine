"""Make a failed HubSpot push VISIBLE where the team lives — on the Notion row.

Run by to-hubspot.yml's `if: failure()` step. Every past miss (disabled workflow,
renamed trigger column, image bugs) failed where only GitHub could see it, and a
teammate discovered the gap hours later. Now a failed push:
  1. flips the row's Status to "⚠️ Push Failed" (red chip in the calendar), and
  2. drops a red callout at the top of the page with the last error lines.
The next SUCCESSFUL push clears both (to_hubspot.clear_failure_marks).

  python push_failed.py <page_id> [<logfile>]
"""
import sys
import notion

FAIL_STATUS = "⚠️ Push Failed"
FAIL_PREFIX = "⚠️ Push to HubSpot FAILED"


def mark(page_id, log_tail=""):
    msg = (f"{FAIL_PREFIX} — the automation hit an error and Niko has the logs. "
           f"The draft was NOT created/updated in HubSpot.")
    if log_tail:
        msg += "  Last error: " + log_tail
    blk = {"object": "block", "type": "callout", "callout": {
        "rich_text": [{"type": "text", "text": {"content": msg[:1900]}}],
        "icon": {"type": "emoji", "emoji": "⚠️"}, "color": "red_background"}}
    body = {"children": [blk]}
    first = notion._call("GET", f"/blocks/{page_id}/children?page_size=1")["results"]
    if first:
        body["after"] = first[0]["id"]       # right under the brand callout
    notion._call("PATCH", f"/blocks/{page_id}/children", body)
    notion._call("PATCH", f"/pages/{page_id}",
                 {"properties": {"Status": {"select": {"name": FAIL_STATUS}}}})
    print(f"failure marked on {page_id}")


if __name__ == "__main__":
    pid = sys.argv[1].strip() if len(sys.argv) > 1 and sys.argv[1].strip() else ""
    if not pid:
        print("no page id (sweep-mode failure) — nothing row-level to mark")
        sys.exit(0)
    tail = ""
    if len(sys.argv) > 2:
        try:
            lines = [l.strip() for l in open(sys.argv[2]).read().splitlines() if l.strip()]
            tail = " | ".join(lines[-4:])[-500:]
        except Exception:
            pass
    mark(pid, tail)
