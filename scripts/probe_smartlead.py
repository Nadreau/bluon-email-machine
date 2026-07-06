"""TEMP probe: dump real Smartlead response shapes so we can restructure the Anevo
report the way Tanner asked (active-campaign segmentation + per-campaign A/B tests).
Read-only. Delete once anevo_report.py v2 ships.

  python scripts/probe_smartlead.py
"""
import os, json, urllib.request, time

KEY = os.environ["SMARTLEAD_API_KEY"].strip()
BASE = "https://server.smartlead.ai/api/v1"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"


def sl(path):
    sep = "&" if "?" in path else "?"
    req = urllib.request.Request(f"{BASE}{path}{sep}api_key={KEY}", headers={"User-Agent": UA})
    for _ in range(3):
        try:
            with urllib.request.urlopen(req, timeout=45) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code == 429:
                time.sleep(2); continue
            print(f"  !! {path} -> HTTP {e.code}: {e.read().decode()[:300]}")
            return None
    return None


def shrink(o, depth=0):
    """Truncate long strings (email bodies) so the log stays readable."""
    if isinstance(o, dict):
        return {k: shrink(v, depth + 1) for k, v in o.items()}
    if isinstance(o, list):
        out = [shrink(v, depth + 1) for v in o[:5]]
        if len(o) > 5:
            out.append(f"... +{len(o) - 5} more")
        return out
    if isinstance(o, str) and len(o) > 160:
        return o[:160] + f"...<{len(o)} chars>"
    return o


def dump(label, obj):
    print(f"\n===== {label} =====")
    print(json.dumps(shrink(obj), indent=1, default=str)[:6000])


camps = sl("/campaigns")
camps = camps if isinstance(camps, list) else (camps or {}).get("data", [])
real = [c for c in camps if "subsequence" not in str(c.get("name", "")).lower()]
print(f"{len(real)} campaigns")
for c in real:
    print(f"  {c.get('id')}  {c.get('status'):<10}  {str(c.get('name'))[:70]}")

# probe an ACTIVE campaign if one exists, else the most recent
active = [c for c in real if c.get("status") == "ACTIVE"]
targets = (active or real)[:2]
for c in targets:
    cid = c["id"]
    dump(f"campaign {cid} — /campaigns/{cid} (list entry)", c)
    dump(f"campaign {cid} — /analytics", sl(f"/campaigns/{cid}/analytics"))
    dump(f"campaign {cid} — /sequences", sl(f"/campaigns/{cid}/sequences"))
    dump(f"campaign {cid} — /statistics?limit=3", sl(f"/campaigns/{cid}/statistics?limit=3"))
    dump(f"campaign {cid} — /analytics-by-date (last 14d)",
         sl(f"/campaigns/{cid}/analytics-by-date?start_date=2026-06-22&end_date=2026-07-06"))
