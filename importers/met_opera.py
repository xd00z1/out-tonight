"""Import Metropolitan Opera performances from Operabase's public Met page.

metopera.org sits behind a bot-check waiting room, so we don't fetch it. Operabase's
company page embeds each production's run (slug, minDate, maxDate) and every
performance (startDate, startTime). Re-run monthly; later productions appear as
Operabase adds them.

    python importers/met_opera.py      -> writes seed_met.json
"""
import json
import re
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from refresh import TZ, fetch, iso  # noqa: E402

URL = "https://www.operabase.com/metropolitan-opera-o13779/en"
NAMES = {"cosi-fan-tutte": "Così fan tutte", "la-boheme": "La Bohème", "medee": "Medea", "tosca": "Tosca",
         "macbeth": "Macbeth", "met-macbeth-verdi": "Macbeth", "jenufa": "Jenůfa", "maria-stuarda": "Maria Stuarda",
         "samson-et-dalila": "Samson et Dalila", "lincoln-in-the-bardo": "Lincoln in the Bardo (world premiere)",
         "la-fanciulla-del-west": "La Fanciulla del West", "the-magic-flute": "The Magic Flute",
         "die-zauberflote": "The Magic Flute", "aida": "Aida", "otello": "Otello", "parsifal": "Parsifal",
         "manon": "Manon", "the-met-orchestra-chamber-ensemble": "Met Orchestra Chamber Ensemble",
         "asmik-grigorian-in-concert": "Asmik Grigorian in concert"}
PROD = re.compile(r'"slug":"([a-z0-9-]+)","minDate":"(\d{4}-\d\d-\d\d)","maxDate":"(\d{4}-\d\d-\d\d)"')
PERF = re.compile(r'"id":(\d+),"startDate":"(\d{4}-\d\d-\d\d)","startTime":"(\d\d:\d\d)","isCancelled":false')


def parse(html):
    h = html.replace('\\"', '"')
    prods = [(m.start(), *m.groups()) for m in PROD.finditer(h)]
    seen, out = set(), []
    for m in PERF.finditer(h):
        pid, d, t = m.groups()
        # the production whose run contains this date, nearest in the page
        cands = [p for p in prods if p[2] <= d <= p[3]]
        if not cands or pid in seen:
            continue
        seen.add(pid)
        best = min(cands, key=lambda p: abs(p[0] - m.start()))
        run_days = (datetime.fromisoformat(best[3]) - datetime.fromisoformat(best[2])).days
        start = datetime.fromisoformat(f"{d}T{t}").replace(tzinfo=TZ)
        out.append((iso(start), run_days, NAMES.get(best[1], best[1].replace("-", " ").title())))
    # One stage, one performance per slot. Season-long runs (Bohème, Tosca) overlap every
    # other production's window, so a clash goes to the production with the shorter run.
    slots = {}
    for start, run_days, title in out:
        if start not in slots or run_days < slots[start][0]:
            slots[start] = (run_days, title)
    return [{"title": title, "start": start, "category": "opera_classical",
             "url": "https://www.metopera.org/calendar/", "cost_text": "Not stated",
             "venue": "Metropolitan Opera", "address": "30 Lincoln Center Plaza, New York, NY",
             "neighborhood": "Lincoln Square", "source_id": "seed"}
            for start, (_, title) in sorted(slots.items())]


if __name__ == "__main__":
    r, err = fetch(URL)
    if r is None or r.status_code != 200:
        sys.exit(f"could not fetch Operabase: {err or r.status_code}")
    events = parse(r.text)
    (ROOT / "seed_met.json").write_text(json.dumps(events, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"{len(events)} Met Opera performances written to seed_met.json")
