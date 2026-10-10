"""12twenty career events (search, details, sign up, cancel), against a pretend
12twenty with made-up events and companies."""
import os, sys, unittest
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gsb_client as g
from fakes import next_weekday, TZ

UTC = ZoneInfo("UTC")
ME = 540016000000001


def utc(d, h, m=0):
    return datetime(d.year, d.month, d.day, h, m, tzinfo=TZ).astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S")


def rules(can=True, waitlist=False, reason=None):
    return {"CanRegister": can, "CanRegisterForWaitlist": waitlist, "CannotRegisterReason": reason}


class FakeEvents:
    def __init__(self):
        self.day = next_weekday(2)
        d = self.day
        base = {"TimeZoneId": 2, "IsTimeSlotEvent": False, "PreSurveyMetadataJson": None,
                "RegistrationDocumentTypes": [], "PaymentConfigId": None, "AddOns": [],
                "RegistrationTypeList": [], "StatusName": "Registration Open", "Location": "Virtual",
                "RegistrationBeginDate": utc(d - timedelta(days=7), 9), "RegistrationEndDate": utc(d, 11),
                "IsAttending": False}
        self.events = {
            301: {**base, "Id": 301, "Name": "Info Session", "CompanyName": "Acme Capital",
                  "EventTypeName": "Company Presentation", "StartDate": utc(d, 12), "EndDate": utc(d, 13),
                  "Description": "<p>Learn about <b>private equity</b> at Acme.</p>",
                  "RegistrationInstructions": "Also register on our Zoom link: https://example.com/zoom",
                  "WorkAuthRequirementName": "No requirement", "RegistrationRules": rules()},
            302: {**base, "Id": 302, "Name": "Coffee Chats", "CompanyName": "Beta Partners",
                  "EventTypeName": "Coffee Chat", "StartDate": utc(d, 15), "EndDate": utc(d, 17),
                  "IsTimeSlotEvent": True, "RegistrationRules": rules()},
            303: {**base, "Id": 303, "Name": "Full Panel", "CompanyName": "Gamma Co",
                  "EventTypeName": "Other", "StartDate": utc(d, 18), "EndDate": utc(d, 19),
                  "HasWaitlist": True, "RegistrationRules": rules(False, True, "The event is at maximum capacity now.")},
            304: {**base, "Id": 304, "Name": "Resume Drop", "CompanyName": "Delta Bank",
                  "EventTypeName": "Company Presentation", "StartDate": utc(d + timedelta(days=20), 12),
                  "EndDate": utc(d + timedelta(days=20), 13),
                  "RegistrationDocumentTypes": [{"Name": "Resume", "IsRequired": True}], "RegistrationRules": rules()},
            305: {**base, "Id": 305, "Name": "Closed Talk", "CompanyName": "Epsilon",
                  "EventTypeName": "Other", "StartDate": utc(d, 9), "EndDate": utc(d, 10),
                  "RegistrationRules": rules(False, False, "The event registration has closed. You can't register now.")},
        }
        self.regs = {}          # event id -> registration
        self.next_reg = 7000001
        self.calls = []

    def tw(self, method, path, body=None, referer=None):
        self.calls.append((method, path, body))
        if path == "/api/v2/account/current-user":
            return {"Id": ME}
        if path == "/api/v2/Events/post-query":
            assert body["Filters"][0]["SelectedId"] == 15        # upcoming events only
            items = [{k: v for k, v in e.items() if k != "RegistrationRules"} for e in self.events.values()]
            return {"Total": len(items), "Items": items[(body["PageNumber"] - 1) * body["PageSize"]:][:body["PageSize"]]}
        if path.startswith("/api/v2/event-student-registrations?"):
            assert f"studentId={ME}" in path
            return {"Total": len(self.regs), "Items": list(self.regs.values())}
        if path.startswith("/api/v2/Events/") and method == "GET":
            eid = int(path.rsplit("/", 1)[1])
            if eid not in self.events:
                raise RuntimeError("12twenty said 404")
            return self.events[eid]
        if path == "/api/v2/event-student-registrations" and method == "POST":
            assert body["StudentId"] == ME and body["TimeSlotId"] is None
            e = self.events[body["EventId"]]
            wl = not e["RegistrationRules"]["CanRegister"]
            self.regs[body["EventId"]] = {"Id": self.next_reg, "EventId": body["EventId"], "StudentId": ME,
                                          "IsOnWaitlist": wl, "WaitlistPosition": 3 if wl else None,
                                          "TimeSlotStartTime": None}
            self.next_reg += 1
            return {"Id": self.next_reg - 1}
        if path.startswith("/api/v2/event-student-registrations/") and method == "DELETE":
            rid = int(path.rsplit("/", 1)[1])
            self.regs = {k: v for k, v in self.regs.items() if v["Id"] != rid}
            return None
        raise AssertionError(f"unexpected call {method} {path}")


