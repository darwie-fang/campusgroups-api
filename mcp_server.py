"""
Lets the Claude desktop app use your CampusGroups API as tools, so you can ask
in plain English: "what's the AI Club running next week?"

This is a small MCP server (the plug-in format Claude uses for tools). The
Claude app starts it on your Mac when needed; you don't run it yourself.
It reads clubs, events and rooms, and can book or cancel rooms after the user confirms.
Uses the session saved by `python3 gsb.py login`.

No extra packages: it speaks MCP's JSON-RPC over stdin/stdout directly, so it
works on Python 3.9.
"""

import json
import os
import sys
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from gsb_client import GSBClient, SessionExpired  # noqa: E402
import ace_book  # noqa: E402

SERVER = {"name": "campusgroups", "version": "0.6"}
gsb = GSBClient()

DATE = {"type": "string", "description": "YYYY-MM-DD"}
TIME = {"type": "string", "description": "HH:MM, 24-hour, e.g. 14:00"}

TOOLS = [
    {
        "name": "list_my_clubs",
        "description": "List the CampusGroups clubs the user is a member of, with their IDs. "
                       "Use it to match casual names ('ai club') to real clubs.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_club_events",
        "description": "Events run by one club between two dates. `club` may be an ID, full name, "
                       "short name, or something close like 'ai club'. Dates default to today "
                       "through 7 days from start.",
        "inputSchema": {"type": "object", "properties": {
            "club": {"type": "string"}, "start": DATE, "end": DATE},
            "required": ["club"]},
    },
    {
        "name": "get_all_events",
        "description": "Events across ALL clubs and school offices at Columbia Business School "
                       "between two dates, optionally filtered by club name, keyword (q), topic "
                       "tag (e.g. 'Artificial Intelligence', 'Venture Capital'), or only events "
                       "the user is registered for. Prefer narrow date ranges: a busy week has "
                       "hundreds of events. The first call for a range can take ~10 seconds.",
        "inputSchema": {"type": "object", "properties": {
            "start": DATE, "end": DATE,
            "club": {"type": "string"}, "q": {"type": "string"}, "tag": {"type": "string"},
            "registered_only": {"type": "boolean"},
            "limit": {"type": "integer", "description": "Max events to return (default 60)"}}},
    },
    {
        "name": "find_free_rooms",
        "description": "Bookable rooms at CBS (study rooms, phone booths, classrooms) and their free "
                       "times on a date. With start and end, returns only rooms free for that whole "
                       "window. Works for any upcoming date, but study rooms only open for booking a few days "
                       "ahead. CMC interview rooms are hidden unless room_type asks for them. "
                       "Use book_room to reserve one.",
        "inputSchema": {"type": "object", "properties": {
            "date": DATE, "start": TIME, "end": TIME,
            "building": {"type": "string", "description": "e.g. Geffen, Kravis"},
            "min_capacity": {"type": "integer"},
            "room_type": {"type": "string", "description": "e.g. Study, Phone Booth, Classroom"}}},
    },
    {
        "name": "my_room_reservations",
        "description": "The user's upcoming room reservations, with ids for cancel_booking.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "find_person_email",
        "description": "Look up the email of ONE specific person, named by the user, in the CBS "
                       "myCBS directory, so the user can email or invite them (e.g. 'send Sam Davies "
                       "an invite to my booking'). Needs first and last name. Only use it when the "
                       "user wants to contact that person; never to build lists, look up many people, "
                       "or search by anything other than a name. If several people share the name, "
                       "ask the user which one and call again with class_year. Don't repeat the "
                       "email back unless it's needed for the message or invite.",
        "inputSchema": {"type": "object", "properties": {
            "name": {"type": "string", "description": "First and last name, e.g. 'Sam Davies'"},
            "class_year": {"type": "string", "description": "Only to pick between people with the same name, e.g. '2028'"}},
            "required": ["name"]},
    },
    {
        "name": "find_ace_people",
        "description": "Search the CMC ACE Book (advisors, coaches, fellows, executives in residence) by "
                       "industry, function, company (summer internship / pre-MBA / previous companies), "
                       "keyword or name, to pick who to book an appointment with. All filters optional "
                       "and combined. role: fellow, coach, advisor or executive.",
        "inputSchema": {"type": "object", "properties": {
            "role": {"type": "string"}, "industry": {"type": "string"}, "function": {"type": "string"},
            "company": {"type": "string"}, "keyword": {"type": "string"}, "name": {"type": "string"}}},
    },
    {
        "name": "appointment_slots",
        "description": "Open CMC appointment times in 12twenty (New York time) between two dates "
                       "(default: the next 7 days). Filter by adviser name, or role ('fellow', 'coach', "
                       "'advisor' - uses the ACE Book), or by an ACE Book search (industry/company) to "
                       "only show matching people. Each slot is a window: any start inside it works.",
        "inputSchema": {"type": "object", "properties": {
            "start": DATE, "end": DATE, "adviser": {"type": "string"}, "role": {"type": "string"},
            "industry": {"type": "string"}, "company": {"type": "string"}, "function": {"type": "string"}}},
    },
    {
        "name": "my_appointments",
        "description": "The user's upcoming CMC appointments in 12twenty, with ids for cancel_appointment.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "book_appointment",
        "description": "Book a CMC appointment in 12twenty in the user's name (a real booking). Never "
                       "guess a missing detail: if the person, date, start time, length (15/30/45/60 min) "
                       "or topic is missing, ask the user. BEFORE calling it, state the person, date, "
                       "time, length and topic and get an explicit yes; then pass confirmed=true. One "
                       "appointment per request; never book several to 'hold' them.",
        "inputSchema": {"type": "object", "properties": {
            "adviser": {"type": "string", "description": "Name of the fellow/adviser/coach"},
            "date": DATE, "start": TIME,
            "minutes": {"type": "integer", "description": "15, 30, 45 or 60"},
            "topic": {"type": "string", "description": "What the user wants to discuss, in their words"},
            "appointment_type": {"type": "string", "description": "Only if the slot offers several types"},
            "confirmed": {"type": "boolean"}},
            "required": ["adviser", "date", "start", "minutes", "topic", "confirmed"]},
    },
    {
        "name": "cancel_appointment",
        "description": "Cancel one of the user's CMC appointments (id from my_appointments). Confirm which "
                       "one with the user first, then pass confirmed=true.",
        "inputSchema": {"type": "object", "properties": {
            "appointment_id": {"type": "string"}, "confirmed": {"type": "boolean"}},
            "required": ["appointment_id", "confirmed"]},
    },
    {
        "name": "career_events",
        "description": "Upcoming CMC/recruiting events in 12twenty: employer info sessions, coffee chats, "
                       "company presentations, CMC workshops (New York time). Different from "
                       "get_all_events, which is club events on CampusGroups; for 'recruiting', "
                       "'company', 'info session' or 'coffee chat' questions use this. Default range: "
                       "the next 14 days. Filter by company, keyword, event_type (e.g. 'Coffee Chat', "
                       "'Company Presentation') or registered_only.",
        "inputSchema": {"type": "object", "properties": {
            "start": DATE, "end": DATE, "company": {"type": "string"}, "keyword": {"type": "string"},
            "event_type": {"type": "string"}, "registered_only": {"type": "boolean"},
            "limit": {"type": "integer", "description": "Max events to return (default 60)"}}},
    },
    {
        "name": "career_event_details",
        "description": "Full details of one 12twenty event (id from career_events): description, audience, "
                       "dress code, work authorization, seats, whether the user can sign up here, and any "
                       "extra registration steps (e.g. a company Zoom link). Call it before register_career_event.",
        "inputSchema": {"type": "object", "properties": {"event_id": {"type": "string"}},
                        "required": ["event_id"]},
    },
    {
        "name": "my_career_events",
        "description": "Upcoming 12twenty events the user is registered or waitlisted for, with ids for "
                       "cancel_career_event_registration.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "register_career_event",
        "description": "Sign the user up for a 12twenty event (a real registration that employers can "
                       "see). Never guess which event: if it's ambiguous, ask. BEFORE calling it, state "
                       "the event name, company, date and time, mention any work-authorization "
                       "requirement and extra registration steps from career_event_details, and get an "
                       "explicit yes; then pass confirmed=true. If the event is full and has a waitlist, "
                       "ask whether to join it and only then pass join_waitlist=true. Events with time "
                       "slots (most coffee chats), required uploads or questions must be done on "
                       "12twenty; give the user the link. One event per request.",
        "inputSchema": {"type": "object", "properties": {
            "event_id": {"type": "string"}, "join_waitlist": {"type": "boolean"},
            "confirmed": {"type": "boolean"}},
            "required": ["event_id", "confirmed"]},
    },
    {
        "name": "cancel_career_event_registration",
        "description": "Cancel the user's registration for a 12twenty event, or take them off its waitlist "
                       "(event id from my_career_events). Confirm which event with the user first, then "
                       "pass confirmed=true.",
        "inputSchema": {"type": "object", "properties": {
            "event_id": {"type": "string"}, "confirmed": {"type": "boolean"}},
            "required": ["event_id", "confirmed"]},
    },
    {
        "name": "find_free_food",
        "description": "In-person CBS events with food (New York time): CampusGroups events whose "
                       "organiser ticked 'Food Provided', events with food in the title, and 12twenty "
                       "recruiting events whose description mentions food (receptions, lunches). "
                       "Default: the rest of today and tomorrow; max a week. Paid events are left out "
                       "unless include_paid. Tell the user when an event needs registration, and that "
                       "'confidence: low' means only the title suggests food. Can take ~10 seconds.",
        "inputSchema": {"type": "object", "properties": {
            "start": DATE, "end": DATE, "include_paid": {"type": "boolean"}}},
    },
    {
        "name": "book_room",
        "description": "Reserve a room on CampusGroups in the user's name. This makes a real booking. "
                       "Never guess or fill in a detail the user didn't give: if the room, date, start "
                       "time, end time (or duration) or title is missing or ambiguous, ask the user for "
                       "exactly those missing details first (e.g. 'What title should I put on it?'). "
                       "Suggesting free rooms is fine, but the user must pick. "
                       "BEFORE calling it, state the exact room, date, start and end time and title, "
                       "and get an explicit yes from the user in this conversation; then pass "
                       "confirmed=true. Never book on your own initiative, never book several rooms "
                       "or slots at once to 'hold' them, and never book CMC interview rooms unless "
                       "the user says they're entitled to. Max 4 hours. It checks the room is free "
                       "first and verifies the booking afterwards.",
        "inputSchema": {"type": "object", "properties": {
            "room": {"type": "string", "description": "Room id or name, e.g. 'Geffen 504'"},
            "date": DATE, "start": TIME, "end": TIME,
            "title": {"type": "string", "description": "Title shown on the booking, exactly as the user gave it (ask if they didn't)"},
            "confirmed": {"type": "boolean", "description": "true only after the user said yes to these exact details"}},
            "required": ["room", "date", "start", "end", "title", "confirmed"]},
    },
    {
        "name": "cancel_booking",
        "description": "Cancel one of the user's room reservations (id from my_room_reservations). "
                       "Confirm which reservation with the user first, then pass confirmed=true.",
        "inputSchema": {"type": "object", "properties": {
            "reservation_id": {"type": "string"},
            "confirmed": {"type": "boolean"}},
            "required": ["reservation_id", "confirmed"]},
    },
]

