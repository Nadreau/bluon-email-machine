"""Sunday topic generator — the comms-machine idea drop.

Every Sunday evening this drops ~6 topic ideas into the calendar as Status=Idea
rows (💡-prefixed) covering the weekly cadence Tanner set on the Aug 7 call:
2 pushes + 1-2 texts (+ optional email) to existing techs, humor-forward like
the old Coda-era content. Niko reviews Monday morning, keeps the good ones
(drafting happens from there), archives the rest.

Ideas come from `claude -p` (headless, CLAUDE_CODE_OAUTH_TOKEN — same auth as
the sales coach) fed the recent calendar so it never repeats a fresh angle.
If Claude is unavailable the run falls back to a rotating built-in bank so the
Sunday drop never silently skips.

Idempotent: if this week's 💡 rows already exist, the run no-ops.
"""
import os, json, re, subprocess, sys, datetime
import notion

TARGET_MIX = [("Push", 2), ("Text", 2), ("Email", 1)]
IDEA_PREFIX = "💡 "

FEATURES = ("nameplate scan (right part in seconds), instant manuals/wiring diagrams, "
            "MasterMechanic AI troubleshooting, BluonSearch parts lookup, Live Tech "
            "Support calls (standalone product), distributor parts ordering")

# Fallback bank — used only when claude -p fails. Rotates by ISO week so
# consecutive fallback Sundays don't drop identical ideas.
FALLBACK = [
    {"format": "Push", "title": "Second-trip season", "engagement": "Unengaged",
     "hook": "It's 95° and the wrong part is a 40-minute drive away. Scan the nameplate first. (Humor angle: 'Your truck's AC works. Use it less.')"},
    {"format": "Push", "title": "Manual in 10 seconds flat", "engagement": "Both",
     "hook": "Timer challenge framing: nameplate photo → full manual before your coffee's cold. Dare them to beat 10 seconds."},
    {"format": "Text", "title": "The one-tool text", "engagement": "Unengaged",
     "hook": "Plain, personal text: 'You've got Bluon on your phone. One scan on your next call and you'll get why 100K+ techs use it. That's the whole pitch.'"},
    {"format": "Text", "title": "Callback killer", "engagement": "Engaged",
     "hook": "Fewer callbacks = weekends stay weekends. Ask what they'd do with a Saturday back."},
    {"format": "Email", "title": "5 things techs miss in the app", "engagement": "Both",
     "hook": "Quick-hits listicle with GIFs — the 5 features power users lean on that casual users never find."},
    {"format": "Push", "title": "Friday flex", "engagement": "Engaged",
     "hook": "End-of-week stat push: 'Techs on Bluon dodged ~X second trips this week. You were one of them.'"},
    {"format": "Text", "title": "Old-school meme energy", "engagement": "Unengaged",
     "hook": "Coda-era style: winter-without-Bluon / summer-with-Bluon meme framing, one line, one link."},
    {"format": "Push", "title": "Monday morning save", "engagement": "Both",
     "hook": "First call of the week is always the weird one. Bluon knows that unit. Scan it."},
]


def recent_rows(limit=30):
    out = []
    res = notion._call("POST", f"/databases/{notion.CALENDAR_DB_ID}/query", {
        "page_size": limit, "sorts": [{"timestamp": "created_time", "direction": "descending"}]})
    for r in res.get("results", []):
        pr = r["properties"]
        name = "".join(x.get("plain_text", "") for x in (pr.get("Email", {}).get("title") or []))
        status = ((pr.get("Status", {}) or {}).get("select") or {}).get("name") or ""
        out.append({"name": name, "format": notion.format_of(pr), "status": status,
                    "created": r.get("created_time", "")[:10]})
    return out


def already_dropped(rows):
    """This drop already happened? (💡 rows created since yesterday — catches a
    double-fired cron without letting a manual mid-week run block Sunday's)"""
    cutoff = (datetime.date.today() - datetime.timedelta(days=1)).isoformat()
    fresh = [r for r in rows if r["name"].startswith(IDEA_PREFIX) and r["created"] >= cutoff]
    return len(fresh) >= 3