class CareerEvents(unittest.TestCase):
    def setUp(self):
        self.site = FakeEvents()
        self.c = g.GSBClient()
        self.c._tw = self.site.tw
        self.d = self.site.day

    def test_search_in_new_york_time_and_filters(self):
        r = self.c.career_events(self.d, self.d)
        self.assertEqual([(e["name"], e["start"], e["end"]) for e in r["events"]],
                         [("Closed Talk", "09:00", "10:00"), ("Info Session", "12:00", "13:00"),
                          ("Coffee Chats", "15:00", "17:00"), ("Full Panel", "18:00", "19:00")])
        self.assertEqual([e["company"] for e in self.c.career_events(self.d, self.d, company="acme")["events"]], ["Acme Capital"])
        self.assertEqual([e["id"] for e in self.c.career_events(self.d, self.d, event_type="coffee chats")["events"]], ["302"])
        self.assertEqual([e["id"] for e in self.c.career_events(self.d, self.d, keyword="private equity")["events"]], ["301"])
        # Default range is two weeks, so the event 20 days out is only found with a wider range.
        self.assertNotIn("304", [e["id"] for e in self.c.career_events(self.d)["events"]])
        self.assertIn("304", [e["id"] for e in self.c.career_events(self.d, self.d + timedelta(days=25))["events"]])

    def test_details_flag_website_only_events(self):
        d = self.c.event_details("301")
        self.assertTrue(d["can_register"])
        self.assertIn("Zoom", d["registration_instructions"])
        self.assertEqual(d["description"], "Learn about private equity at Acme.")
        self.assertNotIn("sign_up_on_website", d)
        self.assertIn("you pick a time slot", self.c.event_details("302")["sign_up_on_website"])
        self.assertIn("it needs a Resume upload", self.c.event_details("304")["sign_up_on_website"])

    def test_register_list_cancel(self):
        r = self.c.register_event("301")
        self.assertTrue(r["registered"])
        self.assertIn("Zoom", r["also_note"])
        mine = self.c.my_event_registrations()
        self.assertEqual([(m["id"], m["registered"]) for m in mine], [("301", True)])
        self.assertTrue(self.c.career_events(self.d, self.d, registered_only=True)["events"][0]["registered"])
        with self.assertRaises(ValueError):                     # already registered
            self.c.register_event("301")
        self.assertTrue(self.c.cancel_event_registration("301")["cancelled"])
        self.assertEqual(self.c.my_event_registrations(), [])
        with self.assertRaises(KeyError):
            self.c.cancel_event_registration("301")

    def test_waitlist_needs_explicit_ok(self):
        with self.assertRaises(ValueError) as e:
            self.c.register_event("303")
        self.assertIn("waitlist", str(e.exception))
        r = self.c.register_event("303", join_waitlist=True)
        self.assertTrue(r["waitlisted"])
        self.assertFalse(r["registered"])
        self.assertEqual(r["waitlist_position"], 3)
        self.assertTrue(self.c.my_event_registrations()[0]["waitlisted"])

    def test_refusals_send_nothing(self):
        for eid in ("302", "304", "305", "Info Session"):
            with self.subTest(eid):
                with self.assertRaises(ValueError):
                    self.c.register_event(eid)
        self.assertFalse([c for c in self.site.calls if c[0] == "POST" and "registrations" in c[1]])


class SignedOutOf12twenty(unittest.TestCase):
    def test_empty_who_am_i_means_signed_out(self):
        # 12twenty answers 204 (empty) to "who am I?" when you're not signed in.
        c = g.GSBClient()
        c._tw = lambda method, path, body=None, referer=None: None
        with self.assertRaises(g.SessionExpired):
            c.career_events()


if __name__ == "__main__":
    unittest.main()
