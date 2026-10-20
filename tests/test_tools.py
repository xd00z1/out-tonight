import sys
import unittest
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import alerts  # noqa: E402
import apply_request  # noqa: E402

BODY = """### Alert name

Big bands

### Words to match

big band|jazz orchestra

### Venue

_No response_

### Category

jazz_music

### Free only

- [ ] Free events only"""


class IssueForms(unittest.TestCase):
    def test_fields_parse(self):
        f = apply_request.fields(BODY)
        self.assertEqual(f["alert name"], "Big bands")
        self.assertEqual(f["words to match"], "big band|jazz orchestra")
        self.assertEqual(f["venue"], "")
        self.assertEqual(f["category"], "jazz_music")

    def test_slug(self):
        self.assertEqual(apply_request.slug("Joe's Pub & Bar"), "joe_s_pub_bar")


class Alerts(unittest.TestCase):
    EV = [
        {"id": "a", "title": "Mike LeDonne Big Band", "venue": "Dizzy's Club", "category": "jazz_music",
         "start": "2026-10-26T19:00:00-04:00", "url": "u", "price_min": 24},
        {"id": "b", "title": "Axel Tosca's Cuban Jam", "venue": "Zinc Bar", "category": "jazz_music",
         "start": "2026-10-22", "url": "u", "price_min": None},
        {"id": "c", "title": "Tosca", "venue": "Metropolitan Opera", "category": "opera_classical",
         "start": "2026-11-01T15:00:00-05:00", "url": "u", "price_min": None},
        {"id": "d", "title": "Gallery talk", "venue": "The Frick Collection", "category": "museums",
         "start": "2026-10-22T15:00:00-04:00", "url": "u", "price_min": 0},
    ]

    def test_rules(self):
        self.assertTrue(alerts.matches({"text": "big band"}, self.EV[0]))
        tosca = {"text": r"\btosca\b", "category": "opera_classical"}
        self.assertFalse(alerts.matches(tosca, self.EV[1]))  # Axel Tosca, wrong category
        self.assertTrue(alerts.matches(tosca, self.EV[2]))
        self.assertTrue(alerts.matches({"venue": "frick", "free": True}, self.EV[3]))
        self.assertFalse(alerts.matches({"name": "empty rule"}, self.EV[3]))

    def test_digest_skips_already_sent(self):
        body, ids = alerts.digest(self.EV, [{"name": "Jazz", "category": "jazz_music"}], {"a"}, date(2026, 10, 20))
        self.assertEqual(ids, ["b"])
        self.assertIn("### Jazz", body)


if __name__ == "__main__":
    unittest.main()
