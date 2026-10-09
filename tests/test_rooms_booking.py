import json, os, sys, unittest
from datetime import date, datetime, timedelta
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gsb_client as g
from fakes import FakeSite, next_weekday, TZ


def client():
    site = FakeSite()
    return site.install(g.GSBClient()), site


class Rooms(unittest.TestCase):
    def test_free_window(self):
        c, _ = client()
        d = next_weekday(1)
        free = [x["room"] for x in c.free_rooms(d, "13:00", "14:00")["rooms"]]
        self.assertNotIn("Geffen 504 - The Chrin Family Study Room", free)   # booked 12:00-14:30
        self.assertIn("Geffen 506 - The Chrin Family Study Room", free)
        self.assertNotIn("Geffen 742", free)                                  # CMC hidden by default
        self.assertIn("Geffen 742", [x["room"] for x in c.free_rooms(d, "13:00", "14:00", room_type="Interview")["rooms"]])

    def test_filters(self):
        c, _ = client()
        d = next_weekday(1)
        self.assertEqual({x["type"] for x in c.free_rooms(d, min_capacity=6)["rooms"]}, {"Study/Breakout Room"})
        self.assertEqual([x["room"] for x in c.free_rooms(d, room_type="Phone")["rooms"]], ["Geffen 481"])

    def test_next_weeks(self):
        c, _ = client()
        for days in (7, 14):
            with self.subTest(days=days):
                self.assertGreater(c.free_rooms(next_weekday(days))["count"], 0)

    def test_closed_day_note_and_past(self):
        c, _ = client()
        sat = datetime.now(TZ).date() + timedelta(days=1)
        while sat.weekday() != 5:
            sat += timedelta(days=1)
        r = c.free_rooms(sat)
        self.assertEqual(r["count"], 0)
        self.assertIn("hasn't opened", r["note"])
        with self.assertRaises(ValueError):
            c.free_rooms(datetime.now(TZ).date() - timedelta(days=1))
        self.assertIn("note_booking", c.free_rooms(next_weekday(20)))

    def test_never_leaks_other_names(self):
        c, _ = client()
        self.assertNotIn("Some Student", json.dumps(c.free_rooms(next_weekday(1))))


class Booking(unittest.TestCase):
    def test_book_and_cancel(self):
        c, site = client()
        d = next_weekday(1)
        r = c.book_room("Geffen 504", d, "15:00", "Study", end="16:00")
        self.assertTrue(r["booked"])
        path, data = site.posts[-1]
        self.assertEqual(path, "/room_reservation_form")
        self.assertEqual({k: data[k] for k in ("_csrf", "update", "room", "start_hour", "start_minute", "start_ampm", "duration", "name")},
                         {"_csrf": "FORMTOKEN", "update": "1", "room": "648", "start_hour": "03", "start_minute": "00",
                          "start_ampm": "PM", "duration": "60", "name": "Study"})
        self.assertEqual(data["start"], d.strftime("%d %b %y"))
        mine = c.my_reservations()
        self.assertEqual(len(mine), 1)
        self.assertEqual(mine[0]["title"], "Study")
        out = c.cancel_booking(mine[0]["id"])
        self.assertTrue(out["cancelled"])
        self.assertEqual(site.posts[-1], ("/room_reservation_details", {"id": mine[0]["id"], "action": "cancel", "_csrf": "CANCELTOKEN"}))
        self.assertEqual(c.my_reservations(), [])
        with self.assertRaises(KeyError):
            c.cancel_booking(mine[0]["id"])

    def test_refusals(self):
        c, _ = client()
        d = next_weekday(1)
        c.book_room("504", d, "15:00", "Study", end="16:00")
        bad = {
            "overlaps my booking": lambda: c.book_room("504", d, "15:30", "x", end="16:30"),
            "someone else's": lambda: c.book_room("504", d, "13:00", "x", end="14:00"),
            "not open": lambda: c.book_room("504", d, "05:00", "x", end="06:00"),
            "ambiguous": lambda: c.book_room("Geffen", d, "17:00", "x", end="18:00"),
            "no title": lambda: c.book_room("504", d, "17:00", "", end="18:00"),
            "too long": lambda: c.book_room("504", d, "17:00", "x", end="22:00"),
            "odd minute": lambda: c.book_room("504", d, "17:07", "x", end="18:07"),
            "past": lambda: c.book_room("504", datetime.now(TZ).date() - timedelta(days=1), "10:00", "x", end="11:00"),
        }
        for label, f in bad.items():
            with self.subTest(label):
                with self.assertRaises(ValueError):
                    f()


if __name__ == "__main__":
    unittest.main()
