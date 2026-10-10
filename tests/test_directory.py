"""find_person_email: one named person at a time, never lists of emails."""
import json, os, sys, tempfile, unittest
from pathlib import Path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gsb_client as g
from fakes import FakeSite


class Directory(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_log = g.GSBClient.LOOKUP_LOG
        g.GSBClient.LOOKUP_LOG = Path(self.tmp.name, "lookups.json")
        self.site = FakeSite()
        self.c = self.site.install(g.GSBClient())

    def tearDown(self):
        g.GSBClient.LOOKUP_LOG = self.old_log
        self.tmp.cleanup()

    def test_exact_match(self):
        r = self.c.find_person_email("Sam Davies")
        self.assertEqual((r["found"], r["email"], r["class"]), (True, "sdavies28@example.edu", "Class of 2028"))
        self.assertTrue(self.c.find_person_email("sam davies")["found"])

    def test_same_name_asks_which_without_emails(self):
        r = self.c.find_person_email("Pat Lee")
        self.assertFalse(r["found"])
        self.assertEqual(len(r["candidates"]), 2)
        self.assertNotIn("@", json.dumps(r))
        self.assertEqual(self.c.find_person_email("Pat Lee", class_year="2027")["email"], "plee27@example.edu")

    def test_no_exact_match_never_returns_emails(self):
        r = self.c.find_person_email("Sam Davi")
        self.assertFalse(r["found"])
        self.assertNotIn("@", json.dumps(r))

    def test_needs_first_and_last_name(self):
        for bad in ["Sam", "", "a b", "S Davies", "*", "@gsb"]:
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    self.c.find_person_email(bad)

    def test_no_email_shared(self):
        r = self.c.find_person_email("Robin Noemail")
        self.assertFalse(r["found"])
        self.assertIn("hasn't shared", r["message"])

    def test_rate_limit_counts_distinct_people(self):
        for _ in range(30):
            self.c.find_person_email("Sam Davies")        # same person: counted once
        for i in range(g.GSBClient.LOOKUP_LIMIT_PER_HOUR - 1):
            self.c.find_person_email(f"Person Number{chr(97 + i)}x")
        with self.assertRaises(ValueError):
            self.c.find_person_email("One Toomany")
        self.assertTrue(self.c.find_person_email("Sam Davies")["found"])   # already counted
        log = g.GSBClient.LOOKUP_LOG.read_text()
        self.assertNotIn("davies", log.lower())                          # names never stored

    def test_status_reports_directory_login(self):
        self.assertEqual(self.c.status(), {"logged_in": True, "directory": True})
        self.site.mycbs_logged_in = False
        self.assertEqual(self.c.status()["directory"], False)
        with self.assertRaises(g.SessionExpired):
            self.c.find_person_email("Sam Davies")


if __name__ == "__main__":
    unittest.main()
