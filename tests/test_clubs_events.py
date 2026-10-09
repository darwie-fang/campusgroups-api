import os, sys, unittest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gsb_client as g
from fakes import FakeSite


def client(site=None):
    site = site or FakeSite()
    return site.install(g.GSBClient()), site


class Clubs(unittest.TestCase):
    def test_my_clubs_include_most_recent(self):
        c, _ = client()
        names = [x["name"] for x in c.my_clubs()]
        self.assertIn("Technology Club", names)          # only in "Most Recent"
        self.assertIn("Golf Club", names)
        self.assertIn("Gaming & Esports Club", names)    # HTML entity decoded

    def test_resolve_names(self):
        c, _ = client()
        cases = {"ai club": "Artificial Intelligence Club",   # not a member: found via events feed
                 "AI Club": "Artificial Intelligence Club",
                 "aba": "Asian Business Association",
                 "tech": "Technology Club", "tech club": "Technology Club",
                 "fintech": "FinTech and Blockchain Club",
                 "golf": "Golf Club", "esports": "Gaming & Esports Club", "gaming": "Gaming & Esports Club",
                 "vc club": "Venture Capital Club", "venture capital": "Venture Capital Club",
                 "ski": "Snow Sports Club", "snow": "Snow Sports Club"}
        for q, want in cases.items():
            with self.subTest(q=q):
                self.assertEqual(c.resolve_club(q)["name"], want)

    def test_regressions_no_loose_guess(self):
        c, _ = client()
        self.assertNotEqual(c.resolve_club("ai club")["name"], "Golf Club")        # Oct 8 bug
        self.assertNotEqual(c.resolve_club("tech club")["name"], "FinTech and Blockchain Club")
        for q in ["underwater basket weaving", "club"]:
            with self.assertRaises(KeyError):
                c.resolve_club(q)

    def test_resolve_by_id(self):
        c, _ = client()
        self.assertEqual(c.resolve_club("24090")["name"], "Golf Club")
        self.assertEqual(c.resolve_club("99999")["id"], 99999)


class Events(unittest.TestCase):
    def test_club_events(self):
        c, site = client()
        r = c.club_events("ai club", site.d1.isoformat(), site.d2.isoformat())
        self.assertEqual(len(r["events"]), 1)
        e = r["events"][0]
        self.assertEqual(e["title"], "Vibe Coding Dinner")
        self.assertTrue(e["start"].startswith(site.d1.isoformat() + "T18:00"))
        self.assertEqual(e["tags"], ["Social", "Artificial Intelligence"])
        self.assertNotIn("<", e["when"])

    def test_all_events_filters(self):
        c, site = client()
        r = c.all_events(site.d1.isoformat(), site.d2.isoformat())
        self.assertEqual(r["count"], 3)
        self.assertEqual(c.all_events(site.d1.isoformat(), site.d2.isoformat(), registered_only=True)["count"], 1)
        self.assertEqual(c.all_events(site.d1.isoformat(), site.d2.isoformat(), club="tech")["count"], 1)
        self.assertEqual(c.all_events(site.d1.isoformat(), site.d1.isoformat())["count"], 2)

    def test_session_expired(self):
        site = FakeSite(); site.logged_in = False
        c, _ = client(site)
        with self.assertRaises(g.SessionExpired):
            c.my_clubs()


if __name__ == "__main__":
    unittest.main()
