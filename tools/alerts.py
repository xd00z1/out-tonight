"""Find upcoming events that match alerts.yaml and haven't been alerted yet.

    python tools/alerts.py        -> prints a Markdown digest (empty if nothing new)
                                     and records alerted ids in alerts_state.json
"""
import json
import re
import sys
from datetime import date, timedelta
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
HORIZON_DAYS = 60


def matches(rule, e):
    hay = " ".join(str(e.get(k) or "") for k in ("title", "venue", "why"))
    if rule.get("text") and not re.search(rule["text"], hay, re.I):
        return False
    if rule.get("venue") and not re.search(rule["venue"], e.get("venue") or "", re.I):
        return False
    if rule.get("category") and e.get("category") != rule["category"]:
        return False
    if rule.get("free") and e.get("price_min") != 0:
        return False
    return any(rule.get(k) for k in ("text", "venue", "category", "free"))


def digest(events, rules, sent, today):
    horizon = (today + timedelta(days=HORIZON_DAYS)).isoformat()
    hits = {}
    for e in events:
        if e.get("recurring") or e["id"] in sent or not (today.isoformat() <= e["start"][:10] <= horizon):
            continue
        for r in rules:
            if matches(r, e):
                hits.setdefault(r.get("name") or r.get("text") or r.get("venue"), []).append(e)
                break
    lines = []
    for name, evs in hits.items():
        lines.append(f"### {name}")
        for e in sorted(evs, key=lambda e: e["start"]):
            when = e["start"][:16].replace("T", " ")
            lines.append(f"- {when} · **{e['title']}** · {e.get('venue') or ''} · [link]({e['url']})")
        lines.append("")
    return "\n".join(lines), [e["id"] for evs in hits.values() for e in evs]


if __name__ == "__main__":
    rules = (yaml.safe_load((ROOT / "alerts.yaml").read_text(encoding="utf-8")) or {}).get("rules") or []
    state_path = ROOT / "alerts_state.json"
    sent = set(json.loads(state_path.read_text()) if state_path.exists() else [])
    events = json.loads((ROOT / "public" / "events.json").read_text(encoding="utf-8"))["events"]
    body, ids = digest(events, rules, sent, date.today())
    if ids:
        state_path.write_text(json.dumps(sorted(sent | set(ids))))
    sys.stdout.write(body)
