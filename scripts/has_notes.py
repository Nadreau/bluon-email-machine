"""Exit 0 if a draft has instruction notes to act on (so the AI step runs),
else exit 1 (so the fast deterministic re-render runs instead)."""
import sys
import notion

info = notion.parse_draft_page(sys.argv[1].strip())
sys.exit(0 if info["style_notes"] else 1)
