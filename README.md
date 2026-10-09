# GSB CampusGroups API

A small, read-only API for your own CampusGroups account at groups.gsb.columbia.edu.
It calls the same internal JSON endpoints the website uses. There's no scraping and no browser, except for logging in.

## Setup (once)

```bash
cd gsb-api
python3 -m pip install -r requirements.txt
python3 -m playwright install chromium
python3 gsb.py login      # a browser opens: sign in with your UNI + approve Duo
```

`login` saves **only session cookies** to `~/.gsb-api/session.json` (readable only by you).
Your password is never seen or stored. `python3 gsb.py logout` deletes the file.
When the session expires, calls return **401**. Run `python3 gsb.py login` again.

## Run the API

```bash
python3 gsb.py serve
```

Then open **http://127.0.0.1:8765/docs** to try every endpoint in your browser.
It only listens on your own computer. Don't expose it: anyone who can reach it can read CampusGroups as you.

| Endpoint | What it answers |
|---|---|
| `GET /status` | Is my saved session still valid? |
| `GET /clubs` | Which clubs am I in? |
| `GET /clubs/{club}/events?start=&end=` | "What's the AI Club running next week?" (`club` = ID, name, or "ai club") |
| `GET /events?start=&end=&club=&q=&tag=&registered_only=` | Everything across all clubs, filterable |
| `GET /rooms/free?date=&start=&end=&building=&min_capacity=&type=` | "Free 6-person room in Geffen tomorrow 14:00–16:00?" |
| `GET /me/reservations` | My upcoming room reservations |

Dates are `YYYY-MM-DD`, times `HH:MM`. Date ranges default to today → +7 days.

Examples:
```
/clubs/ai club/events
/events?start=2026-10-05&end=2026-10-11&tag=Venture Capital
/events?registered_only=true
/rooms/free?date=2026-09-29&start=14:00&end=16:00&building=Geffen&min_capacity=6
```

## Or skip the server

```bash
python3 gsb.py clubs
python3 gsb.py events "ai club" 2026-09-28 2026-10-05
python3 gsb.py all-events 2026-10-05 2026-10-11
python3 gsb.py rooms 2026-09-29 14:00 16:00 Geffen
python3 gsb.py reservations
```

## Good to know

- **Read-only.** Nothing here RSVPs, books, or changes anything.
- **All-events** pages through a scrolling feed (~20 events per request) until it passes your end date. A week takes a few seconds the first time. Results are cached for 30 minutes.
- **Rooms** only cover the window CampusGroups shows (about the current week). Asking for a date outside it returns a clear error instead of pretending rooms are free.
- The source room data includes other students' names. This API drops them and returns only free/busy times.
- These are undocumented endpoints. If CampusGroups changes them, the parsing in `gsb_client.py` may need updating.
- `/me/reservations` returns the raw fields for now, because the format wasn't visible yet (you had none).

## Files

- `gsb_client.py`: login, session, endpoint calls, parsing (all the logic)
- `api.py`: the FastAPI endpoints
- `gsb.py`: command line (`login`, `logout`, `serve`, quick queries)
