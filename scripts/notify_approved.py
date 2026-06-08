"""When a draft is Approved, ping Kelsey so she can queue the send.

Polls the Email Calendar for rows where Approved is checked but 'Sent to Kelsey'
is not, posts a Slack message via SLACK_WEBHOOK_URL, then checks 'Sent to Kelsey'
so it never double-notifies. Safe no-op until SLACK_WEBHOOK_URL is set.

This is the "for now" handoff: a human still does the actual send.
"""
import os, json, urllib.request
import notion

WEBHOOK = os.environ.get("SLACK_WEBHOOK_URL", "").strip()


def _post_slack(text):
    if not WEBHOOK:
        print("[dry run] would Slack:", text)
        return True
    req = urllib.request.Request(WEBHOOK, data=json.dumps({"text": text}).encode(),
                                 method="POST", headers={"Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req, timeout=30)
        return True
    except Exception as e:
        print("slack post failed:", e)
        return False


def main():
    res = notion._call("POST", f"/databases/{notion.CALENDAR_DB_ID}/query", {"page_size": 100})
    notified = 0
    for r in res.get("results", []):
        pr = r.get("properties", {})
        approved = pr.get("Approved", {}).get("checkbox", False)
        sent = pr.get("Sent to Kelsey", {}).get("checkbox", False)
        if not approved or sent:
            continue
        name = "".join(x.get("plain_text", "") for x in pr.get("Email", {}).get("title", []))
        aud = (pr.get("Audience", {}).get("select") or {}).get("name", "")
        eng = (pr.get("Engagement", {}).get("select") or {}).get("name", "")
        chan = (pr.get("Channel", {}).get("select") or {}).get("name", "")
        date = (pr.get("Send Date", {}).get("date") or {}).get("start", "")
        url = r.get("url", "")
        msg = (f":white_check_mark: *Email approved — ready to send*\n"
               f"*{name}*  ({aud} / {eng})\n"
               f"Channel: *{chan}*  ·  Send date: *{date or 'TBD'}*\n{url}")
        if _post_slack(msg):
            notion._call("PATCH", f"/pages/{r['id']}",
                         {"properties": {"Sent to Kelsey": {"checkbox": True}}})
            notified += 1
            print("notified:", name)
    print(f"\nnotified={notified}" + ("" if WEBHOOK else "  (dry run — SLACK_WEBHOOK_URL not set)"))


if __name__ == "__main__":
    main()