COMPACT = ("title", "club", "when", "location", "registration", "registered", "price", "id")


def _compact(events, limit):
    out = [{k: e[k] for k in COMPACT if e.get(k) not in (None, "", [])} for e in events[:limit]]
    return out


def call_tool(name, a):
    if name == "list_my_clubs":
        return [{"id": c["id"], "name": c["name"]} for c in gsb.my_clubs()]
    if name == "get_club_events":
        r = gsb.club_events(a["club"], a.get("start"), a.get("end"))
        return {"club": r["club"]["name"], "start": r["start"], "end": r["end"],
                "events": _compact(r["events"], 100)}
    if name == "get_all_events":
        limit = int(a.get("limit") or 60)
        r = gsb.all_events(a.get("start"), a.get("end"), a.get("club"), a.get("q"),
                           a.get("tag"), bool(a.get("registered_only")))
        res = {"start": r["start"], "end": r["end"], "total_matching": r["count"],
               "events": _compact(r["events"], limit)}
        if r["count"] > limit:
            res["note"] = (f"Showing {limit} of {r['count']}. Narrow the dates or filter by "
                           "club, q or tag to see the rest.")
        return res
    if name == "find_free_rooms":
        r = gsb.free_rooms(a.get("date"), a.get("start"), a.get("end"), a.get("building"),
                           a.get("min_capacity"), a.get("room_type"))
        return r
    if name == "my_room_reservations":
        return gsb.my_reservations()
    if name in ("book_room", "cancel_booking") and a.get("confirmed") is not True:
        raise ValueError("Not done: confirm the exact details with the user first, then call again "
                         "with confirmed=true.")
    if name == "find_ace_people":
        hits = ace_book.search(ace_book.load(), a.get("role"), a.get("industry"), a.get("function"),
                               a.get("company"), a.get("keyword"), a.get("name"))
        return {"count": len(hits), "people": [{"name": p["name"], "role": p["role"],
                **({"class_of": "'" + p["class_of"]} if p.get("class_of") else {}), **p["fields"],
                **({"bio": p["bio"][:400]} if p.get("bio") else {})} for p in hits[:25]],
                **({"note": f"Showing 25 of {len(hits)}; narrow the search."} if len(hits) > 25 else {})}
    if name == "appointment_slots":
        r = gsb.appointment_slots(a.get("start"), a.get("end"), a.get("adviser"))
        if any(a.get(k) for k in ("role", "industry", "company", "function")):
            wanted = {ace_book._norm(p["name"]) for p in ace_book.search(
                ace_book.load(), a.get("role"), a.get("industry"), a.get("function"), a.get("company"))}
            r["slots"] = [x for x in r["slots"] if ace_book._norm(x["adviser"]) in wanted]
            r["count"] = len(r["slots"])
            r["filtered_by_ace_book"] = True
        return r
    if name == "my_appointments":
        return gsb.my_appointments()
    if name in ("book_appointment", "cancel_appointment") and a.get("confirmed") is not True:
        raise ValueError("Not done: confirm the exact details with the user first, then call again "
                         "with confirmed=true.")
    if name == "book_appointment":
        return gsb.book_appointment(a["adviser"], a["date"], a["start"], a["topic"],
                                    int(a.get("minutes") or 0), a.get("appointment_type"))
    if name == "cancel_appointment":
        return gsb.cancel_appointment(a["appointment_id"])
    if name == "career_events":
        limit = int(a.get("limit") or 60)
        r = gsb.career_events(a.get("start"), a.get("end"), a.get("company"), a.get("keyword"),
                              a.get("event_type"), bool(a.get("registered_only")))
        res = {"start": r["start"], "end": r["end"], "total_matching": r["count"],
               "events": [{k: v for k, v in e.items() if k != "link" and v not in (None, False, "")}
                          for e in r["events"][:limit]]}
        if r["count"] > limit:
            res["note"] = f"Showing {limit} of {r['count']}. Narrow the dates or filter to see the rest."
        return res
    if name == "career_event_details":
        return gsb.event_details(a["event_id"])
    if name == "my_career_events":
        return gsb.my_event_registrations()
    if name in ("register_career_event", "cancel_career_event_registration") and a.get("confirmed") is not True:
        raise ValueError("Not done: confirm the exact event with the user first, then call again "
                         "with confirmed=true.")
    if name == "register_career_event":
        return gsb.register_event(a["event_id"], bool(a.get("join_waitlist")))
    if name == "cancel_career_event_registration":
        return gsb.cancel_event_registration(a["event_id"])
    if name == "find_free_food":
        return gsb.free_food(a.get("start"), a.get("end"), bool(a.get("include_paid")))
    if name == "find_person_email":
        return gsb.find_person_email(a["name"], a.get("class_year"))
    if name == "book_room":
        return gsb.book_room(a["room"], a["date"], a["start"], a["title"], end=a["end"])
    if name == "cancel_booking":
        return gsb.cancel_booking(a["reservation_id"])
    raise KeyError(f"Unknown tool: {name}")


