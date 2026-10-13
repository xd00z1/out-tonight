import json
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import refresh as R  # noqa: E402

TODAY = date(2026, 10, 8)


def ev(**kw):
    base = {"title": "Test", "start": "2026-10-20T19:00:00-04:00", "venue": "Somewhere",
            "category": "openings", "url": "https://example.org/e", "source_id": "x",
            "confidence": "verified"}
    base.update(kw)
    return R.finish(base, TODAY)


def gcal_params(e):
    return {k: v[0] for k, v in parse_qs(urlparse(e["gcal_url"]).query).items()}


class Dedupe(unittest.TestCase):
    def test_same_title_and_day_keeps_most_trusted(self):
        a = ev(title="Opening: Jane Doe", confidence="carried", source_id="seed")
        b = ev(title="opening — jane doe!", confidence="verified", venue="Other venue")
        out = R.dedupe([a, b])
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["confidence"], "verified")

    def test_different_days_kept(self):
        self.assertEqual(len(R.dedupe([ev(), ev(start="2026-10-21T19:00:00-04:00")])), 2)

    def test_id_is_stable_and_case_insensitive(self):
        self.assertEqual(R.make_id("The Drift", "2026-10-08T19:00", "Tavern 29"),
                         R.make_id("the drift", "2026-10-08", "TAVERN 29"))

    def test_merge_keeps_first_seen_and_carries_after_two_misses(self):
        old = ev(title="Old show")
        old["first_seen"] = "2026-09-01"
        old2 = ev(title="Gone show")
        prev = [old, old2]
        fresh = [ev(title="Old show")]
        out = {e["title"]: e for e in R.merge(prev, fresh, TODAY, {"x"})}
        self.assertEqual(out["Old show"]["first_seen"], "2026-09-01")
        self.assertEqual(out["Gone show"]["confidence"], "verified")  # one miss
        out2 = {e["title"]: e for e in R.merge(list(out.values()), fresh, TODAY, {"x"})}
        self.assertEqual(out2["Gone show"]["confidence"], "carried")  # two misses, kept

    def test_failed_source_does_not_count_as_miss(self):
        prev = [ev(title="Kept")]
        out = R.merge(prev, [], TODAY, ok_sources=set())
        self.assertEqual(out[0].get("missed_runs", 0), 0)


class Recurring(unittest.TestCase):
    def test_first_friday_except_jan_and_sep(self):
        rule = {"title": "First Fri", "category": "museums", "url": "https://x.org",
                "days": ["fri"], "nth": [1], "except_months": [1, 9], "time": "17:30"}
        hits = [d for d in (date(2026, 8, 1) + R.timedelta(days=i) for i in range(365))
                if R.rule_matches(rule, d)]
        months = [d.month for d in hits]
        self.assertNotIn(1, months)
        self.assertNotIn(9, months)
        self.assertEqual(len(hits), 10)
        self.assertTrue(all(d.weekday() == 4 and d.day <= 7 for d in hits))
        self.assertIn(date(2026, 10, 2), hits)
        self.assertIn(date(2026, 11, 6), hits)

    def test_expand_window_and_times(self):
        rule = {"title": "Whitney", "category": "museums", "url": "https://x.org",
                "days": ["fri"], "time": "17:00", "end": "22:00"}
        out = R.expand_recurring([rule], TODAY, days=30)
        self.assertEqual([e["start"][:10] for e in out],
                         ["2026-10-09", "2026-10-16", "2026-10-23", "2026-10-30", "2026-11-06"])
        self.assertEqual(out[0]["start"], "2026-10-09T17:00:00-04:00")
        self.assertEqual(out[-1]["start"], "2026-11-06T17:00:00-05:00")  # after DST ends
        self.assertTrue(all(e["confidence"] == "carried" and e["recurring"] for e in out))

    def test_no_time_means_all_day(self):
        out = R.expand_recurring([{"title": "Django", "category": "jazz_music",
                                   "url": "https://x.org"}], TODAY, days=3)
        self.assertEqual(len(out), 3)
        self.assertTrue(all(e["all_day"] and "T" not in e["start"] for e in out))

    def test_until_and_monthday(self):
        segue = {"title": "Segue", "category": "books_talks", "url": "https://x.org",
                 "days": ["sat"], "time": "17:00", "until": "2026-10-20"}
        self.assertEqual(len(R.expand_recurring([segue], TODAY)), 2)
        onsale = {"title": "Notables", "category": "opera_classical", "url": "https://x.org",
                  "monthday": 1, "time": "11:00", "kind": "on_sale"}
        out = R.expand_recurring([onsale], TODAY, days=60)
        self.assertEqual([e["start"][:10] for e in out], ["2026-11-01", "2026-12-01"])
        self.assertEqual(out[0]["kind"], "on_sale")

    def test_late_end_rolls_to_next_day(self):
        rule = {"title": "Late", "category": "nightlife", "url": "https://x.org",
                "days": ["fri"], "time": "22:00", "end": "02:00"}
        e = R.expand_recurring([rule], TODAY, days=2)[0]
        self.assertEqual(e["end"], "2026-10-10T02:00:00-04:00")


