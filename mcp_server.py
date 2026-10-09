"""
Lets the Claude desktop app use your CampusGroups API as tools, so you can ask
in plain English: "what's the AI Club running next week?"

This is a small MCP server (the plug-in format Claude uses for tools). The
Claude app starts it on your Mac when needed; you don't run it yourself.
It's read-only and uses the session saved by `python3 gsb.py login`.

No extra packages: it speaks MCP's JSON-RPC over stdin/stdout directly, so it
works on Python 3.9.
"""

import json
import os
import sys
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gsb_client import GSBClient, SessionExpired  # noqa: E402

SERVER = {"name": "campusgroups", "version": "0.1"}
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
                       "window. Only covers roughly the current week. Read-only: it cannot book.",
        "inputSchema": {"type": "object", "properties": {
            "date": DATE, "start": TIME, "end": TIME,
            "building": {"type": "string", "description": "e.g. Geffen, Kravis"},
            "min_capacity": {"type": "integer"},
            "room_type": {"type": "string", "description": "e.g. Study, Phone Booth, Classroom"}}},
    },
    {
        "name": "my_room_reservations",
        "description": "The user's upcoming room reservations.",
        "inputSchema": {"type": "object", "properties": {}},
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
            text = ("The CampusGroups login has expired. Ask the user to open Terminal and run:\n"
                    "cd ~/Downloads/gsb-api && source .venv/bin/activate && python3 gsb.py login")
        except (KeyError, ValueError) as e:
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
