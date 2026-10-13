"""Regenerate public/events.json, public/calendar.ics and public/status.json.

    python refresh.py              full run
    python refresh.py --offline    skip all network (seed + recurring only)

Five steps: load -> fetch -> extract -> normalize/dedupe -> write.
Only structured data is read (ICS, The Events Calendar API, JSON-LD).
No LLM or other paid API is called. A failing source never fails the run.
"""
from __future__ import annotations

import hashlib
import html as htmllib
import json
import re
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib import robotparser
from urllib.parse import quote, urlparse
from zoneinfo import ZoneInfo

import requests
import yaml

TZ = ZoneInfo("America/New_York")
ROOT = Path(__file__).parent
PUBLIC = ROOT / "public"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/129.0 Safari/537.36")
TIMEOUT = 20
DELAY = 1.0
DEADLINE_S = 9 * 60
RECUR_DAYS = 120
FETCH_DAYS = 60
ICS_DAYS = 60

CATEGORIES = ["openings", "museums", "fairs", "opera_classical", "jazz_music", "film",
              "performance", "books_talks", "institutes", "fashion_design",
              "watches_jewelry", "parties_benefits", "food_wine", "nightlife", "audio_vinyl"]

# Manhattan/Brooklyn zips -> neighborhood, for events whose source gives an address.
ZIP_HOOD = {
    "10013": "Tribeca", "10007": "Tribeca", "10282": "Tribeca", "10012": "SoHo",
    "10014": "West Village", "10004": "FiDi", "10005": "FiDi", "10006": "FiDi",
    "10038": "FiDi", "10280": "FiDi", "10002": "Lower East Side", "10003": "East Village",
    "10009": "East Village", "10011": "Chelsea", "10001": "Chelsea", "10010": "Flatiron",
    "10016": "Murray Hill", "10017": "Midtown East", "10022": "Midtown East",
    "10018": "Midtown", "10019": "Midtown", "10036": "Midtown", "10023": "Lincoln Square",
    "10024": "Upper West Side", "10025": "Upper West Side", "10027": "Morningside Heights",
    "10021": "Upper East Side", "10028": "Upper East Side", "10065": "Upper East Side",
    "10075": "Upper East Side", "10128": "Upper East Side", "10029": "East Harlem",
    "10026": "Harlem", "10030": "Harlem", "10037": "Harlem",
    "11201": "Brooklyn Heights", "11211": "Williamsburg", "11249": "Williamsburg",
    "11231": "Red Hook", "11217": "Fort Greene", "11215": "Park Slope", "11222": "Greenpoint",
    "11101": "Long Island City",
}


# ---------------------------------------------------------------- fetch

_session = requests.Session()
_session.headers.update({"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"})
_robots: dict[str, robotparser.RobotFileParser | None] = {}


def fetch(url, timeout=TIMEOUT, retries=1, method="GET"):
    """Return (response, None) or (None, error_string). Never raises."""
    err = None
    for attempt in range(retries + 1):
        try:
            r = _session.request(method, url, timeout=timeout, allow_redirects=True)
            if r.status_code < 500 or attempt == retries:
                return r, None
            err = f"HTTP {r.status_code}"
        except requests.RequestException as e:
            err = type(e).__name__
        time.sleep(2)
    return None, err


def robots_allows(url):
    p = urlparse(url)
    origin = f"{p.scheme}://{p.netloc}"
    if origin not in _robots:
        rp = robotparser.RobotFileParser()
        r, _ = fetch(origin + "/robots.txt", timeout=10, retries=0)
        if r is not None and r.status_code == 200:
            rp.parse(r.text.splitlines())
            _robots[origin] = rp
        else:
            _robots[origin] = None  # no robots.txt -> allowed
    rp = _robots[origin]
    return True if rp is None else rp.can_fetch(UA, url)


# ---------------------------------------------------------------- dates

