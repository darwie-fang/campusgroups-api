"""12twenty appointments and ACE Book matching, against a pretend 12twenty and a
made-up ACE Book (no real people)."""
import json, os, sys, unittest
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gsb_client as g
import ace_book
from fakes import next_weekday, TZ

UTC = ZoneInfo("UTC")
ME = 540016000000001


def utc(d, h, m=0):
    return datetime(d.year, d.month, d.day, h, m, tzinfo=TZ).astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S")


class Fake12twenty:
    def __init__(self):
        self.day = next_weekday(3)
        self.blocks = [  # (block id, adviser id, name, local start hour, local end hour)
            (2001, 9001, "Pat Fellow", 14, 16),
            (2002, 9002, "Robin Coach", 10, 11),
            (2003, 9003, "Alex Fellowson", 18, 19),
        ]
        self.appts = []      # {id, adviser_id, name, start, end, status, topic}
        self.calls = []

    def tw(self, method, path, body=None):
        self.calls.append((method, path, body))
        if path == "/api/v2/account/current-user":
            return {"Id": ME, "FirstName": "Test"}
        if path == "/api/v2/appointment-calendar-events/post-query":
            assert body["StudentId"] == ME
            items = [{"EntityId": bid, "TypeId": 2, "StartTime": utc(self.day, s), "EndTime": utc(self.day, e),
                      "AdviserId": aid, "AdviserName": n, "PreferredAppointmentTypeName": None,
                      "MinAvailableBookingTime": utc(datetime.now(TZ).date(), 0), "HasMaxAppointmentsReached": False}
                     for bid, aid, n, s, e in self.blocks]
            items += [{"EntityId": a["id"], "TypeId": 1, "StartTime": a["start"], "EndTime": a["end"],
                       "AdviserId": a["adviser_id"], "AdviserName": a["name"], "Location": "https://x/virtual-meetings/join/1?token=SECRET",
                       "PreferredAppointmentTypeName": "CMC Fellow Appointment"} for a in self.appts if a["status"] == "Booked"]
            return {"Total": len(items), "Items": items}
        if path.startswith("/api/v2/appointment-blocks/"):
            bid = int(path.rsplit("/", 1)[1])
            b = next(x for x in self.blocks if x[0] == bid)
            return {"Id": bid, "TimeZoneId": 2, "Adviser": {"Id": b[1], "AdviserId": b[1], "AdviserName": b[2]},
                    "AvailableAppointmentTypeIds": [10004401017599], "AvailableAppointmentTypeNames": "CMC Fellow Appointment"}
        if path == "/api/v2/appointments" and method == "POST":
            a = {"id": 220000000000001 + len(self.appts), "adviser_id": body["AdviserId"], "start": body["StartTime"],
                 "end": body["EndTime"].replace(".000Z", ""), "status": "Booked", "topic": body["Topic"],
                 "name": next(x[2] for x in self.blocks if x[1] == body["AdviserId"])}
            self.appts.append(a)
            return {"Id": a["id"]}
        if path.endswith("/cancel") and method == "PUT":
            aid = int(path.split("/")[-2])
            for a in self.appts:
                if a["id"] == aid:
                    a["status"] = "Canceled"
            return None
        if path.startswith("/api/v2/appointments/") and method == "GET":
            aid = int(path.rsplit("/", 1)[1])
            a = next(x for x in self.appts if x["id"] == aid)
            return {"Id": aid, "StatusName": a["status"]}
        raise AssertionError(f"unexpected call {method} {path}")


class Appointments(unittest.TestCase):
    def setUp(self):
        self.site = Fake12twenty()
        self.c = g.GSBClient()
        self.c._tw = self.site.tw
        self.d = self.site.day

    def test_slots_in_new_york_time(self):
        r = self.c.appointment_slots(self.d, self.d)
        self.assertEqual([(s["adviser"], s["from"], s["until"]) for s in r["slots"]],
                         [("Robin Coach", "10:00", "11:00"), ("Pat Fellow", "14:00", "16:00"), ("Alex Fellowson", "18:00", "19:00")])
        self.assertEqual([s["adviser"] for s in self.c.appointment_slots(self.d, self.d, adviser="pat")["slots"]], ["Pat Fellow"])

    def test_book_list_cancel(self):
        r = self.c.book_appointment("Pat Fellow", self.d, "14:30", "Recruiting for PE", 30)
        self.assertTrue(r["booked"])
        method, path, body = [c for c in self.site.calls if c[1] == "/api/v2/appointments"][0]
        self.assertEqual(body["StartTime"], utc(self.d, 14, 30))
        self.assertEqual((body["AppointmentBlockId"], body["AdviserId"], body["PreferredAppointmentTypeId"], body["Topic"], body["StudentId"]),
                         (2001, 9001, 10004401017599, "Recruiting for PE", ME))
        mine = self.c.my_appointments()
        self.assertEqual([(m["with"], m["start"], m["end"]) for m in mine], [("Pat Fellow", "14:30", "15:00")])
        self.assertNotIn("SECRET", json.dumps(mine))                 # join links never returned
        self.assertTrue(self.c.cancel_appointment(mine[0]["id"])["cancelled"])
        self.assertEqual(self.c.my_appointments(), [])

    def test_refusals(self):
        bad = {
            "no topic": lambda: self.c.book_appointment("Pat Fellow", self.d, "14:30", "", 30),
            "odd length": lambda: self.c.book_appointment("Pat Fellow", self.d, "14:30", "x", 20),
            "outside block": lambda: self.c.book_appointment("Pat Fellow", self.d, "15:45", "x", 30),
            "not offered that day": lambda: self.c.book_appointment("Pat Fellow", self.d + timedelta(days=1), "14:00", "x", 30),
            "ambiguous name": lambda: self.c.book_appointment("Fellow", self.d, "14:00", "x", 30),
            "bad time": lambda: self.c.book_appointment("Pat Fellow", self.d, "2pm", "x", 30),
        }
        for label, f in bad.items():
            with self.subTest(label):
                with self.assertRaises(ValueError):
                    f()
        self.c.book_appointment("Pat Fellow", self.d, "14:00", "x", 30)
        with self.assertRaises(ValueError):           # overlaps my own appointment
            self.c.book_appointment("Pat Fellow", self.d, "14:15", "y", 30)


