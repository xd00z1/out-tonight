"""One-off: probe each source in sources.yaml for the best machine-readable method.

    python probe.py            -> prints a table, writes probe_results.json
    python probe.py --write    -> also writes method/feed_url back into sources.yaml

Checks, in order: The Events Calendar REST API, ICS feed, JSON-LD Event blocks,
Next.js embedded data.
Anything else becomes method: link.
"""
import json
import re
import sys
import time
from urllib.parse import urljoin, urlparse

import yaml

from refresh import fetch, jsonld_events, nextdata_events, robots_allows

ROOT = __import__("pathlib").Path(__file__).parent


def probe(src):
    url = src["url"]
    out = {"id": src["id"], "status": None, "method": "link", "feed_url": None, "detail": ""}
    if not robots_allows(url):
        out.update(status="robots", detail="disallowed by robots.txt")
        return out
    resp, err = fetch(url)
    if resp is None:
        out.update(status="error", detail=err)
        return out
    out["status"] = resp.status_code
    if resp.status_code >= 400:
        out["detail"] = f"HTTP {resp.status_code}"
        return out
    html = resp.text

    # 1. The Events Calendar REST first: richer than its own ICS export (venue, cost)
    origin = f"{urlparse(url).scheme}://{urlparse(url).netloc}"
    api = urljoin(origin, "/wp-json/tribe/events/v1/events?per_page=5")
    r, _ = fetch(api)
    if r is not None and r.status_code == 200 and r.headers.get("content-type", "").startswith("application/json"):
        try:
            n = len(r.json().get("events", []))
        except ValueError:
            n = 0
        if n:
            out.update(method="tribe", feed_url=urljoin(origin, "/wp-json/tribe/events/v1/events"), detail=f"{n}+ events")
            return out

    # 2. ICS: explicit link tags or hrefs ending .ics / ical
    cands = re.findall(r'href=["\']([^"\']+(?:\.ics|[?&]ical=1|/ical/?|webcal:[^"\']+))["\']', html, re.I)
    if "tribe-events" in html or "/wp-json/tribe/" in html:
        cands.append(urljoin(origin, "/events/?ical=1"))
    for c in cands:
        c = urljoin(url, c.replace("webcal:", "https:"))
        r, _ = fetch(c)
        if r is not None and r.status_code == 200 and "BEGIN:VCALENDAR" in r.text[:500]:
            n = r.text.count("BEGIN:VEVENT")
            if n:
                out.update(method="ics", feed_url=c, detail=f"{n} VEVENTs")
                return out

    # 3. JSON-LD
    evs = jsonld_events(html)
    if evs:
        out.update(method="jsonld", detail=f"{len(evs)} JSON-LD events")
        return out

    # 4. Next.js embedded data (count only future-dated objects)
    today = __import__("datetime").date.today()
    nd = [e for e in nextdata_events(html, url) if e["start"].date() >= today]
    if nd:
        out.update(method="nextdata", detail=f"{len(nd)} upcoming in __NEXT_DATA__")
        return out

    rss = re.search(r'<link[^>]+type=["\']application/(?:rss|atom)\+xml["\'][^>]*>', html, re.I)
    out["detail"] = "rss posts only" if rss else "no structured events"
    return out


def write_back(results):
    path = ROOT / "sources.yaml"
    lines = path.read_text(encoding="utf-8").splitlines()
    by_id = {r["id"]: r for r in results}
    new = []
    for line in lines:
        m = re.match(r"\s*- \{id: (\w+),", line)
        if m and m.group(1) in by_id:
            r = by_id[m.group(1)]
            line = re.sub(r", (method|feed_url|probe): [^,}]+", "", line)
            add = f", method: {r['method']}"
            if r["feed_url"]:
                add += f', feed_url: "{r["feed_url"]}"'
            add += f', probe: "{r["status"]} {r["detail"]}"'
            line = line[:-1] + add + "}"
        new.append(line)
    path.write_text("\n".join(new) + "\n", encoding="utf-8")


if __name__ == "__main__":
    cfg = yaml.safe_load((ROOT / "sources.yaml").read_text(encoding="utf-8"))
    results = []
    for s in cfg["sources"]:
        r = probe(s)
        results.append(r)
        print(f"{r['id']:24} {str(r['status']):6} {r['method']:7} {r['detail']}", flush=True)
        time.sleep(1)
    (ROOT / "probe_results.json").write_text(json.dumps(results, indent=2))
    if "--write" in sys.argv:
        write_back(results)