def parse_dt(value, tz=TZ):
    """ISO-ish string -> (aware datetime in NY, all_day). Date-only means all_day."""
    s = str(value).strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
        return datetime.fromisoformat(s).replace(tzinfo=TZ), True
    s = s.replace("Z", "+00:00").replace(" ", "T", 1)
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=tz)
    return dt.astimezone(TZ), False


def iso(dt):
    return dt.astimezone(TZ).isoformat(timespec="seconds")


def gcal_link(ev):
    """Google Calendar 'add event' template URL, times local to New York."""
    start, all_day = parse_dt(ev["start"])
    if ev.get("all_day") or all_day:
        last = date.fromisoformat(ev["multi_day_end"]) if ev.get("multi_day_end") else start.date()
        dates = f"{start:%Y%m%d}/{last + timedelta(days=1):%Y%m%d}"
    else:
        end = parse_dt(ev["end"])[0] if ev.get("end") else start + timedelta(hours=2)
        dates = f"{start:%Y%m%dT%H%M%S}/{end.astimezone(TZ):%Y%m%dT%H%M%S}"
    details = " ".join(x for x in [ev.get("why") or "", ev.get("url") or ""] if x)
    params = [("action", "TEMPLATE"), ("text", ev["title"]), ("dates", dates),
              ("details", details), ("location", ev.get("address") or ev.get("venue") or ""),
              ("ctz", "America/New_York")]
    return "https://calendar.google.com/calendar/render?" + "&".join(
        f"{k}={quote(str(v), safe='')}" for k, v in params)


_MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August",
           "September", "October", "November", "December"]


def date_text_present(start, text):
    """Integrity guard for text-extracted events: the event's date must literally
    appear in the page. Accepts 'October 8', 'Oct. 8', '8 October', '10/8', ISO."""
    d = parse_dt(start)[0].date()
    full, abbr, day = _MONTHS[d.month - 1], _MONTHS[d.month - 1][:3], d.day
    pats = [rf"\b{full}\s+{day}(?!\d)", rf"\b{abbr}\.?\s+{day}(?!\d)",
            rf"(?<!\d){day}(?:st|nd|rd|th)?\s+(?:of\s+)?(?:{full}|{abbr})\b",
            rf"(?<![\d/]){d.month}/{day}(?![\d])", rf"{d:%Y-%m-%d}",
            rf"\b(?:{full}|{abbr})\.?\s+{day}(?:st|nd|rd|th)\b"]
    return any(re.search(p, text, re.I) for p in pats)


# ---------------------------------------------------------------- readers

def _unfold(text):
    return re.sub(r"\r?\n[ \t]", "", text).splitlines()


def _ics_unescape(v):
    return v.replace("\\n", "\n").replace("\\N", "\n").replace("\\,", ",").replace("\\;", ";").replace("\\\\", "\\")


def _ics_dt(params, value):
    if "VALUE=DATE" in params or re.fullmatch(r"\d{8}", value):
        return datetime.strptime(value[:8], "%Y%m%d").replace(tzinfo=TZ), True
    tzm = re.search(r"TZID=([^;:]+)", params)
    tz = TZ
    if tzm:
        try:
            tz = ZoneInfo(tzm.group(1).strip('"'))
        except Exception:
            tz = TZ
    dt = datetime.strptime(value.rstrip("Z")[:15], "%Y%m%dT%H%M%S")
    dt = dt.replace(tzinfo=ZoneInfo("UTC") if value.endswith("Z") else tz)
    return dt.astimezone(TZ), False


def ics_events(text):
    out, cur = [], None
    for line in _unfold(text):
        if line == "BEGIN:VEVENT":
            cur = {}
        elif line == "END:VEVENT" and cur is not None:
            if "start" in cur and cur.get("title"):
                out.append(cur)
            cur = None
        elif cur is not None and ":" in line:
            key, value = line.split(":", 1)
            name, _, params = key.partition(";")
            if name == "SUMMARY":
                cur["title"] = _ics_unescape(value)
            elif name == "DTSTART":
                cur["start"], cur["all_day"] = _ics_dt(params, value)
            elif name == "DTEND":
                cur["end"], _ = _ics_dt(params, value)
            elif name == "LOCATION":
                cur["location"] = _ics_unescape(value)
            elif name == "URL":
                cur["url"] = value
            elif name == "DESCRIPTION":
                cur["description"] = _ics_unescape(value)
    for e in out:  # ICS all-day DTEND is exclusive
        if e.get("all_day") and e.get("end"):
            last = (e["end"] - timedelta(days=1)).date()
            e["multi_day_end"] = last.isoformat() if last > e["start"].date() else None
            e["end"] = None
    return out


