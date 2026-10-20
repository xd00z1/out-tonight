"""Apply a GitHub issue-form request to the config files.

Run by .github/workflows/requests.yml for issues opened by the repo owner:

    python tools/apply_request.py <event.json>

Handles three forms (by label): source-add, source-remove, alert-add.
Prints a one-line summary for the issue comment.
"""
import json
import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
CATEGORIES = ["openings", "museums", "fairs", "opera_classical", "jazz_music", "film", "performance",
              "books_talks", "institutes", "fashion_design", "watches_jewelry", "parties_benefits",
              "food_wine", "nightlife", "audio_vinyl", "social", "comedy"]


def fields(body):
    """Issue forms render as '### Label' followed by the value."""
    out = {}
    for label, value in re.findall(r"^### (.+?)\s*\n+(.*?)(?=^### |\Z)", body or "", re.S | re.M):
        value = value.strip()
        out[label.strip().lower()] = "" if value == "_No response_" else value
    return out


def slug(name):
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")[:40] or "source"


def q(v):
    return json.dumps(v, ensure_ascii=False)  # a JSON string is valid YAML


def add_source(f):
    url = f.get("url", "").strip()
    if not re.match(r"https?://", url):
        return "No valid URL given; nothing changed."
    name = f.get("name") or re.sub(r"^www\.", "", url.split("/")[2])
    cat = f.get("category", "").split()[0] if f.get("category") else "social"
    cat = cat if cat in CATEGORIES else "social"
    path = ROOT / "sources.yaml"
    text = path.read_text(encoding="utf-8")
    sid, n = slug(name), 2
    while re.search(rf"id: {sid},", text):
        sid, n = f"{slug(name)}_{n}", n + 1
    extra = f", note: {q(f['note'])}" if f.get("note") else ""
    line = f"  - {{id: {sid}, name: {q(name)}, url: {q(url)}, category: {cat}{extra}}}\n"
    path.write_text(text.rstrip("\n") + "\n" + line, encoding="utf-8")
    return f"Added **{name}** ({cat}) as `{sid}`. The probe will check it for a live feed."


def remove_source(f):
    want = (f.get("source") or "").strip().lower()
    if not want:
        return "No source named; nothing changed."
    path = ROOT / "sources.yaml"
    lines = path.read_text(encoding="utf-8").split("\n")
    for i, l in enumerate(lines):
        m = re.match(r"\s*- \{id: (\w+),", l)
        if not m:
            continue
        name = re.search(r'name: "([^"]+)"', l)
        if want in (m.group(1), slug(want)) or (name and name.group(1).lower() == want):
            if "enabled: false" not in l:
                lines[i] = l[:-1] + ", enabled: false}"
            path.write_text("\n".join(lines), encoding="utf-8")
            return f"Turned off `{m.group(1)}` for everyone. Re-enable by deleting `enabled: false` in sources.yaml."
    return f"No source matched '{want}'; nothing changed."


def add_alert(f):
    rule = {"name": f.get("alert name") or f.get("words to match") or f.get("venue") or "Alert"}
    if f.get("words to match"):
        rule["text"] = re.escape(f["words to match"]).replace(r"\ ", " ").replace(r"\|", "|")
    if f.get("venue"):
        rule["venue"] = re.escape(f["venue"]).replace(r"\ ", " ")
    cat = (f.get("category") or "").split()[0] if f.get("category") else ""
    if cat in CATEGORIES:
        rule["category"] = cat
    if "[x]" in (f.get("free only") or "").lower():
        rule["free"] = True
    if len(rule) == 1:
        return "No words, venue or category given; nothing changed."
    path = ROOT / "alerts.yaml"
    block = yaml.safe_dump([rule], allow_unicode=True, sort_keys=False)
    block = "".join("  " + l + "\n" for l in block.splitlines())
    path.write_text(path.read_text(encoding="utf-8").rstrip("\n") + "\n" + block, encoding="utf-8")
    return f"Alert added: **{rule['name']}**."


if __name__ == "__main__":
    event = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    issue = event["issue"]
    labels = {l["name"] for l in issue.get("labels", [])}
    f = fields(issue.get("body"))
    if "source-add" in labels:
        print(add_source(f))
    elif "source-remove" in labels:
        print(remove_source(f))
    elif "alert-add" in labels:
        print(add_alert(f))
    else:
        print("Not a recognized request; nothing changed.")
