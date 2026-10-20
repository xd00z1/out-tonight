"""Import Carnegie Hall's season from its press-kit "Chronology of Events" PDF.

Carnegie's own events page is behind bot protection, but the press kit publishes a
full dated listing ("Current as of ..."). Re-run this now and then (monthly is fine):

    pip install pypdf
    python importers/carnegie.py

Writes seed_carnegie.json, which refresh.py loads alongside seed_events.json.
"""
import json
import re
import sys
from datetime import datetime
from io import BytesIO
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from refresh import TZ, fetch, iso  # noqa: E402

PDF = ("https://www.carnegiehall.org/-/media/CarnegieHall/Files/PDFs/About/Press-Resources/"
       "2026-2027-Season/13-20262027-Chronology-of-Events.pdf?la=en")
DATE = re.compile(r"^(?:Mon|Tues|Wednes|Thurs|Fri|Satur|Sun)day, (\w+) (\d{1,2}), (\d{4}) at (\d{1,2}):(\d\d) (AM|PM)$")
HALLS = {"Stern Auditorium / Perelman Stage", "Zankel Hall", "Weill Recital Hall", "Resnick Education Wing"}
OFFSITE = {"Divine": "Cathedral of St. John the Divine", "Guggenheim New York": "Guggenheim",
           "Presented by The Metropolitan Opera": "Metropolitan Opera House"}
HEADER = re.compile(r"^(Current as of|2026.2027 Chronological Listing|Carnegie Hall 2026|Chronological Listing|_+$|"
                    r"(January|February|March|April|May|June|July|August|September|October|November|December) \d{4}$)")
CREDIT = re.compile(r"[.,;:]$|made possible|[Ss]ponsor|[Ff]oundation|[Ff]unded|support", re.I)
PROGRAM = re.compile(r"^[A-ZÀ-Ý][A-ZÀ-Ý.\-' ]{2,} [A-Z0-9\"“'‘]")  # "BRAHMS Piano Concerto No. 1"
JAZZ = re.compile(r"jazz|spalding|Marsalis|Reeves|Lakecia|Billy Childs|Sean Jones|Coltrane", re.I)


def parse(text):
    # Blank lines and page headers both end a title block, so keep them as "".
    lines = ["" if HEADER.match(l.strip()) else l.strip() for l in text.splitlines()]
    out, i = [], 0
    while i < len(lines):
        if not DATE.match(lines[i]):
            i += 1
            continue
        # header above the first date: hall line, then up to 3 title lines
        j = i - 1
        while j >= 0 and not lines[j]:
            j -= 1
        hall = lines[j]
        k = j - (2 if hall == "Divine" else 1)
        title = lines[k] if k >= 0 and not CREDIT.search(lines[k]) else ""
        prev = lines[k - 1] if k >= 1 else ""
        if title and prev and not CREDIT.search(prev) and not PROGRAM.match(prev) and (
                len(title.split()) <= 2 or re.search(r"(with|with the|featuring|and|&)$", prev)):
            title = f"{prev} {title}"  # a title wrapped onto two lines
        venue = f"Carnegie Hall, {hall.split(' /')[0]}" if hall in HALLS else OFFSITE.get(hall, hall)
        # all dates in this block
        dates = []
        while i < len(lines) and (DATE.match(lines[i]) or not lines[i]):
            if DATE.match(lines[i]):
                dates.append(DATE.match(lines[i]))
            i += 1
        block = []
        while i < len(lines) and not DATE.match(lines[i]) and len(block) < 60:
            block.append(lines[i])
            i += 1
        # stop the block at the next listing's header (hall line) so programs don't bleed over
        for n, l in enumerate(block):
            if l in HALLS or l in OFFSITE:
                block = block[:max(0, n - 3)]
                break
        if not title:  # header lost to a page break: use the first performer line
            title = next((l for l in block if l and "," not in l), None) or next((l.split(",")[0] for l in block if l), "Carnegie Hall concert")
        programs = [l for l in block if PROGRAM.match(l)]
        opera = next((l for l in programs if "opera in concert" in l.lower()), None)
        if opera:
            composer, _, work = opera.partition(" ")
            title = f"{work.replace('(opera in concert)', '').strip()} in concert ({composer.title()})"
        if any("Well-Being Concert" in l for l in block[:15]):
            why = "Well-Being Concert"
        elif programs and not opera:
            first = programs[0].split(" ", 1)
            why = f"{first[0].title()} {first[1] if len(first) > 1 else ''}".strip()
            why += f" + {len(programs) - 1} more" if len(programs) > 1 else ""
        else:
            why = None
        for m in dates:
            month, day, year, hh, mm, ap = m.groups()
            h = int(hh) % 12 + (12 if ap == "PM" else 0)
            start = datetime.strptime(f"{month} {day} {year}", "%B %d %Y").replace(hour=h, minute=int(mm), tzinfo=TZ)
            row = {"title": title, "start": iso(start),
                   "category": "jazz_music" if JAZZ.search(title) else "opera_classical",
                   "url": "https://www.carnegiehall.org/events", "cost_text": "Not stated", "venue": venue,
                   "address": "881 Seventh Ave, New York, NY" if venue.startswith("Carnegie") else None,
                   "neighborhood": "Midtown" if venue.startswith("Carnegie") else None,
                   "why": why, "source_id": "seed", "origin": "carnegie"}
            out.append({k: v for k, v in row.items() if v is not None})
    return out


if __name__ == "__main__":
    from pypdf import PdfReader

    r, err = fetch(PDF)
    if r is None or r.status_code != 200:
        sys.exit(f"could not fetch PDF: {err or r.status_code}")
    text = "\n".join(p.extract_text() or "" for p in PdfReader(BytesIO(r.content)).pages)
    events = parse(text)
    (ROOT / "seed_carnegie.json").write_text(json.dumps(events, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"{len(events)} Carnegie performances written to seed_carnegie.json")