def tribe_events(data):
    out = []
    for e in data.get("events", []):
        tz = TZ
        try:
            tz = ZoneInfo(e.get("timezone") or "America/New_York")
        except Exception:
            pass
        all_day = bool(e.get("all_day"))
        start = datetime.fromisoformat(e["start_date"]).replace(tzinfo=tz).astimezone(TZ)
        end = datetime.fromisoformat(e["end_date"]).replace(tzinfo=tz).astimezone(TZ) if e.get("end_date") else None
        v = e.get("venue") or {}
        if isinstance(v, list):
            v = v[0] if v else {}
        addr = ", ".join(x for x in [v.get("address"), v.get("city"), v.get("state") or v.get("province")] if x)
        ev = {"title": htmllib.unescape(e.get("title", "")), "start": start, "all_day": all_day,
              "end": None if all_day else end, "url": e.get("url"), "venue": v.get("venue"),
              "address": addr or None, "zip": v.get("zip"), "cost": e.get("cost") or None}
        if all_day and end and end.date() > start.date():
            ev["multi_day_end"] = end.date().isoformat()
        out.append(ev)
    return out


def _walk_ld(node):
    if isinstance(node, list):
        for n in node:
            yield from _walk_ld(n)
    elif isinstance(node, dict):
        t = node.get("@type")
        types = t if isinstance(t, list) else [t]
        if any(isinstance(x, str) and x.endswith("Event") for x in types):
            yield node
        for k in ("@graph", "itemListElement", "item", "subEvent", "event", "events"):
            if k in node:
                yield from _walk_ld(node[k])


def jsonld_events(html):
    out = []
    for block in re.findall(r'<script[^>]+application/ld\+json[^>]*>(.*?)</script>', html, re.S | re.I):
        try:
            data = json.loads(block.strip())
        except ValueError:
            continue
        for n in _walk_ld(data):
            if not n.get("startDate") or not n.get("name"):
                continue
            try:
                start, all_day = parse_dt(n["startDate"])
            except ValueError:
                continue
            end = None
            multi = None
            if n.get("endDate"):
                try:
                    end, end_all_day = parse_dt(n["endDate"])
                    if end_all_day or all_day:
                        multi = end.date().isoformat() if end.date() > start.date() else None
                        end = None
                except ValueError:
                    pass
            loc = n.get("location") or {}
            if isinstance(loc, list):
                loc = loc[0] if loc else {}
            addr_obj = loc.get("address") if isinstance(loc, dict) else None
            if isinstance(addr_obj, dict):
                addr = ", ".join(x for x in [addr_obj.get("streetAddress"), addr_obj.get("addressLocality")] if x)
                zipc = addr_obj.get("postalCode")
            else:
                addr, zipc = (addr_obj if isinstance(addr_obj, str) else None), None
            offers = n.get("offers") or {}
            if isinstance(offers, list):
                offers = offers[0] if offers else {}
            cost = None
            price = offers.get("price") if isinstance(offers, dict) else None
            if n.get("isAccessibleForFree") in (True, "true", "True") or str(price) in ("0", "0.0", "0.00"):
                cost = "Free"
            elif price not in (None, ""):
                cost = f"${price}" if re.fullmatch(r"[\d.]+", str(price)) else str(price)
            url = n.get("url")
            out.append({"title": htmllib.unescape(str(n["name"])).strip(), "start": start,
                        "all_day": all_day, "end": end, "multi_day_end": multi,
                        "url": url if isinstance(url, str) else None,
                        "venue": loc.get("name") if isinstance(loc, dict) else None,
                        "address": addr or None, "zip": zipc, "cost": cost})
    return out