FAKE_PAGES = [
    "1 Fellows\nSecond-year student leaders...\nIndustry Contact(s)\nInvestment Banking Pat Fellow, Alex Fellowson\n",
    "21\n  Pat  Fellow  \nFunctions:  M&A, Private Equity  \nAdditional Areas:  International Student  \n"
    "Summer Internship:  Goldman Sachs  \nPre-MBA Company:  Big Bank  \nIndustries:  Investment Banking  \n"
    "  Alex Fellowson  \nFunctions:  Product Mg mt.  \nSummer Internship:  Some Startup  \nIndustries:  Technology  \n",
    "30 Executives in Residence\n",
    "31\n  Jordan Exec  ‘90 \nJordan is a former CEO of a large bank.\nEducation:  MBA, Columbia  \nIndustries : Private Equity  \n",
    "10 Coaches\n",
]


class AceBook(unittest.TestCase):
    def test_parse_and_search(self):
        pages = [FAKE_PAGES[4], FAKE_PAGES[0], FAKE_PAGES[1], FAKE_PAGES[2], FAKE_PAGES[3]]
        ppl = ace_book.parse_pages(pages)
        self.assertEqual([(p["name"], p["role"]) for p in ppl],
                         [("Pat Fellow", "fellow"), ("Alex Fellowson", "fellow"), ("Jordan Exec", "executive")])
        self.assertEqual(ppl[0]["fields"]["Summer Internship"], "Goldman Sachs")
        self.assertEqual(ppl[2]["class_of"], "90")
        names = lambda **k: [p["name"] for p in ace_book.search(ppl, **k)]
        self.assertEqual(names(role="fellow", industry="private equity"), ["Pat Fellow"])
        self.assertEqual(names(company="goldman"), ["Pat Fellow"])
        self.assertEqual(names(function="product mgmt"), ["Alex Fellowson"])   # PDF's "Mg mt" still matches
        self.assertEqual(names(role="EIR"), ["Jordan Exec"])
        self.assertEqual(names(industry="private equity"), ["Pat Fellow", "Jordan Exec"])


if __name__ == "__main__":
    unittest.main()


class AceBookRefresh(unittest.TestCase):
    def setUp(self):
        import tempfile
        from pathlib import Path
        self.tmp = tempfile.TemporaryDirectory()
        self.old = (ace_book.ACE_FILE, ace_book.import_pdf)
        ace_book.ACE_FILE = Path(self.tmp.name, "ace_book.json")
        self.downloads = 0

        def fake_import(source=None):
            self.downloads += 1
            if getattr(self, "offline", False):
                raise RuntimeError("no network")
            ace_book.ACE_FILE.write_text(json.dumps({"fetched": __import__("time").time(),
                                                     "people": [{"name": "Pat Fellow", "role": "fellow", "fields": {}, "bio": ""}]}))
            return 1
        ace_book.import_pdf = fake_import

    def tearDown(self):
        ace_book.ACE_FILE, ace_book.import_pdf = self.old
        self.tmp.cleanup()

    def test_downloads_when_missing_then_uses_local_copy(self):
        self.assertEqual(ace_book.load()[0]["name"], "Pat Fellow")
        ace_book.load()
        self.assertEqual(self.downloads, 1)

    def test_refreshes_when_old_and_falls_back_offline(self):
        ace_book.ACE_FILE.write_text(json.dumps({"fetched": 0, "people": [{"name": "Old Copy", "role": "fellow", "fields": {}, "bio": ""}]}))
        self.offline = True
        self.assertEqual(ace_book.load()[0]["name"], "Old Copy")    # old copy still works offline
        self.offline = False
        self.assertEqual(ace_book.load()[0]["name"], "Pat Fellow")  # refreshed once online
        ace_book.ACE_FILE.unlink()
        self.offline = True
        with self.assertRaises(FileNotFoundError):
            ace_book.load()
