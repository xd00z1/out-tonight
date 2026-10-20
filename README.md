# Out Tonight

A personal list of NYC cultural events: a static page fed by `public/events.json`, plus a calendar feed for Google Calendar. One script regenerates both.

```
sources.yaml        pages to check, with the method probe.py found for each
recurring.yaml      standing weekly/monthly options (museum free nights, late jazz...)
seed_events.json    hand-entered dated events (fairs, festivals, one-offs)
seed_*.json         importer output (Carnegie, Met Opera); every seed*.json is loaded
refresh.py          load -> fetch -> extract -> normalize/dedupe -> write
probe.py            one-off: detect the best method for each source
public/index.html   the page: month calendar (tap a day) or All list, with price/entry/type filters
public/events.json  generated
public/calendar.ics generated, subscribable
public/status.json  generated, per-source result
schema/             JSON schema for events.json
tests/              unit tests (python -m unittest discover -s tests)
```

## Run locally

```bash
pip install -r requirements.txt
```

```bash
python refresh.py
```

```bash
python -m http.server 8765 --directory public
```

Then open http://localhost:8765. `python refresh.py --offline` rebuilds from seed and recurring only, with no network.

## How events get in

No LLM or paid API is called. The script reads only structured data:

| method | what it reads |
|---|---|
| `tribe` | WordPress "The Events Calendar" REST API |
| `ics` | an iCalendar feed |
| `jsonld` | schema.org `Event` blocks in the page |
| `nextdata` | dated objects in a Next.js page's `__NEXT_DATA__` |
| `localist` | Localist calendar API (university calendars) |
| `cards` | server-rendered `<article>` cards with a `<time datetime>` |
| `link` | nothing parsed; listed under "More calendars" at the bottom of the page |

The probe also finds ICS feeds hidden behind embedded Trumba, Google Calendar and Tockify widgets (that is how the Frick works).

**Importers** (`importers/`) cover big venues whose sites block scripts:
- `carnegie.py` reads Carnegie Hall's press-kit "Chronology of Events" PDF → `seed_carnegie.json`
- `met_opera.py` reads the Met's performance list from Operabase → `seed_met.json`

The workflow re-runs both on every refresh and keeps the last files if a site blocks it. Running them locally once a month is plenty:

```bash
python importers/carnegie.py
```

```bash
python importers/met_opera.py
```

Probe results as of Oct 8, 2026: 6 of 61 sources have structured events (Scandinavia House, LMCC, Dizzy's, Austrian Cultural Forum, New Museum, Luma NYC). The rest are link-only, so the page lists them for you to check by hand. Re-probe now and then, since sites change platforms:

```bash
python probe.py --write
```

`--write` overwrites `method`/`feed_url` in `sources.yaml`, so re-apply any hand edits afterwards. (`lmcc` and `scandinavia_house` were switched from `ics` to `tribe` by hand. The probe now prefers `tribe` anyway.)

**Noise control.** `exclude` in `sources.yaml` is a title regex. The default one drops sold-out, online, and class listings. Luma has its own, aimed at tech meetups. Changes apply on the next run, including to events already in the file.

**Confidence.** `verified` = read from the source this run. `carried` = from seed/recurring, or a fetched event missing from its source for two consecutive runs. `projected` = date inferred; the page badges it.

## Adding things by hand

- A one-off or fair: add a row to `seed_events.json` (only `title`, `start`, `category`, `url` are required; date-only `start` means all-day; `multi_day_end` for runs).
- A standing night: add a rule to `recurring.yaml` (see the key reference at the top of the file).
- A new source: add a row to `sources.yaml`, run `python probe.py`, and set `method`.

## Publish (GitHub Pages)

1. Create a GitHub repo and push. A free GitHub Pages site needs a public repo, and nothing in this repo is personal.
2. Repo Settings → Pages → Source: **GitHub Actions**.
3. The workflow runs Mondays and Thursdays at 10:00 UTC (6am EDT, 5am EST), on manual dispatch, and on pushes. It runs the tests, refreshes, commits the regenerated files (free history and rollback), and deploys `public/`.

## Google Calendar

Google Calendar → Other calendars → **From URL** → `https://<user>.github.io/<repo>/calendar.ics`. It shows up as its own calendar you can toggle. Google re-fetches subscribed feeds on its own schedule (often 8 to 24 hours), so the page is the fresh view. UIDs are stable, so refreshes update events instead of duplicating them. The feed holds the next 60 days of dated events plus on-sale reminders. It leaves out standing options and exhibition runs longer than a month.

Each event's **+** button adds it to Google Calendar (New York local time).

## Decisions taken (from the handoff's open list)

1. Where it runs: GitHub Pages + scheduled Action; also runs locally.
2. Public URL: unlisted, `noindex`, no personal data.
3. LLM extraction: **off**. Structured sources only. `date_text_present()` in `refresh.py` is the guard any future text extractor must pass (it has tests).
4. Cadence: Mon/Thu morning.
5. Age gate: 21+ is tagged, not hidden.

## Not done yet

- `seed_events.json` holds the research report's dated calendar (95 rows, Oct 2026 to Sep 2027). The report itself is not in the repo: it contains personal details, and `docs/research-report.md` is gitignored if you save it there.
- Coverage is thin on openings (Tribeca Arts District, Artwrld, and Artnet aren't machine-readable), nightlife, and vinyl/hi-fi. Those need either new sources or a text extractor.
- `recurring.yaml` hours come from the research and haven't been re-checked against venue pages.

## Features on the page

- **Types**: pick one or more categories (Film + Museums, Music + Classical...). Remembered on the device.
- **Settings** (gear icon): calendar app for the + button (Google, Apple, Outlook); subscribe links for each;
  per-source switches (hide on this device); add/remove sources for everyone; alerts; notes backup; AI link.
- **Event sheet** (tap an event): add to any calendar, venue notes, alert buttons, hide the source.
- **Venue notes** stay in the browser (localStorage), never in this public repo. Export/import in Settings.
- **Install**: it's a PWA. iPhone: Safari, Share, Add to Home Screen. Android: Chrome menu, Install app.

## Requests and alerts (GitHub forms)

"Add a source", "Remove for everyone" and "Alert me" open prefilled issue forms. The `requests` workflow
applies them (only for issues opened by the repo owner), commits, and triggers a refresh.

Alert rules live in `alerts.yaml`. After each refresh, new matches are posted as one GitHub issue assigned
to the owner, which GitHub emails. For phone pushes, install the free [ntfy](https://ntfy.sh) app,
subscribe to a hard-to-guess topic name, and save that name as the repo secret `NTFY_TOPIC`.

## For AI assistants

`brief.txt` (next 7 days, one event per line) and `llms.txt` are published next to the page. Point
Grok or any assistant at `https://<user>.github.io/<repo>/brief.txt` for a daily briefing.