_START_KEYS = ("startDate", "start_date", "start", "startTime", "start_time", "startsAt", "starts_at", "eventDate", "event_date")
_END_KEYS = ("endDate", "end_date", "end", "endTime", "end_time", "endsAt", "ends_at")


def nextdata_events(html, base_url):
    """Events from a Next.js page's embedded __NEXT_DATA__ JSON: any object that has
    a string title/name and an ISO date under a start-like key. Deterministic, no guessing."""
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
    if not m:
        return []
    try:
        data = json.loads(m.group(1))
    except ValueError:
        return []
    origin = "{0.scheme}://{0.netloc}".format(urlparse(base_url))
    out, stack = [], [data]
    while stack:
        n = stack.pop()
        if isinstance(n, list):
            stack.extend(n)
            continue
        if not isinstance(n, dict):
            continue
        stack.extend(n.values())
        title = next((n[k] for k in ("title", "name", "headline") if isinstance(n.get(k), str) and n[k].strip()), None)
        skey = next((k for k in _START_KEYS if isinstance(n.get(k), str) and re.match(r"\d{4}-\d{2}-\d{2}", n[k])), None)
        if not title or not skey:
            continue
        try:
            start, all_day = parse_dt(n[skey])
        except ValueError:
            continue
        end = multi = None
        ekey = next((k for k in _END_KEYS if isinstance(n.get(k), str) and re.match(r"\d{4}-\d{2}-\d{2}", n[k])), None)
        if ekey:
            try:
                end, end_all_day = parse_dt(n[ekey])
                if all_day or end_all_day:
                    multi = end.date().isoformat() if end.date() > start.date() else None
                    end = None
                elif end <= start:
                    end = None
            except ValueError:
                end = None
        url = next((n[k] for k in ("url", "link", "href", "permalink") if isinstance(n.get(k), str)), None)
        if url and url.startswith("/"):
            url = origin + url
        elif url and not url.startswith("http"):
            url = None
        out.append({"title": htmllib.unescape(title).strip(), "start": start, "all_day": all_day,
                    "end": end, "multi_day_end": multi, "url": url})
    return out


# ---------------------------------------------------------------- normalize

def norm_key(s):
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def make_id(title, start, venue):
    day = str(start)[:10]
    return hashlib.sha1(f"{norm_key(title)}|{day}|{norm_key(venue)}".encode()).hexdigest()[:16]


def cost_tier_for(cost_text, default):
    if cost_text and re.search(r"\bfree\b", cost_text, re.I) and not re.search(r"\$\s*[1-9]", cost_text):
        return "free"
    if cost_text and re.search(r"\$\s*\d", cost_text):
        return "per_ticket"
    return default


_SMALL = {"a", "an", "and", "at", "by", "for", "in", "of", "on", "or", "the", "to", "with", "from", "x"}


def tidy_title(t):
    t = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", htmllib.unescape(t))).strip()
    letters = [c for c in t if c.isalpha()]
    if len(letters) > 8 and sum(c.isupper() for c in letters) / len(letters) > 0.8:
        words = t.lower().split(" ")
        cap = lambda w: "-".join(p[:1].upper() + p[1:] for p in w.split("-"))
        t = " ".join(w if (i and w in _SMALL) else cap(w) for i, w in enumerate(words))
    return t[:200]


def _clean(s):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", htmllib.unescape(s))).strip() or None if s else None


def excluded(title, patterns, location=""):
    text = f"{title} {location or ''}"
    return any(p and re.search(p, text, re.I) for p in patterns)