class DST(unittest.TestCase):
    def test_utc_inputs_convert_on_both_sides_of_nov_1(self):
        before, _ = R.parse_dt("2026-10-30T23:00:00Z")
        after, _ = R.parse_dt("2026-11-06T00:00:00Z")
        self.assertEqual(R.iso(before), "2026-10-30T19:00:00-04:00")
        self.assertEqual(R.iso(after), "2026-11-05T19:00:00-05:00")

    def test_naive_local_time_gets_correct_offset(self):
        self.assertEqual(R.iso(R.parse_dt("2026-11-01 00:30:00")[0]), "2026-11-01T00:30:00-04:00")
        self.assertEqual(R.iso(R.parse_dt("2026-11-01T12:00:00")[0]), "2026-11-01T12:00:00-05:00")

    def test_ics_tzid_and_utc(self):
        text = ("BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nSUMMARY:A\\, b\r\n"
                "DTSTART;TZID=America/New_York:20261105T190000\r\nEND:VEVENT\r\n"
                "BEGIN:VEVENT\r\nSUMMARY:Long fold\r\n  continued\r\nDTSTART:20261030T230000Z\r\n"
                "END:VEVENT\r\nBEGIN:VEVENT\r\nSUMMARY:Fair\r\nDTSTART;VALUE=DATE:20261104\r\n"
                "DTEND;VALUE=DATE:20261109\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n")
        a, b, c = R.ics_events(text)
        self.assertEqual(a["title"], "A, b")
        self.assertEqual(R.iso(a["start"]), "2026-11-05T19:00:00-05:00")
        self.assertEqual(b["title"], "Long fold continued")
        self.assertEqual(R.iso(b["start"]), "2026-10-30T19:00:00-04:00")
        self.assertTrue(c["all_day"])
        self.assertEqual(c["multi_day_end"], "2026-11-08")

    def test_calendar_ics_uses_utc_and_stable_uids(self):
        evs = [ev(start="2026-10-30T19:00:00-04:00"), ev(title="B", start="2026-11-05T19:00:00-05:00")]
        out = R.to_ics(evs, TODAY, R.datetime(2026, 10, 8, 12, tzinfo=R.ZoneInfo("UTC")))
        self.assertIn("DTSTART:20261030T230000Z", out)
        self.assertIn("DTSTART:20261106T000000Z", out)
        self.assertEqual(out.count(f"UID:{evs[0]['id']}@nyc-culture"), 1)
        self.assertTrue(all(len(l.encode()) <= 75 for l in out.split("\r\n")))


class GoogleCalendarLink(unittest.TestCase):
    def test_before_and_after_dst_use_local_wall_time(self):
        p1 = gcal_params(ev(start="2026-10-30T19:00:00-04:00"))
        p2 = gcal_params(ev(start="2026-11-05T19:00:00-05:00", end="2026-11-05T21:30:00-05:00"))
        self.assertEqual(p1["dates"], "20261030T190000/20261030T210000")
        self.assertEqual(p2["dates"], "20261105T190000/20261105T213000")
        self.assertEqual(p1["ctz"], "America/New_York")

    def test_all_day_end_is_exclusive(self):
        self.assertEqual(gcal_params(ev(start="2026-11-04", multi_day_end="2026-11-08"))["dates"],
                         "20261104/20261109")
        self.assertEqual(gcal_params(ev(start="2026-10-10"))["dates"], "20261010/20261011")

    def test_every_parameter_is_encoded(self):
        e = ev(title="Rock & Roll #1 / 50% off?", why="A, b & c", address="1 Main St, NY")
        url = e["gcal_url"]
        query = url.split("?", 1)[1]
        self.assertNotIn(" ", url)
        self.assertIn("text=Rock%20%26%20Roll%20%231%20%2F%2050%25%20off%3F", query)
        p = gcal_params(e)
        self.assertEqual(p["text"], "Rock & Roll #1 / 50% off?")
        self.assertEqual(p["details"], "A, b & c https://example.org/e")
        self.assertEqual(p["location"], "1 Main St, NY")


