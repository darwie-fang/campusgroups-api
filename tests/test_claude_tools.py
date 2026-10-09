"""The Claude connection (MCP server): protocol, tool list, and the confirm guard."""
import io, json, os, runpy, sys, unittest
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT); sys.path.insert(0, HERE)
import gsb_client as g
from fakes import FakeSite, next_weekday


def run_server(messages, site):
    orig_init = g.GSBClient.__init__
    def patched(self, *a, **k):
        orig_init(self, *a, **k)
        site.install(self)
    g.GSBClient.__init__ = patched
    stdin, stdout = sys.stdin, sys.stdout
    sys.stdin = io.StringIO("".join(json.dumps(m) + "\n" for m in messages))
    sys.stdout = out = io.StringIO()
    try:
        runpy.run_path(os.path.join(ROOT, "mcp_server.py"), run_name="__main__")
    finally:
        sys.stdin, sys.stdout = stdin, stdout
        g.GSBClient.__init__ = orig_init
    return {m["id"]: m for m in map(json.loads, out.getvalue().splitlines())}


def call(i, name, **args):
    return {"jsonrpc": "2.0", "id": i, "method": "tools/call", "params": {"name": name, "arguments": args}}


class ClaudeTools(unittest.TestCase):
    def test_protocol_and_booking_guard(self):
        site = FakeSite()
        d = str(next_weekday(1))
        booking = dict(room="Geffen 504", date=d, start="15:00", end="16:00", title="Study")
        res = run_server([
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            call(3, "book_room", confirmed=False, **booking),
            call(4, "book_room", confirmed=True, **booking),
            call(5, "my_room_reservations"),
            call(6, "cancel_booking", reservation_id="425659"),
            call(7, "cancel_booking", reservation_id="425659", confirmed=True),
            call(8, "get_club_events", club="underwater basket weaving"),
            {"jsonrpc": "2.0", "id": 9, "method": "nope"},
        ], site)
        self.assertEqual(res[1]["result"]["protocolVersion"], "2025-06-18")
        tools = {t["name"]: t for t in res[2]["result"]["tools"]}
        self.assertEqual(set(tools), {"list_my_clubs", "get_club_events", "get_all_events", "find_free_rooms",
                                      "my_room_reservations", "book_room", "cancel_booking"})
        self.assertIn("Never guess", tools["book_room"]["description"])
        self.assertTrue(res[3]["result"]["isError"])                 # not confirmed -> refused
        self.assertEqual(len([p for p in site.posts if p[0] == "/room_reservation_form"]), 1)   # only the confirmed one
        self.assertIn('"booked": true', res[4]["result"]["content"][0]["text"])
        self.assertIn("425659", res[5]["result"]["content"][0]["text"])
        self.assertTrue(res[6]["result"]["isError"])                 # cancel without confirm -> refused
        self.assertIn('"cancelled": true', res[7]["result"]["content"][0]["text"])
        self.assertTrue(res[8]["result"]["isError"])
        self.assertEqual(res[9]["error"]["code"], -32601)
        self.assertNotIn(2.5, res)                                    # notifications get no reply

    def test_not_logged_in_message(self):
        site = FakeSite(); site.logged_in = False
        res = run_server([call(1, "list_my_clubs")], site)
        text = res[1]["result"]["content"][0]["text"]
        self.assertIn("setup.sh", text)
        self.assertTrue(res[1]["result"]["isError"])


if __name__ == "__main__":
    unittest.main()