def from_raw(raw, src, today):
    venue = raw.get("venue") or src.get("venue")
    # A timed event spanning several days is a multi-day item. Short runs (a 3-night
    # residency) keep their nightly start time; long runs (exhibitions) become all-day.
    if not raw.get("all_day") and raw.get("end") and raw["end"].date() > raw["start"].date()             and (raw["end"] - raw["start"]).total_seconds() > 24 * 3600:
        long_run = (raw["end"].date() - raw["start"].date()).days > 7
        raw = {**raw, "all_day": long_run, "multi_day_end": raw["end"].date().isoformat(), "end": None}
    cost_text = (raw.get("cost") or "").strip() or "Not stated"
    zipc = raw.get("zip") or (re.search(r"\b(1[01]\d{3})\b", raw.get("address") or "") or [None, None])[1]
    start = raw["start"]
    ev = {
        "title": tidy_title(raw["title"]),
        "start": start.date().isoformat() if raw.get("all_day") else iso(start),
        "end": iso(raw["end"]) if raw.get("end") and not raw.get("all_day") else None,
        "all_day": bool(raw.get("all_day")),
        "multi_day_end": raw.get("multi_day_end"),
        "venue": htmllib.unescape(venue) if venue else None,
        "address": src.get("address") or raw.get("address") or _clean(raw.get("location")),
        "neighborhood": ZIP_HOOD.get(str(zipc)) if zipc else src.get("neighborhood"),
        "category": src["category"],
        "cost_tier": cost_tier_for(cost_text, src.get("default_cost_tier")),
        "cost_text": cost_text,
        "guest_ok": src.get("default_guest_ok"),
        "rsvp_required": False,
        "age_min": src.get("default_age_min"),
        "url": raw.get("url") or src["url"],
        "source_id": src["id"],
        "why": src.get("why"),
        "confidence": "verified",
        "kind": "event",
        "recurring": False,
    }
    if not ev["neighborhood"]:
        ev["neighborhood"] = src.get("neighborhood")
    return finish(ev, today)


def entry_for(ev):
    """How you get in: walk_in, rsvp, ticket, or None when unknown."""
    if ev.get("rsvp_required"):
        return "rsvp"
    if ev.get("cost_tier") in ("per_ticket", "light", "mid", "heavy"):
        return "ticket"
    if ev.get("cost_tier") == "free":
        return "walk_in"
    return None


def price_min(ev):
    """Lowest dollar figure in the cost text; 0 when free; None when unknown."""
    if ev.get("cost_tier") == "free":
        return 0
    nums = [float(n.replace(",", "")) for n in re.findall(r"\$\s*([\d,]+(?:\.\d+)?)", ev.get("cost_text") or "")]
    return min(nums) if nums else None


def finish(ev, today):
    """Fill derived fields: id, seen dates, calendar link."""
    ev.setdefault("end", None)
    ev.setdefault("multi_day_end", None)
    ev.setdefault("all_day", "T" not in str(ev["start"]))
    for k, v in [("venue", None), ("address", None), ("neighborhood", None), ("cost_tier", None),
                 ("cost_text", "Not stated"), ("guest_ok", None), ("rsvp_required", False),
                 ("age_min", None), ("why", None), ("kind", "event"), ("recurring", False)]:
        ev.setdefault(k, v)
    ev["id"] = ev.get("id") or make_id(ev["title"], ev["start"], ev.get("venue"))
    ev["entry"] = entry_for(ev)
    ev["price_min"] = price_min(ev)
    ev.setdefault("first_seen", today.isoformat())
    ev["last_seen"] = today.isoformat()
    ev["gcal_url"] = gcal_link(ev)
    return ev


def last_day(ev):
    """Last calendar day the event occupies (for dropping past events)."""
    if ev.get("multi_day_end"):
        return date.fromisoformat(ev["multi_day_end"])
    if ev.get("end"):
        return parse_dt(ev["end"])[0].date()
    return parse_dt(ev["start"])[0].date()


# ---------------------------------------------------------------- recurring

_DOW = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}