def handle(msg):
    method, mid = msg.get("method"), msg.get("id")
    if mid is None:            # notifications (e.g. notifications/initialized): no reply
        return None
    if method == "initialize":
        version = (msg.get("params") or {}).get("protocolVersion", "2024-11-05")
        return {"protocolVersion": version, "capabilities": {"tools": {}}, "serverInfo": SERVER}
    if method == "ping":
        return {}
    if method == "tools/list":
        return {"tools": TOOLS}
    if method == "tools/call":
        p = msg.get("params") or {}
        try:
            data = call_tool(p.get("name"), p.get("arguments") or {})
            return {"content": [{"type": "text", "text": json.dumps(data, ensure_ascii=False)}]}
        except SessionExpired:
            text = ("Not logged in to CampusGroups (the login may have expired). Ask the user "
                    "to open Terminal, paste this line and press Enter, then sign in with "
                    "their UNI and Duo:\n" + f"bash '{os.path.join(HERE, 'setup.sh')}'")
        except (KeyError, ValueError, FileNotFoundError) as e:
            text = str(e).strip("'\"")
        except Exception as e:  # network trouble, site changes, etc.
            traceback.print_exc(file=sys.stderr)
            text = f"CampusGroups request failed: {type(e).__name__}: {e}"
        return {"content": [{"type": "text", "text": text}], "isError": True}
    raise LookupError(method)


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        try:
            result = handle(msg)
            if result is None:
                continue
            reply = {"jsonrpc": "2.0", "id": msg["id"], "result": result}
        except LookupError:
            reply = {"jsonrpc": "2.0", "id": msg.get("id"),
                     "error": {"code": -32601, "message": f"Method not found: {msg.get('method')}"}}
        except Exception as e:
            traceback.print_exc(file=sys.stderr)
            reply = {"jsonrpc": "2.0", "id": msg.get("id"),
                     "error": {"code": -32603, "message": str(e)}}
        sys.stdout.write(json.dumps(reply) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
