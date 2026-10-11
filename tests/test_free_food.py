"""Free food finder: CampusGroups' "Food Provided" flag, food words in titles, and
12twenty descriptions. Made-up events only."""
import os, sys, unittest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gsb_client as g
from fakes import FakeSite
from test_career_events import FakeEvents


def signed_out(*a, **k):
    raise g.SessionExpired("12twenty signed out")


class FreeFood(unittest.TestCase):
    def setUp(self):
        self.site = FakeSite()
        self.c = self.site.install(g.GSBClient())
        self.c._tw = signed_out

    def test_campusgroups_flag_and_title_words(self):
        r = self.c.free_food(self.site.d1, self.site.d2)
        got = {e["title"]: e["confidence"] for e in r["events"]}
        self.assertEqual(got, {"Tech Mixer": "high",              # organiser ticked Food Provided
                               "Vibe Coding Dinner": "low"})      # only the title says so
        self.assertNotIn("Exec Ed Day", got)
        self.assertIn("12twenty", r["notes"][0])                  # says what it couldn't check

    def test_flags_are_cached(self):
        self.c.free_food(self.site.d1, self.site.d2)
        n = len(self.site.event_pages)
        self.c.free_food(self.site.d1, self.site.d2)
        self.assertEqual(len(self.site.event_pages), n)

    def test_12twenty_in_person_food_only(self):
        ev = FakeEvents()
        ev.day = self.site.d1
        ev.events[301].update({"Location": "Uris Hall 301", "EventFormatId": 1,
                               "Description": "<p>Join us; a networking <b>reception</b> follows.</p>"})
        ev.events[303].update({"Description": "Lunch provided!"})        # but it's virtual
        ev.events[301]["StartDate"] = ev.events[301]["StartDate"].replace(
            ev.events[301]["StartDate"][:10], self.site.d1.isoformat())
        ev.events[303]["StartDate"] = ev.events[303]["StartDate"].replace(
            ev.events[303]["StartDate"][:10], self.site.d1.isoformat())
        self.c._tw = ev.tw
        r = self.c.free_food(self.site.d1, self.site.d2)
        tw = [e for e in r["events"] if e["source"] == "12twenty"]
        self.assertEqual([(e["host"], e["food"]) for e in tw], [("Acme Capital", "description mentions 'reception'")])
        self.assertIn("reception", tw[0]["snippet"])
        self.assertNotIn("notes", r)

    def test_range_limit(self):
        from datetime import timedelta
        with self.assertRaises(ValueError):
            self.c.free_food(self.site.d1, self.site.d1 + timedelta(days=10))


if __name__ == "__main__":
    unittest.main()