def rule_matches(rule, d):
    if rule.get("until") and d > date.fromisoformat(str(rule["until"])):
        return False
    if rule.get("from") and d < date.fromisoformat(str(rule["from"])):
        return False
    if d.month in rule.get("except_months", []):
        return False
    if "monthday" in rule:
        return d.day == rule["monthday"]
    if d.weekday() not in [_DOW[x] for x in rule.get("days", list(_DOW))]:
        return False
    if "nth" in rule:  # nth weekday of the month, e.g. first Friday = 1
        return (d.day - 1) // 7 + 1 in rule["nth"]
    return True


def expand_recurring(rules, today, days=RECUR_DAYS):
    out = []
    for rule in rules:
        for i in range(days):
            d = today + timedelta(days=i)
            if not rule_matches(rule, d):
                continue
            ev = {k: v for k, v in rule.items() if k in (
                "title", "venue", "address", "neighborhood", "category", "cost_tier",
                "cost_text", "guest_ok", "rsvp_required", "age_min", "url", "why", "kind")}
            if rule.get("time"):
                h, m = map(int, str(rule["time"]).split(":"))
                start = datetime(d.year, d.month, d.day, h, m, tzinfo=TZ)
                ev["start"], ev["all_day"] = iso(start), False
                if rule.get("end"):
                    eh, em = map(int, str(rule["end"]).split(":"))
                    end = datetime(d.year, d.month, d.day, eh, em, tzinfo=TZ)
                    if end <= start:
                        end += timedelta(days=1)
                    ev["end"] = iso(end)
            else:
                ev["start"], ev["all_day"] = d.isoformat(), True
            ev.update(source_id="recurring", confidence="carried", recurring=True)
            out.append(finish(ev, today))
    return out


# ---------------------------------------------------------------- merge

def merge(previous, fresh, today, ok_sources):
    """Merge this run's verified events into the previous file.

    - keeps first_seen from the previous file
    - fetched events missing from a source that fetched OK this run get a miss;
      after two consecutive misses they become 'carried' (kept, not deleted)
    - seed/recurring rows are regenerated each run, so old copies are discarded
    """
    prev = {e["id"]: e for e in previous if e.get("source_id") not in ("seed", "recurring")}
    out = {}
    for e in fresh:
        old = prev.pop(e["id"], None) or out.get(e["id"])
        if old:
            e["first_seen"] = old.get("first_seen", e["first_seen"])
        e["missed_runs"] = 0
        out[e["id"]] = e
    for e in prev.values():
        if e["source_id"] in ok_sources:
            e["missed_runs"] = e.get("missed_runs", 0) + 1
            if e["missed_runs"] >= 2:
                e["confidence"] = "carried"
        out[e["id"]] = e
    return list(out.values())


def dedupe(events):
    """Same id, or same normalized title on the same day: keep the most trusted."""
    rank = {"verified": 0, "carried": 1, "projected": 2}
    best = {}
    for e in events:
        key = (norm_key(e["title"]), str(e["start"])[:10])
        cur = best.get(key)
        if cur is None or rank[e["confidence"]] < rank[cur["confidence"]]:
            if cur is not None:
                e["first_seen"] = min(e["first_seen"], cur["first_seen"])
            best[key] = e
    return list(best.values())


# ---------------------------------------------------------------- outputs

def _ics_escape(s):
    return (s or "").replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def _fold(line):
    raw = line.encode("utf-8")
    if len(raw) <= 75:
        return line
    parts, cur = [], b""
    for ch in line:
        b = ch.encode("utf-8")
        if len(cur) + len(b) > (75 if not parts else 74):
            parts.append(cur.decode("utf-8"))
            cur = b""
        cur += b
    parts.append(cur.decode("utf-8"))
    return "\r\n ".join(parts)


