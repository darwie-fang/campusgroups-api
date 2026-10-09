"""
Your personal CampusGroups API.

Run:   uvicorn api:app --port 8765
Docs:  http://127.0.0.1:8765/docs   (try every endpoint in the browser)

It only listens on your own computer (127.0.0.1), because anyone who can reach
it can read CampusGroups as you.
"""

from fastapi import FastAPI, HTTPException, Query

from gsb_client import GSBClient, SessionExpired

app = FastAPI(
    title="GSB CampusGroups API",
    description="Read-only access to your clubs, events, rooms and reservations.",
    version="0.1",
)
gsb = GSBClient()


def _call(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except SessionExpired as e:
        raise HTTPException(401, str(e))
    except KeyError as e:
        raise HTTPException(404, str(e).strip("'\""))
    except ValueError as e:
        raise HTTPException(422, str(e))


@app.get("/status", summary="Is the saved CampusGroups session still valid?")
def status():
    return gsb.status()


@app.get("/clubs", summary="Clubs you're a member of")
def clubs():
    return _call(gsb.my_clubs)


@app.get("/clubs/{club}/events", summary="One club's events",
         description="`club` can be an ID (24308), full name, short name, or something close like 'ai club'.")
def club_events(club: str,
                start: str | None = Query(None, description="YYYY-MM-DD, default today"),
                end: str | None = Query(None, description="YYYY-MM-DD, default start + 7 days")):
    return _call(gsb.club_events, club, start, end)


@app.get("/events", summary="Events across all clubs",
         description="Pages through the site-wide feed. The first call for a date range "
                     "takes a few seconds; results are cached for 30 minutes.")
def events(start: str | None = Query(None, description="YYYY-MM-DD, default today"),
           end: str | None = Query(None, description="YYYY-MM-DD, default start + 7 days"),
           club: str | None = Query(None, description="Filter by club name"),
           q: str | None = Query(None, description="Search in title / club"),
           tag: str | None = Query(None, description="e.g. 'Artificial Intelligence', 'Venture Capital'"),
           registered_only: bool = Query(False, description="Only events you're registered for")):
    return _call(gsb.all_events, start, end, club, q, tag, registered_only)


@app.get("/rooms/free", summary="Free rooms",
         description="Without start/end: every room's free slots that day. With them: only rooms "
                     "free for that whole window. Booker names are never returned.")
def free_rooms(date: str | None = Query(None, description="YYYY-MM-DD, default today"),
               start: str | None = Query(None, description="HH:MM, e.g. 14:00"),
               end: str | None = Query(None, description="HH:MM, e.g. 16:00"),
               building: str | None = Query(None, description="e.g. Geffen, Kravis"),
               min_capacity: int | None = Query(None, description="e.g. 6"),
               type: str | None = Query(None, description="e.g. 'Study', 'Phone Booth'")):
    return _call(gsb.free_rooms, date, start, end, building, min_capacity, type)


@app.get("/me/reservations", summary="Your upcoming room reservations")
def my_reservations():
    return _call(gsb.my_reservations)
