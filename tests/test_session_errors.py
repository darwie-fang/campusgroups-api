"""A busy CampusGroups (error page, empty reply) must not be reported as 'logged out'."""
import os, sys, unittest
from unittest import mock
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import gsb_client as g


class R:
    def __init__(self, status=200, text="[]", location=""):
        self.status_code, self.text, self.headers = status, text, {"Location": location}


class FakeSession:
    def __init__(self, replies):
        self.replies = list(replies)
    def get(self, *a, **k):
        return self.replies.pop(0)


def client(replies):
    c = g.GSBClient()
    c._s = FakeSession(replies)
    return c


class SessionErrors(unittest.TestCase):
    def setUp(self):
        self.sleep = mock.patch.object(g.time, "sleep", lambda s: None)
        self.sleep.start()
    def tearDown(self):
        self.sleep.stop()

    def test_hiccup_then_ok(self):
        self.assertEqual(client([R(503, "<html>busy</html>"), R(200, '[{"a": 1}]')])._get("/x"), [{"a": 1}])

    def test_persistent_error_is_not_logout(self):
        with self.assertRaises(RuntimeError):
            client([R(500, "oops"), R(200, "")])._get("/x")

    def test_login_redirect_is_logout(self):
        with self.assertRaises(g.SessionExpired):
            client([R(302, "", "https://groups.gsb.columbia.edu/auth/login")])._get("/x")
        with self.assertRaises(g.SessionExpired):
            client([R(401, "")])._get("/x")


if __name__ == "__main__":
    unittest.main()