def ideas_from_claude(rows):
    recent = "\n".join(f"- [{r['format']}] {r['name']} ({r['status']})"
                       for r in rows if not r["name"].startswith("📐"))
    today = datetime.date.today()
    prompt = f"""You write weekly retention comms ideas for Bluon, the HVAC app 100K+ technicians use (nameplate scan → {FEATURES}).

AUDIENCE: existing technicians (not prospects). Roughly half are unengaged — they have the app, they forgot it exists. Tone: funny, blue-collar, zero corporate polish. Think memes, dares, one-liners a tech would screenshot. Never condescending, never salesy. Identity/status framing works ("badass operators", "first-time fixes").

DATE: {today} (consider HVAC seasonality — August = peak cooling chaos, techs are slammed).

RECENT SENDS/DRAFTS (do NOT repeat these angles):
{recent}

Produce EXACTLY 6 ideas: 2 Push notifications, 2 Texts, 2 Emails. Output ONLY a JSON array, no prose:
[{{"format": "Push|Text|Email", "title": "short internal name", "engagement": "Engaged|Unengaged|Both", "hook": "2-3 sentences: the actual angle/joke/payoff, concrete enough to draft from"}}]

HARD RULES: Live Tech Support is a standalone product — never describe it as inside ServiceTitan or any FSM integration. Pushes: the eventual title must fit ~40 chars. Texts: personal, one idea, no images."""
    try:
        r = subprocess.run(["claude", "-p", prompt], capture_output=True, text=True, timeout=300)
        m = re.search(r"\[.*\]", r.stdout, re.S)
        ideas = json.loads(m.group(0))
        assert isinstance(ideas, list) and len(ideas) >= 4
        return ideas[:6]
    except Exception as e:
        print("claude idea generation unavailable, using fallback bank:", e)
        wk = datetime.date.today().isocalendar()[1]
        rot = FALLBACK[wk % len(FALLBACK):] + FALLBACK[:wk % len(FALLBACK)]
        picked, counts = [], {}
        for i in rot:
            cap = dict(TARGET_MIX).get(i["format"], 0)
            if counts.get(i["format"], 0) < cap:
                picked.append(i); counts[i["format"]] = counts.get(i["format"], 0) + 1
        return picked


def create_idea_row(idea):
    fmt = idea.get("format") if idea.get("format") in ("Email", "Text", "Push") else "Email"
    eng = idea.get("engagement") or ""
    tmpl = {"Email": "📐 TEMPLATE — EMAIL", "Text": "📐 TEMPLATE — TEXT", "Push": "📐 TEMPLATE — PUSH"}[fmt]
    props = {
        "Email": {"title": [{"type": "text", "text": {"content": (IDEA_PREFIX + idea["title"])[:200]}}]},
        "Status": {"select": {"name": "Idea"}},
        "Format": {"select": {"name": fmt}},
        "Hook": {"rich_text": [{"type": "text", "text": {"content": idea.get("hook", "")[:1900]}}]},
        notion.READY_ID: {"checkbox": False},
    }
    if eng in ("Engaged", "Unengaged"):
        props["Engagement"] = {"multi_select": [{"name": eng}]}
    elif eng == "Both":
        props["Engagement"] = {"multi_select": [{"name": "Engaged"}, {"name": "Unengaged"}]}
    children = [
        {"object": "block", "type": "callout", "callout": {
            "rich_text": [{"type": "text", "text": {"content":
                f"IDEA ({fmt}) — keep it? Draft the copy on this page ({tmpl} shows the shape) or ask Claude to draft it. Not this week's vibe? Archive the row."}}],
            "icon": {"type": "emoji", "emoji": "💡"}, "color": "yellow_background"}},
        {"object": "block", "type": "paragraph", "paragraph": {
            "rich_text": [{"type": "text", "text": {"content": idea.get("hook", "")}}]}},
    ]
    notion._call("POST", "/pages", {"parent": {"database_id": notion.CALENDAR_DB_ID},
                                    "properties": props, "children": children})
    print(f"  💡 [{fmt}] {idea['title']}")


def main():
    rows = recent_rows()
    if already_dropped(rows) and "--force" not in sys.argv:
        print("this week's idea drop already exists — nothing to do")
        return
    ideas = ideas_from_claude(rows)
    print(f"dropping {len(ideas)} ideas into the calendar")
    for idea in ideas:
        try:
            create_idea_row(idea)
        except Exception as e:
            print("  row failed:", e)


if __name__ == "__main__":
    main()