def to_ics(events, today, now_utc):
    horizon = today + timedelta(days=ICS_DAYS)
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//nyc-culture//events//EN",
             "CALSCALE:GREGORIAN", "METHOD:PUBLISH", "X-WR-CALNAME:NYC culture picks",
             "X-WR-TIMEZONE:America/New_York", "REFRESH-INTERVAL;VALUE=DURATION:PT12H"]
    stamp = now_utc.strftime("%Y%m%dT%H%M%SZ")
    for e in sorted(events, key=lambda e: str(e["start"])):
        start, _ = parse_dt(e["start"])
        if start.date() > horizon or e.get("recurring"):
            continue
        if e.get("multi_day_end") and (date.fromisoformat(e["multi_day_end"]) - start.date()).days > 31:
            continue  # long exhibition runs would sit in the all-day row for months
        title = ("On sale: " if e["kind"] == "on_sale" else "") + e["title"]
        if e["confidence"] == "projected":
            title += " (projected date)"
        lines += ["BEGIN:VEVENT", f"UID:{e['id']}@nyc-culture", f"DTSTAMP:{stamp}"]
        if e["all_day"] or e["kind"] == "on_sale":
            last = date.fromisoformat(e["multi_day_end"]) if e.get("multi_day_end") else start.date()
            lines += [f"DTSTART;VALUE=DATE:{start:%Y%m%d}",
                      f"DTEND;VALUE=DATE:{last + timedelta(days=1):%Y%m%d}"]
        else:
            end = parse_dt(e["end"])[0] if e.get("end") else start + timedelta(hours=2)
            utc = ZoneInfo("UTC")
            lines += [f"DTSTART:{start.astimezone(utc):%Y%m%dT%H%M%SZ}",
                      f"DTEND:{end.astimezone(utc):%Y%m%dT%H%M%SZ}"]
        desc = "\n".join(x for x in [e.get("why"), f"Cost: {e['cost_text']}", e["url"]] if x)
        lines += [f"SUMMARY:{_ics_escape(title)}", f"DESCRIPTION:{_ics_escape(desc)}",
                  f"LOCATION:{_ics_escape(e.get('address') or e.get('venue') or '')}",
                  f"URL:{e['url']}", "END:VEVENT"]
    lines.append("END:VCALENDAR")
    return "\r\n".join(_fold(l) for l in lines) + "\r\n"


def check_urls(events, deadline):
    """HEAD (falling back to GET) each unique URL once. Sets url_status."""
    seen = {}
    for e in events:
        u = e["url"]
        if u not in seen:
            if time.monotonic() > deadline:
                seen[u] = "unchecked"
            else:
                r, _ = fetch(u, timeout=10, retries=0, method="HEAD")
                if r is None or r.status_code in (403, 405, 404, 400, 501):
                    r, _ = fetch(u, timeout=15, retries=0)
                seen[u] = "ok" if r is not None and r.status_code < 400 else (
                    "blocked" if r is not None and r.status_code in (401, 403, 429) else "failed")
        e["url_status"] = seen[u]


