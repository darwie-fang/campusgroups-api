"""A simulated CampusGroups, so tests never touch the real site or anyone's login.

The shapes mirror real responses captured from groups.gsb.columbia.edu (with
made-up names). Each test builds a FakeSite, then `install(client)` patches a
GSBClient so its requests go here instead of the network.
"""
import json
from datetime import date, datetime, timedelta

import gsb_client as g

TZ = g.TZ


def next_weekday(days_ahead=1):
    d = datetime.now(TZ).date() + timedelta(days=days_ahead)
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d


def epoch(d: date, h: int, m: int = 0) -> int:
    return int(datetime(d.year, d.month, d.day, h, m, tzinfo=TZ).timestamp() * 1000)


ROOM_FIELDS = ("checkbox_id,name,roomSchedule,roomCalendarStartHour,roomCalendarEndHour,dateMap,"
               "description,capacity,building,floor,room_type,")
RES_FIELDS = ("reservation_status,room_name,reservation_date,duration,checkbox_id,reservation_type,"
              "name,event_id,room_description,show_cancel_button,room_reservations_id,")
CLUB_EVENT_FIELDS = ("eventDates,id,event_name,registrationRequired,eventButtonLabel,eventLocation,"
                     "eventTags,ariaEventDetails,ariaEventDetailsWithLocation,eventAttendees,registrationStatus,")
FEED_FIELDS = ("date_separator,eventId,eventName,eventDates,clubId,clubLogin,clubName,eventLocation,"
               "eventTags,registered,ariaEventDetails,eventPriceRange,")


def aria(d: date, h: int) -> str:
    ap = "PM" if h >= 12 else "AM"
    return f"X. {d:%A}, {d.day} {d:%B} {d.year} At {h % 12 or 12}:00 {ap}, EDT (GMT-4)."