class DateTextGuard(unittest.TestCase):
    PAGE = "Opening reception Thursday, October 8, 6-8pm. Artist talk Oct. 15. Closing 22 October."

    def test_present_forms(self):
        for d in ("2026-10-08T18:00:00-04:00", "2026-10-15", "2026-10-22"):
            self.assertTrue(R.date_text_present(d, self.PAGE), d)

    def test_absent_and_prefix_collisions(self):
        self.assertFalse(R.date_text_present("2026-10-01", self.PAGE))  # 'Oct. 15' is not Oct 1
        self.assertFalse(R.date_text_present("2026-10-18", "October 1 and 8"))
        self.assertFalse(R.date_text_present("2026-11-08", self.PAGE))

    def test_numeric_forms(self):
        self.assertTrue(R.date_text_present("2026-10-08", "Thu 10/8 at 7"))
        self.assertFalse(R.date_text_present("2026-10-08", "Thu 10/18 at 7"))
        self.assertTrue(R.date_text_present("2026-10-08", "2026-10-08"))


class EntryAndPrice(unittest.TestCase):
    def test_entry_and_price(self):
        self.assertEqual(ev(cost_tier="free", cost_text="Free")["entry"], "walk_in")
        self.assertEqual(ev(cost_tier="free", rsvp_required=True)["entry"], "rsvp")
        e = ev(cost_tier="per_ticket", cost_text="$50 to $110")
        self.assertEqual((e["entry"], e["price_min"]), ("ticket", 50))
        self.assertEqual(ev(cost_text="$20 door, free for print subscribers", cost_tier="per_ticket")["price_min"], 20)
        self.assertIsNone(ev()["price_min"])
        self.assertIsNone(ev()["entry"])


class Cards(unittest.TestCase):
    CARD = """<article><h3><a href="/event/1/liederabend"><span>Liederabend</span></a></h3>
      <div class="field--name-field-venue">Paul Hall</div>
      <time datetime="{dt}">{shown}</time><div>Free</div></article>"""

    def test_local_time_mislabeled_as_utc(self):
        (e,) = R.card_events(self.CARD.format(dt="2026-10-14T18:00:00Z", shown="6:00PM"), "https://x.edu/cal")
        self.assertEqual(R.iso(e["start"]), "2026-10-14T18:00:00-04:00")
        self.assertEqual((e["venue"], e["cost"], e["url"]), ("Paul Hall", "Free", "https://x.edu/event/1/liederabend"))

    def test_real_utc_kept(self):
        (e,) = R.card_events(self.CARD.format(dt="2026-11-05T00:00:00Z", shown="7:00 PM"), "https://x.edu/cal")
        self.assertEqual(R.iso(e["start"]), "2026-11-04T19:00:00-05:00")


class JsonLd(unittest.TestCase):
    def test_extracts_nested_events_and_free(self):
        html = """<script type="application/ld+json">{"@graph":[{"@type":"MusicEvent",
          "name":"Free Pop-Up &amp; Talk","startDate":"2026-10-21T18:00:00-04:00",
          "location":{"@type":"Place","name":"Miller Theatre","address":{"streetAddress":
          "2960 Broadway","addressLocality":"New York","postalCode":"10027"}},
          "isAccessibleForFree":true,"url":"https://m.org/e"}]}</script>
          <script type="application/ld+json">not json</script>"""
        (e,) = R.jsonld_events(html)
        self.assertEqual(e["title"], "Free Pop-Up & Talk")
        self.assertEqual(e["cost"], "Free")
        src = {"id": "m", "url": "https://m.org", "category": "opera_classical"}
        out = R.from_raw(e, src, TODAY)
        self.assertEqual(out["neighborhood"], "Morningside Heights")
        self.assertEqual(out["cost_tier"], "free")


class Schema(unittest.TestCase):
    def test_offline_output_validates_and_has_no_past_events(self):
        try:
            import jsonschema
        except ImportError:
            self.skipTest("jsonschema not installed")
        with tempfile.TemporaryDirectory() as tmp:
            R.run(offline=True, today=TODAY, out=Path(tmp))
            data = json.loads((Path(tmp) / "events.json").read_text(encoding="utf-8"))
        schema = json.loads((ROOT / "schema" / "events.schema.json").read_text())
        jsonschema.validate(data, schema)
        self.assertTrue(all(R.last_day(e) >= TODAY for e in data["events"]))


if __name__ == "__main__":
    unittest.main()
