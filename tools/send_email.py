"""Email the alert digest through Resend (free tier: 3,000 emails/month).

    python tools/send_email.py alerts.md

Needs repo secrets RESEND_API_KEY and ALERT_EMAIL. Optional RESEND_FROM; the default
sender onboarding@resend.dev works without a verified domain, but Resend then only
delivers to the address you signed up with, which is all a personal alert needs.
Exits quietly when the secrets are missing.
"""
import html
import json
import os
import re
import sys
import urllib.request
from pathlib import Path

SITE = "https://xd00z1.github.io/out-tonight/"


def to_html(md):
    """The digest is '### Rule' headings and '- when · **title** · venue · [link](url)' lines."""
    out = ['<div style="font-family:-apple-system,Segoe UI,Helvetica,Arial,sans-serif;font-size:15px;line-height:1.45;color:#1a1916">']
    for line in md.splitlines():
        if line.startswith("### "):
            out.append(f'<h3 style="font-size:13px;letter-spacing:.06em;text-transform:uppercase;color:#8a857a;margin:20px 0 6px">{html.escape(line[4:])}</h3>')
        elif line.startswith("- "):
            m = re.match(r"- (.*?) · \*\*(.*?)\*\* · (.*?) · \[link\]\((.*?)\)$", line)
            if m:
                when, title, venue, url = (html.escape(x) for x in m.groups())
                out.append(f'<p style="margin:0 0 10px"><a href="{url}" style="color:#c2410c;font-weight:600;text-decoration:none">{title}</a>'
                           f'<br><span style="color:#8a857a;font-size:13px">{when} · {venue}</span></p>')
            else:
                out.append(f"<p>{html.escape(line[2:])}</p>")
    out.append(f'<p style="margin-top:24px;font-size:12px;color:#8a857a"><a href="{SITE}" style="color:#8a857a">Out Tonight</a>'
               ' · edit alerts in alerts.yaml</p></div>')
    return "\n".join(out)


def send(md, key, to, sender):
    n = sum(1 for l in md.splitlines() if l.startswith("- "))
    payload = {"from": sender, "to": [to], "subject": f"Out Tonight: {n} new match{'es' if n != 1 else ''}",
               "html": to_html(md), "text": md}
    req = urllib.request.Request("https://api.resend.com/emails", data=json.dumps(payload).encode(), method="POST",
                                 headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json",
                                          "User-Agent": "out-tonight/1.0"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.status


if __name__ == "__main__":
    key, to = os.environ.get("RESEND_API_KEY"), os.environ.get("ALERT_EMAIL")
    md = Path(sys.argv[1]).read_text(encoding="utf-8") if len(sys.argv) > 1 else ""
    if not (key and to and md.strip()):
        sys.exit(0)
    try:
        print("resend:", send(md, key, to, os.environ.get("RESEND_FROM") or "Out Tonight <onboarding@resend.dev>"))
    except Exception as e:  # a failed email must not fail the refresh
        print(f"resend failed: {e}", file=sys.stderr)