class FakeSite:
    def __init__(self):
        self.my_groups = [
            {"groupID": 24090, "groupName": "Golf Club", "groupLogin": "golf"},
            {"groupID": 24028, "groupName": "Asian Business Association", "groupLogin": "aba"},
            {"groupID": 24052, "groupName": "FinTech and Blockchain Club", "groupLogin": "FinTech"},
            {"groupID": 24299, "groupName": "Gaming &amp; Esports Club", "groupLogin": "Esports"},
            {"groupID": 24182, "groupName": "Venture Capital Club", "groupLogin": "VCC"},
            {"groupID": 24165, "groupName": "Snow Sports Club", "groupLogin": "ski"},
        ]
        self.recent_groups = [{"groupID": 24173, "groupName": "Technology Club", "groupLogin": "Tech"}]
        # (club name, club id, login, day, hour, title, registered)
        self.d1 = next_weekday(1)
        self.d2 = self.d1 + timedelta(days=1)
        while self.d2.weekday() >= 5:
            self.d2 += timedelta(days=1)
        self.feed_events = [
            ("Artificial Intelligence Club", 24308, "AIC", self.d1, 18, "Vibe Coding Dinner", "0"),
            ("Technology Club", 24173, "Tech", self.d1, 12, "Tech Mixer", "1"),
            ("Executive Education", 99, "ExecEd", self.d2, 9, "Exec Ed Day", "0"),
        ]
        self.reservations = []      # dicts: id, room_id, start(ms), end(ms), title, status
        self.posts = []
        self.logged_in = True

    # ---- data builders ----
    def _schedule(self, room_id, week_start):
        blocks = []
        for i in range(7):
            day = week_start + timedelta(days=i)
            if day.weekday() >= 5:      # weekends: closed all day
                blocks.append({"title": "Unavailable", "startEpoch": epoch(day, 0), "endEpoch": epoch(day, 23, 56)})
                continue
            blocks.append({"title": "Unavailable", "startEpoch": epoch(day, 0), "endEpoch": epoch(day, 6)})
            if room_id == "648":        # someone else's booking, with their name (must never leak)
                blocks.append({"title": "Some Student ab1234", "startEpoch": epoch(day, 12), "endEpoch": epoch(day, 14, 30)})
        for r in self.reservations:
            if r["room_id"] == room_id and r["status"] != "cancelled":
                blocks.append({"title": "Darwie Fang", "startEpoch": r["start"], "endEpoch": r["end"]})
        return json.dumps(blocks)

    def rooms(self, day: date):
        ws = day - timedelta(days=(day.weekday() + 1) % 7)
        def room(i, name, typ, cap="6 seats"):
            return {"fields": ROOM_FIELDS, "p0": i, "p1": name, "p2": self._schedule(i, ws), "p3": "6",
                    "p4": "24", "p5": "", "p6": "", "p7": cap, "p8": "Geffen Hall", "p9": "5", "p10": typ}
        return [room("648", "Geffen 504 - The Chrin Family Study Room", "Study/Breakout Room"),
                room("649", "Geffen 506 - The Chrin Family Study Room", "Study/Breakout Room"),
                room("621", "Geffen 481", "Phone Booth", "1 seat"),
                room("517", "Geffen 742", "CMC Interview Room")]

    # ---- request handlers ----
    def get(self, path, **p):
        if not self.logged_in:
            raise g.SessionExpired("expired")
        if "mobile_header_groups" in path:
            if p.get("all") == "true":
                return [{"type": "search", "groups": []}]
            return [{"type": "last", "groups": self.recent_groups}, {"type": "group", "groups": self.my_groups}]
        if "mobile_group_page_events" in path:
            if p.get("range", 0) > 0:
                return []
            out = []
            for club, cid, _, d, h, title, _ in self.feed_events:
                if str(cid) == str(p.get("param")):
                    out.append({"fields": CLUB_EVENT_FIELDS, "p0": f"<p>{d:%a, %b} {d.day}, {d.year}</p><p>{h % 12 or 12} PM &ndash; 8 PM</p>",
                                "p1": "1", "p2": title, "p3": "1", "p4": "Register", "p5": "Geffen 590",
                                "p6": '<p><span class="label label-tag">Social</span><span class="label label-tag"><span>Artificial Intelligence</span></span></p>',
                                "p7": aria(d, h), "p8": "", "p9": "10", "p10": ""})
            return out
        if "mobile_events_list" in path:
            if p.get("range", 0) > 0:
                return []
            return [{"fields": FEED_FIELDS, "p0": None, "p1": str(i), "p2": title,
                     "p3": f"<p>{d:%a, %b} {d.day}, {d.year}</p>", "p4": str(cid), "p5": login, "p6": club,
                     "p7": "Kravis 420", "p8": "", "p9": reg, "p10": aria(d, h), "p11": "Free"}
                    for i, (club, cid, login, d, h, title, reg) in enumerate(self.feed_events)]
        if "mobile_room_availability_calendar" in path:
            if p.get("range", 0) > 0:
                return []
            return self.rooms(date.fromisoformat(p["filter8"]))
        if "mobile_user_rooms_reservations" in path:
            out = []
            for r in self.reservations:
                if r["status"] == "cancelled":
                    continue
                s = datetime.fromtimestamp(r["start"] / 1000, TZ)
                when = f"{s:%a, %b} {s.day}, {s.year} {s.hour % 12 or 12}:{s:%M} {s:%p}"
                out.append({"fields": RES_FIELDS, "p0": "booked", "p1": "Geffen 504 - The Chrin Family Study Room",
                            "p2": when, "p3": "0 hours", "p4": r["id"], "p5": "Direct Booking", "p6": r["title"],
                            "p7": "-", "p8": "", "p9": "1", "p10": r["id"]})
            return out
        return []

    def get_html(self, path, **p):
        if path == "/room_reservation_form":
            return '<form id="table_form" action="/room_reservation_form" method="POST"><input type="hidden" name="_csrf" value="FORMTOKEN"></form>'
        if path == "/room_reservation_details":
            return 'function cancelReservation%s(){ jQuery.ajax({ data: { id: %s, action: "cancel", _csrf: "CANCELTOKEN" } }) }' % (p["id"], p["id"])
        return ""

    def post(self, path, data, referer):
        self.posts.append((path, dict(data)))
        if path == "/room_reservation_form":
            s = datetime.strptime(f'{data["start"]} {data["start_hour"]}:{data["start_minute"]} {data["start_ampm"]}',
                                  "%d %b %y %I:%M %p").replace(tzinfo=TZ)
            self.reservations.append({"id": str(425659 + len(self.reservations)), "room_id": data["room"],
                                      "start": int(s.timestamp() * 1000),
                                      "end": int((s + timedelta(minutes=int(data["duration"]))).timestamp() * 1000),
                                      "title": data["name"], "status": "booked"})
        if path == "/room_reservation_details":
            for r in self.reservations:
                if r["id"] == data["id"]:
                    r["status"] = "cancelled"

        class R:
            text = "<p>ok</p>"
            status_code = 200
            headers = {}
        return R()

    def install(self, client):
        client._get = self.get
        client._get_html = self.get_html
        client._post = self.post
        return client
