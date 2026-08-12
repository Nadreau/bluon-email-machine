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


def _week_title():
    """The coming week's planning row. Matches the pre-created separator rows
    ('📋 Week of Aug 17') that Niko seeds ahead of time — the generator fills one
    of those in rather than creating a competing row."""
    today = datetime.date.today()
    monday = today + datetime.timedelta(days=(7 - today.weekday()) % 7 or 7)
    return monday, f"📋 Week of {monday.strftime('%b %-d')}"


def find_week_row(title):
    """The pre-seeded separator row for this week, if it exists."""
    res = notion._call("POST", f"/databases/{notion.CALENDAR_DB_ID}/query", {"page_size": 100})
    for r in res.get("results", []):
        nm = "".join(x.get("plain_text", "") for x in (r["properties"].get("Email", {}).get("title") or []))
        if nm == title:
            return r["id"]
    return None


def already_dropped(rows):
    """Ideas already added to this week's row? (the row itself may pre-exist empty)"""
    _, title = _week_title()
    pid = find_week_row(title)
    if not pid:
        return False
    try:
        for b in notion._call("GET", f"/blocks/{pid}/children?page_size=100")["results"]:
            txt = "".join(x.get("plain_text", "") for x in
                          (b.get(b["type"], {}).get("rich_text") or []))
            if txt.startswith(IDEA_PREFIX):
                return True
    except Exception:
        pass
    return False


def _voice_bank():
    """VOICE.md (the mined 2022-23 Coda archive) — the generator's style reference.
    Missing file degrades to empty, never crashes the Sunday drop."""
    try:
        p = os.path.join(os.path.dirname(__file__), "..", "VOICE.md")
        return open(p).read()[:6000]
    except Exception:
        return ""


def ideas_from_claude(rows):
    recent = "\n".join(f"- [{r['format']}] {r['name']} ({r['status']})"
                       for r in rows if not r["name"].startswith("📐"))
    today = datetime.date.today()
    voice = _voice_bank()
    prompt = f"""You write weekly retention comms ideas for Bluon, the HVAC app 100K+ technicians use (nameplate scan → {FEATURES}).

AUDIENCE: existing technicians (not prospects). Roughly half are unengaged — they have the app, they forgot it exists. Tone: funny, blue-collar, zero corporate polish. Think memes, dares, one-liners a tech would screenshot. Never condescending, never salesy. Identity/status framing works ("badass operators", "first-time fixes").

HOUSE VOICE (mined from Bluon's best-era sends — match the ENERGY, obey its warning about dead mechanics):
{voice}

DATE: {today} (consider HVAC seasonality — August = peak cooling chaos, techs are slammed).

RECENT SENDS/DRAFTS (do NOT repeat these angles):
{recent}

Produce EXACTLY 6 ideas: 2 Push notifications, 2 Texts, 2 Emails. Output ONLY a JSON array, no prose:
[{{"format": "Push|Text|Email", "title": "short internal name", "engagement": "Engaged|Unengaged|Both", "hook": "2-3 sentences: the actual angle/joke/payoff, concrete enough to draft from", "draft": "a first-pass of the actual copy in the house voice — Push: title line + body line; Text: the full message under 300 chars; Email: subject + a 4-6 line body"}}]

HARD RULES: Live Tech Support is a PAID standalone product — never call it free, never describe it as inside ServiceTitan or any FSM integration. No 2% cashback / points / contests / BluonPro signups (dead 2022 mechanics). Pushes: title fits ~40 chars, body ~120. Texts: personal, one idea, no images. No em-dashes or semicolons in draft copy."""
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


def create_week_row(ideas):
    """ONE dated row for the whole weekly drop (Niko, Aug 10: never create rows
    without a Send Date, and one weekly item — not six loose rows). The ideas
    live INSIDE the page; approved ones get their own dated row when drafted."""
    monday, title = _week_title()
    existing = find_week_row(title)
    def t(c, **a):
        o = {"type": "text", "text": {"content": c}}
        if a: o["annotations"] = a
        return o
    children = [{"object": "block", "type": "callout", "callout": {
        "rich_text": [t("This week's topic ideas. Keep the good ones (draft from a 📐 TEMPLATE row or ask Claude), ignore the rest. This page never sends anything.")],
        "icon": {"type": "emoji", "emoji": "💡"}, "color": "yellow_background"}}]
    for idea in ideas:
        fmt = idea.get("format") if idea.get("format") in ("Email", "Text", "Push") else "Email"
        eng = idea.get("engagement") or ""
        children.append({"object": "block", "type": "heading_3", "heading_3": {"rich_text": [
            t(f"{IDEA_PREFIX}{idea['title']}  ·  {fmt}" + (f" · {eng}" if eng else ""), bold=True)]}})
        if idea.get("hook"):
            children.append({"object": "block", "type": "paragraph", "paragraph": {
                "rich_text": [t(idea["hook"][:1900])]}})
        for ln in [l for l in (idea.get("draft") or "").split("\n") if l.strip()][:10]:
            children.append({"object": "block", "type": "quote", "quote": {
                "rich_text": [t(ln.strip()[:1900])]}})
    if existing:   # append into the pre-seeded separator row
        notion._call("PATCH", f"/blocks/{existing}/children", {"children": children})
        print(f"added {len(ideas)} ideas to the existing {title}")
        return
    notion._call("POST", "/pages", {"parent": {"database_id": notion.CALENDAR_DB_ID},
        "properties": {
            "Email": {"title": [{"type": "text", "text": {"content": title}}]},
            "Type": {"select": {"name": "📋 Week Plan"}},
            "Status": {"select": {"name": "Backlog"}},
            "Send Date": {"date": {"start": monday.isoformat()}},
            notion.READY_ID: {"checkbox": False}},
        "children": children})
    print(f"created {title} with {len(ideas)} ideas inside")


def main():
    rows = recent_rows()
    if already_dropped(rows) and "--force" not in sys.argv:
        print("this week's idea drop already exists — nothing to do")
        return
    ideas = ideas_from_claude(rows)
    create_week_row(ideas)


if __name__ == "__main__":
    main()
