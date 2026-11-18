"""cookie_header(): turns the saved session into a Cookie header for Claude on the web."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from gsb_client import SessionExpired, cookie_header  # noqa: E402


def session(cookies):
    fd, path = tempfile.mkstemp(suffix=".json")
    with os.fdopen(fd, "w") as f:
        json.dump({"cookies": cookies}, f)
    return Path(path)


class CookieHeaderTests(unittest.TestCase):
    def test_keeps_only_cookies_sent_to_campusgroups(self):
        p = session([
            {"name": "CG.SessionID", "value": "abc", "domain": "groups.gsb.columbia.edu"},
            {"name": "shared", "value": "1", "domain": ".columbia.edu"},
            {"name": "sso", "value": "x", "domain": "cas.columbia.edu"},
            {"name": "duo", "value": "y", "domain": ".duosecurity.com"},
        ])
        self.assertEqual(cookie_header(session_file=p), "CG.SessionID=abc; shared=1")

    def test_missing_file_means_not_logged_in(self):
        with self.assertRaises(SessionExpired):
            cookie_header(session_file=Path(tempfile.gettempdir()) / "no-such-session.json")

    def test_no_matching_cookies_means_not_logged_in(self):
        with self.assertRaises(SessionExpired):
            cookie_header(session_file=session([{"name": "sso", "value": "x", "domain": "cas.columbia.edu"}]))


if __name__ == "__main__":
    unittest.main()