def load_json(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


# ---------------------------------------------------------------- main

def run(offline=False, today=None, out=PUBLIC):
    t0 = time.monotonic()
    deadline = t0 + DEADLINE_S
    now = datetime.now(TZ)
    today = today or now.date()

    # 1. load
    cfg = yaml.safe_load((ROOT / "sources.yaml").read_text(encoding="utf-8"))
    sources = [{**cfg.get("defaults", {}), **s} for s in cfg["sources"]]
    default_ex = cfg.get("defaults", {}).get("exclude")
    pats = {s["id"]: [default_ex, s.get("exclude")] for s in cfg["sources"]}
    recurring = yaml.safe_load((ROOT / "recurring.yaml").read_text(encoding="utf-8")) or []
    seed = load_json(ROOT / "seed_events.json", [])
    previous = load_json(out / "events.json", {}).get("events", [])
    prev_status = {s["id"]: s for s in load_json(out / "status.json", {}).get("sources", [])}

    # 2-3. fetch + extract
    fresh, status, ok_sources = [], [], set()
    for src in sources:
        st = {"id": src["id"], "url": src["url"], "method": src.get("method", "link"),
              "result": None, "http_status": None, "count": 0, "error": None,
              "last_success": prev_status.get(src["id"], {}).get("last_success")}
        status.append(st)
        if not src.get("enabled", True):
            st["result"] = "disabled"
            continue
        if st["method"] == "link":
            st["result"] = "link"
            continue
        if offline or time.monotonic() > deadline:
            st["result"] = "skipped"
            continue
        try:
            target = src.get("feed_url") or src["url"]
            if st["method"] == "tribe":
                end = today + timedelta(days=FETCH_DAYS)
                target += f"?per_page=50&start_date={today}&end_date={end}"
            if not robots_allows(target):
                st.update(result="failed", error="robots.txt disallows")
                continue
            r, err = fetch(target)
            time.sleep(DELAY)
            if r is None:
                st.update(result="failed", error=err)
                continue
            st["http_status"] = r.status_code
            if r.status_code >= 400:
                st.update(result="failed", error=f"HTTP {r.status_code}")
                continue
            raws = {"ics": lambda: ics_events(r.text), "tribe": lambda: tribe_events(r.json()),
                    "jsonld": lambda: jsonld_events(r.text),
                    "nextdata": lambda: nextdata_events(r.text, target)}[st["method"]]()
            horizon = today + timedelta(days=FETCH_DAYS)
            evs = [from_raw(x, src, today) for x in raws if not excluded(x.get("title", ""), pats[src["id"]], x.get("location"))]
            evs = [e for e in evs if last_day(e) >= today and parse_dt(e["start"])[0].date() <= horizon]
            evs.sort(key=lambda e: str(e["start"]))
            evs = evs[: src.get("max_events", 15)]
            fresh += evs
            ok_sources.add(src["id"])
            st.update(result="ok" if evs else "empty", count=len(evs), last_success=today.isoformat())
        except Exception as e:  # one source must never fail the run
            st.update(result="failed", error=f"{type(e).__name__}: {e}"[:200])

    # 4. normalize + dedupe
    seeded = []
    for row in seed:
        ev = dict(row)
        ev.setdefault("source_id", "seed")
        ev.setdefault("confidence", "carried")
        seeded.append(finish(ev, today))
    merged = [e for e in merge(previous, fresh, today, ok_sources)  # config changes apply retroactively
              if e["source_id"] in pats and not excluded(e["title"], pats[e["source_id"]])]
    events = merged + seeded + expand_recurring(recurring, today)
    events = [e for e in events if last_day(e) >= today]
    events = dedupe(events)
    # A standing option is redundant when the venue's own feed lists the same slot.
    slots = {(norm_key(e["venue"]), e["start"]) for e in events if not e.get("recurring")}
    events = [e for e in events if not (e.get("recurring") and (norm_key(e["venue"]), e["start"]) in slots)]
    events.sort(key=lambda e: (str(e["start"])[:10], str(e["start"])))
    if not offline:
        check_urls(events, deadline)
    else:
        for e in events:
            e.setdefault("url_status", "unchecked")

    # 5. write
    browse = [{"id": s["id"], "url": s["url"], "category": s["category"], "note": s.get("note")}
              for s in sources if s.get("method", "link") == "link" and s.get("enabled", True)]
    counts = {k: sum(1 for s in status if s["result"] == k)
              for k in ("ok", "empty", "failed", "link", "skipped", "disabled")}
    out.mkdir(exist_ok=True)
    (out / "events.json").write_text(json.dumps(
        {"generated_at": iso(now), "events": events, "browse": browse},
        indent=1, ensure_ascii=False), encoding="utf-8")
    (out / "calendar.ics").write_text(
        to_ics(events, today, now.astimezone(ZoneInfo("UTC"))), encoding="utf-8", newline="")
    (out / "status.json").write_text(json.dumps(
        {"generated_at": iso(now), "duration_s": round(time.monotonic() - t0, 1),
         "counts": counts, "sources": status}, indent=1), encoding="utf-8")
    print(f"{len(events)} events | sources {counts} | {time.monotonic() - t0:.0f}s")
    return events


if __name__ == "__main__":
    # Source failures are handled inside run(); only a genuine bug exits non-zero,
    # and in that case the previous output files are left untouched.
    run(offline="--offline" in sys.argv)
