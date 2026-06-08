"""Print everything the generator needs in one read:
the Email Content Intelligence guide + the current Email Calendar rows.

Usage: python scripts/get_context.py
"""
import notion

print("=" * 70)
print("EMAIL CONTENT INTELLIGENCE GUIDE")
print("=" * 70)
print(notion.get_guide_text())

print()
print("=" * 70)
print("EXISTING EMAIL CALENDAR ROWS (avoid duplicating these)")
print("=" * 70)
rows = notion.get_calendar_rows()
if not rows:
    print("(none yet)")
for r in rows:
    flags = ("approved" if r["approved"] else "draft") + (", done" if r["done"] else "")
    print(f"- [{flags}] {r['audience']} / {r['engagement']} | {r['send_date']} | {r['name']}")
