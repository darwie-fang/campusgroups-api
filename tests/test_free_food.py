"""Free food finder: CampusGroups' "Food Provided" flag and food words in titles.
Made-up events only."""
import os, sys, unittest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gsb_client as g
from fakes import FakeSite


def signed_out(*a, **k):
    raise g.SessionExpired("12twenty signed out")


class FreeFood(unittest.TestCase):
    def setUp(self):
        self.site = FakeSite()
        self.c = self.site.install(g.GSBClient())
        self.c._tw = signed_out          # must never touch 12twenty

    def test_campusgroups_flag_and_title_words(self):
        r = self.c.free_food(self.site.d1, self.site.d2)
        got = {e["title"]: e["confidence"] for e in r["events"]}
        self.assertEqual(got, {"Tech Mixer": "high",              # organiser ticked Food Provided
                               "Vibe Coding Dinner": "low"})      # only the title says so
        self.assertNotIn("Exec Ed Day", got)
        self.assertTrue(all(e["source"] == "CampusGroups" for e in r["events"]))

    def test_flags_are_cached(self):
        self.c.free_food(self.site.d1, self.site.d2)
        n = len(self.site.event_pages)
        self.c.free_food(self.site.d1, self.site.d2)
        self.assertEqual(len(self.site.event_pages), n)

    def test_private_page_doesnt_break_search(self):
        self.site.private_events = {"0"}
        r = self.c.free_food(self.site.d1, self.site.d2)
        self.assertEqual([e["title"] for e in r["events"]], ["Vibe Coding Dinner", "Tech Mixer"] if
                         r["events"][0]["title"] == "Vibe Coding Dinner" else ["Tech Mixer", "Vibe Coding Dinner"])
        self.assertIn("couldn't be checked", r["note"])

    def test_range_limit(self):
        from datetime import timedelta
        with self.assertRaises(ValueError):
            self.c.free_food(self.site.d1, self.site.d1 + timedelta(days=10))


if __name__ == "__main__":
    unittest.main()
