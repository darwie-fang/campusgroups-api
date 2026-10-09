"""
Client for Columbia GSB CampusGroups (groups.gsb.columbia.edu).

Talks directly to the site's internal JSON endpoints (the ones the web pages
use), with your own logged-in session. Read-only: nothing here books, RSVPs
or changes anything.

Session: `python gsb.py login` opens a browser once so you can sign in with
your UNI + Duo. Only the resulting session cookies are saved, to
~/.gsb-api/session.json (readable only by you). Your password is never seen
or stored. `python gsb.py logout` deletes that file.
"""

from __future__ import annotations

import difflib
import html
import json
import os
import re
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

BASE = "https://groups.gsb.columbia.edu"
TZ = ZoneInfo("America/New_York")
SESSION_DIR = Path.home() / ".gsb-api"
SESSION_FILE = SESSION_DIR / "session.json"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0 Safari/537.36"
)


class SessionExpired(Exception):
    """No saved session, or CampusGroups sent us back to the login page."""


# --------------------------------------------------------------------------
# Login / logout (the only part that uses a browser)
# --------------------------------------------------------------------------

def login(timeout_minutes: int = 5) -> None:
    """Open a real browser window; you sign in with UNI + Duo; we save cookies."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        ctx = browser.new_context()
        page = ctx.new_page()
        page.goto(f"{BASE}/club_signup?all_my_groups=my_groups")
        print("Sign in with your UNI and approve Duo in the browser window...")
        page.wait_for_url(
            lambda url: url.startswith(BASE) and "/auth/login" not in url,
            timeout=timeout_minutes * 60_000,
        )
        page.wait_for_load_state("networkidle")
        SESSION_DIR.mkdir(mode=0o700, exist_ok=True)
        ctx.storage_state(path=str(SESSION_FILE))
        os.chmod(SESSION_FILE, 0o600)
        browser.close()
    print(f"Logged in. Session saved to {SESSION_FILE} (only your user can read it).")


def logout() -> None:
    if SESSION_FILE.exists():
        SESSION_FILE.unlink()
        print("Session deleted.")
    else:
        print("No saved session.")


# --------------------------------------------------------------------------
# Low-level helpers
# --------------------------------------------------------------------------

def _strip_html(s: str | None) -> str:
    s = re.sub(r"<[^>]+>", " ", s or "")
    return re.sub(r"\s+", " ", html.unescape(s)).strip()


def _map_row(row: dict) -> dict:
    """CampusGroups rows list their field names in `fields` and put the
    values in p0, p1, p2... in the same order. Turn that into a normal dict."""
    names = [f for f in (row.get("fields") or "").split(",") if f]
    return {name: row.get(f"p{i}") for i, name in enumerate(names)}


_ARIA_RE = re.compile(
    r"(\d{1,2}) (January|February|March|April|May|June|July|August|September|"
    r"October|November|December) (\d{4})(?: At (\d{1,2}):(\d{2}) (AM|PM))?"
)
_DATE_RE = re.compile(r"[A-Z][a-z]{2}, ([A-Z][a-z]{2}) (\d{1,2}), (\d{4})")


def _event_start(ev: dict) -> datetime | None:
    """Start time of an event, from its accessibility text
    ("Monday, 28 September 2026 At 12:30 PM, EDT") or its date HTML."""
    m = _ARIA_RE.search(ev.get("ariaEventDetails") or "")
    if m:
        d, mon, y, hh, mm, ap = m.groups()
        dt = datetime.strptime(f"{d} {mon} {y}", "%d %B %Y")
        if hh:
            h = int(hh) % 12 + (12 if ap == "PM" else 0)
            dt = dt.replace(hour=h, minute=int(mm))
        return dt.replace(tzinfo=TZ)
    m = _DATE_RE.search(_strip_html(ev.get("eventDates")))
    if m:
        return datetime.strptime(" ".join(m.groups()), "%b %d %Y").replace(tzinfo=TZ)
    return None


def _parse_date(d: str | date | None, default: date) -> date:
    if d is None or d == "":
        return default
    if isinstance(d, date):
        return d
    return date.fromisoformat(d)


# --------------------------------------------------------------------------
# Client
# --------------------------------------------------------------------------

class GSBClient:
    def __init__(self, cache_seconds: int = 1800):
        self.cache_seconds = cache_seconds
        self._cache: dict[str, tuple[float, object]] = {}
        self._s: requests.Session | None = None

    # -- session ----------------------------------------------------------

    def _session(self) -> requests.Session:
        if self._s is not None:
            return self._s
        if not SESSION_FILE.exists():
            raise SessionExpired("Not logged in. Run: python gsb.py login")
        state = json.loads(SESSION_FILE.read_text())
        s = requests.Session()
        s.headers.update({"User-Agent": USER_AGENT, "Referer": f"{BASE}/events",
                          "Accept": "application/json, text/plain, */*"})
        for c in state.get("cookies", []):
            s.cookies.set(c["name"], c["value"], domain=c["domain"], path=c.get("path", "/"))
        self._s = s
        return s

    def _get(self, path: str, **params) -> object:
        r = self._session().get(f"{BASE}{path}", params=params, timeout=20,
                                allow_redirects=False)
        body = r.text.strip()
        if r.status_code in (301, 302, 401, 403) or "/auth/login" in r.headers.get("Location", "") \
                or not body.startswith(("[", "{")):
            self._s = None
            raise SessionExpired("CampusGroups session expired. Run: python gsb.py login")
        return json.loads(body)

    def _get_html(self, path: str, **params) -> str:
        """An HTML page/fragment (forms, details). Raises SessionExpired on a login redirect."""
        r = self._session().get(f"{BASE}{path}", params=params, timeout=20, allow_redirects=False)
        if r.status_code in (301, 302, 401, 403) or "/auth/login" in r.text[:2000]:
            self._s = None
            raise SessionExpired("CampusGroups session expired. Run: python gsb.py login")
        return r.text

    def _post(self, path: str, data: dict, referer: str) -> requests.Response:
        r = self._session().post(f"{BASE}{path}", data=data, timeout=30, allow_redirects=False,
                                 headers={"Referer": f"{BASE}{referer}", "X-Requested-With": "XMLHttpRequest",
                                          "Accept": "text/html, */*"})
        if r.status_code in (301, 302, 401, 403) and "/auth/login" in r.headers.get("Location", ""):
            self._s = None
            raise SessionExpired("CampusGroups session expired. Run: python gsb.py login")
        return r

    def _cached(self, key: str, fn):
        hit = self._cache.get(key)
        if hit and time.time() - hit[0] < self.cache_seconds:
            return hit[1]
        val = fn()
        self._cache[key] = (time.time(), val)
        return val

    def status(self) -> dict:
        try:
            self._get("/mobile_ws/v17/mobile_header_groups", search="", all="false")
            return {"logged_in": True}
        except SessionExpired as e:
            return {"logged_in": False, "detail": str(e)}

    # -- clubs ------------------------------------------------------------

    def my_clubs(self) -> list[dict]:
        def load():
            data = self._get("/mobile_ws/v17/mobile_header_groups", search="", all="false")
            # The site splits your clubs into "Most Recent" (type "last") and "My Groups"
            # (type "group") without repeating them, so read both sections.
            clubs, seen = [], set()
            for section in data:
                if section.get("type") not in ("group", "last"):
                    continue
                for g in section.get("groups", []):
                    if g["groupID"] in seen:
                        continue
                    seen.add(g["groupID"])
                    clubs.append({
                        "id": g["groupID"],
                        "name": html.unescape(g["groupName"]),
                        "short_name": g.get("groupLogin"),
                        "is_officer": bool(g.get("isOfficer")),
                    })
            return clubs
        return self._cached("my_clubs", load)

    # Words that don't identify a club ("golf club" vs "ai club" share "club").
    _GENERIC = {"club", "clubs", "association", "society", "the", "of", "and", "for", "in",
                "cbs", "gsb", "columbia", "business", "school", "group", "student"}

    @classmethod
    def _words(cls, name: str, keep_generic: bool = False) -> list[str]:
        words = re.findall(r"[a-z0-9]+", html.unescape(name or "").lower())
        return words if keep_generic else [w for w in words if w not in cls._GENERIC]

    def _all_groups_matching(self, query: str) -> list[dict]:
        """Clubs you're not a member of: ask the site's group search, and also use
        the clubs that appear in the upcoming all-events feed."""
        found: dict = {}
        try:
            data = self._get("/mobile_ws/v17/mobile_header_groups", search=query, all="true")
            for section in data if isinstance(data, list) else []:
                for g in section.get("groups", []):
                    found.setdefault(g["groupID"], {
                        "id": g["groupID"], "name": html.unescape(g["groupName"]),
                        "short_name": g.get("groupLogin"), "is_officer": False})
        except SessionExpired:
            raise
        except Exception:
            pass
        try:
            end = datetime.now(TZ).date() + timedelta(days=14)
            self.all_events(end=end, max_pages=15)          # fills the cache below
            raw_rows = self._cache.get(f"all:{end.isoformat()}", (0, []))[1]
        except SessionExpired:
            raise
        except Exception:
            raw_rows = []
        for raw in raw_rows:
            cid = raw.get("clubId")
            if cid and raw.get("clubName"):
                try:
                    cid = int(cid)
                except ValueError:
                    pass
                found.setdefault(cid, {"id": cid, "name": html.unescape(raw["clubName"]),
                                       "short_name": raw.get("clubLogin"), "is_officer": False})
        return list(found.values())

    def _match(self, s: str, clubs: list[dict], fuzzy: bool) -> dict | None:
        q_all = self._words(s, keep_generic=True)
        q = self._words(s) or q_all
        q_str = "".join(q)
        for c in clubs:   # exact id / name / short name
            if s in (str(c["id"]), (c["name"] or "").lower(), (c["short_name"] or "").lower()):
                return c
        for c in clubs:   # initials: "aba" -> Asian Business Association, "ai club" -> Artificial Intelligence Club
            full = "".join(w[0] for w in self._words(c["name"], keep_generic=True))
            core = "".join(w[0] for w in self._words(c["name"]))
            if q_str and q_str in (full, core) and len(q_str) >= 2:
                return c
        for c in clubs:   # every meaningful word appears: "tech" -> Technology Club
            words = self._words(c["name"])
            if q and all(any(w.startswith(t) for w in words) for t in q):
                return c
        if fuzzy and q:
            names = {" ".join(self._words(c["name"])): c for c in clubs}
            close = difflib.get_close_matches(" ".join(q), list(names), n=1, cutoff=0.8)
            if close:
                return names[close[0]]
        return None

    def resolve_club(self, club: str | int) -> dict:
        """Accepts an ID, full name, short name, initials, or something close ("ai club").
        Looks at your own clubs first, then every club on CampusGroups."""
        s = str(club).strip().lower()
        mine = self.my_clubs()
        hit = self._match(s, mine, fuzzy=False)
        if hit:
            return hit
        if s.isdigit():   # an ID for a club you're not in
            return {"id": int(s), "name": None, "short_name": None, "is_officer": False}
        query = " ".join(self._words(s)) or s
        others = self._all_groups_matching(query)
        hit = self._match(s, others, fuzzy=False) or self._match(s, mine + others, fuzzy=True)
        if hit:
            return hit
        raise KeyError(f"Couldn't find a club matching '{club}'. Try its full name, "
                       "or check the spelling.")

    # -- events -----------------------------------------------------------

    @staticmethod
    def _clean_event(ev: dict, club_name: str | None = None) -> dict:
        start = _event_start(ev)
        price = _strip_html(ev.get("eventPriceRange"))
        registered = ev.get("registered")
        event_id = ev.get("eventId") or ev.get("id")
        # Tags come as <span class="label-tag">Tag</span> ... ; one chunk per tag.
        tags = [_strip_html(chunk.split(">", 1)[-1].split('<span class="label')[0])
                for chunk in (ev.get("eventTags") or "").split("label-tag")[1:]]
        url = ev.get("eventUrl") or f"/rsvp_boot?id={event_id}"
        return {
            "id": event_id,
            "title": html.unescape(ev.get("eventName") or ev.get("event_name") or ""),
            "club": html.unescape(ev.get("clubName") or club_name or ""),
            "start": start.isoformat() if start else None,
            "when": _strip_html(ev.get("eventDates")),
            "location": _strip_html(ev.get("eventLocation")),
            "tags": [t for t in tags if t],
            "price": price or None,
            "registration": ev.get("eventButtonLabel"),   # "Register" / "View" / ...
            "attendees": ev.get("eventAttendees"),
            "registered": (str(registered) in ("1", "true", "True")) if registered is not None else None,
            "url": url if url.startswith("http") else f"{BASE}{url}",
        }

    def club_events(self, club: str | int, start: str | date | None = None,
                    end: str | date | None = None) -> dict:
        c = self.resolve_club(club)
        today = datetime.now(TZ).date()
        d0 = _parse_date(start, today)
        d1 = _parse_date(end, d0 + timedelta(days=7))

        def load():
            out, rng = [], 0
            while rng < 400:
                rows = self._get("/mobile_ws/v17/mobile_group_page_events", range=rng,
                                 limit=40, order="", search_word="", param=c["id"])
                evs = [_map_row(r) for r in rows if isinstance(r, dict)]
                evs = [e for e in evs if e.get("eventDates")]
                if not evs:
                    break
                out.extend(evs)
                rng += len(rows)
            return out

        raw = self._cached(f"club:{c['id']}", load)
        events = [self._clean_event(e, c["name"]) for e in raw]
        events = [e for e in events if e["start"] and d0 <= date.fromisoformat(e["start"][:10]) <= d1]
        return {"club": c, "start": d0.isoformat(), "end": d1.isoformat(), "events": events}

    def all_events(self, start: str | date | None = None, end: str | date | None = None,
                   club: str | None = None, q: str | None = None, tag: str | None = None,
                   registered_only: bool = False, max_pages: int = 60) -> dict:
        today = datetime.now(TZ).date()
        d0 = _parse_date(start, today)
        d1 = _parse_date(end, d0 + timedelta(days=7))

        def load():
            # The feed starts at "now" and scrolls forward ~20 rows at a time;
            # `range` is the row offset. Page until we pass the end date.
            seen, out, rng = set(), [], 0
            for _ in range(max_pages):
                rows = self._get("/mobile_ws/v17/mobile_events_list", range=rng, limit=40,
                                 filter4_contains="OR", filter4_notcontains="OR",
                                 order="undefined", search_word="")
                if not rows:
                    break
                rng += len(rows)
                evs = [_map_row(r) for r in rows if isinstance(r, dict)]
                evs = [e for e in evs if e.get("eventDates")]
                past_end = False
                for e in evs:
                    key = e.get("eventId")
                    if key in seen:
                        continue
                    seen.add(key)
                    out.append(e)
                    st = _event_start(e)
                    if st and st.date() > d1:
                        past_end = True
                if past_end:
                    break
            return out

        raw = self._cached(f"all:{d1.isoformat()}", load)
        events = [self._clean_event(e) for e in raw]
        events = [e for e in events if e["start"] and d0 <= date.fromisoformat(e["start"][:10]) <= d1]
        if club:
            try:
                name = (self.resolve_club(club)["name"] or "").lower()
            except KeyError:
                name = club.lower()
            events = [e for e in events if name and (name in e["club"].lower() or club.lower() in e["club"].lower())]
        if q:
            ql = q.lower()
            events = [e for e in events if ql in e["title"].lower() or ql in e["club"].lower()]
        if tag:
            tl = tag.lower()
            events = [e for e in events if any(tl in t.lower() for t in e["tags"])]
        if registered_only:
            events = [e for e in events if e["registered"]]
        return {"start": d0.isoformat(), "end": d1.isoformat(), "count": len(events), "events": events}

    # -- rooms ------------------------------------------------------------

    def _rooms_raw(self, day: date) -> list[dict]:
        """Every room with its bookings for the week (Sun-Sat) containing `day`.
        CampusGroups' date box (filter8) picks the week; any date works."""
        week_start = day - timedelta(days=(day.weekday() + 1) % 7)

        def load():
            seen, out, rng = set(), [], 0
            while rng < 1000:
                rows = self._get("/mobile_ws/v17/mobile_room_availability_calendar", range=rng,
                                 limit=200, filter1=1, filter7=1, filter8=day.isoformat(),
                                 order="undefined", search_word="")
                new = [_map_row(r) for r in rows if isinstance(r, dict)]
                new = [r for r in new if r.get("checkbox_id") not in seen]
                if not new:
                    break
                for r in new:
                    seen.add(r.get("checkbox_id"))
                out.extend(new)
                rng += len(rows)
            return out
        return self._cached(f"rooms:{week_start.isoformat()}", load)

    def free_rooms(self, day: str | date | None = None, start: str | None = None,
                   end: str | None = None, building: str | None = None,
                   min_capacity: int | None = None, room_type: str | None = None,
                   min_minutes: int = 30) -> dict:
        """Free time on `day`. If start/end ("14:00") are given, only rooms free
        for that whole window. Bookers' names in the source data are discarded."""
        d = _parse_date(day, datetime.now(TZ).date())
        if d < datetime.now(TZ).date():
            raise ValueError(f"{d} is in the past.")
        rooms = self._rooms_raw(d)

        covered = set()
        parsed = []
        for r in rooms:
            try:
                blocks = json.loads(r.get("roomSchedule") or "[]")
            except ValueError:
                blocks = []
            busy = []
            for b in blocks:  # keep only times; drop b["title"] (a person's name/UNI)
                s = datetime.fromtimestamp(b["startEpoch"] / 1000, TZ)
                e = datetime.fromtimestamp(b["endEpoch"] / 1000, TZ)
                covered.add(s.date())
                busy.append((s, e))
            parsed.append((r, sorted(busy)))

        if covered and d not in covered:
            raise ValueError(f"CampusGroups didn't return a schedule for {d}. Try another date.")

        def hour(v, default):
            try:
                return int(float(v))
            except (TypeError, ValueError):
                return default

        want_s = want_e = None
        if start:
            want_s = datetime.combine(d, datetime.strptime(start, "%H:%M").time(), TZ)
            want_e = datetime.combine(d, datetime.strptime(end or start, "%H:%M").time(), TZ)
            if not end:
                want_e = want_s + timedelta(hours=1)

        results = []
        for r, busy in parsed:
            cap_txt = _strip_html(r.get("capacity"))
            cap = int(re.search(r"\d+", cap_txt).group()) if re.search(r"\d+", cap_txt) else None
            info = {
                "id": r.get("checkbox_id"),
                "room": html.unescape(r.get("name") or ""),
                "building": _strip_html(r.get("building")),
                "floor": _strip_html(r.get("floor")),
                "type": _strip_html(r.get("room_type")),
                "capacity": cap,
            }
            if building and building.lower() not in (info["building"] + " " + info["room"]).lower():
                continue
            if min_capacity and (cap is None or cap < min_capacity):
                continue
            if room_type and room_type.lower() not in info["type"].lower():
                continue
            # CMC interview rooms are for recruiting interviews; only show them when asked for.
            if not room_type and "interview" in info["type"].lower():
                continue

            open_s = datetime.combine(d, datetime.min.time(), TZ) + timedelta(
                hours=hour(r.get("roomCalendarStartHour"), 8))
            open_e = datetime.combine(d, datetime.min.time(), TZ) + timedelta(
                hours=hour(r.get("roomCalendarEndHour"), 22))
            day_busy = [(s, e) for s, e in busy if e > open_s and s < open_e]

            free, cur = [], open_s
            for s, e in day_busy:
                if s > cur:
                    free.append((cur, s))
                cur = max(cur, e)
            if cur < open_e:
                free.append((cur, open_e))
            free = [(s, e) for s, e in free if (e - s) >= timedelta(minutes=min_minutes)]

            if want_s:
                if not any(s <= want_s and e >= want_e for s, e in free):
                    continue
            info["free"] = [f"{s:%H:%M}-{e:%H:%M}" for s, e in free]
            if info["free"]:
                results.append(info)

        out = {"date": d.isoformat(), "window": [start, end] if start else None,
               "count": len(results), "rooms": results}
        if not results and not start:
            out["note"] = ("No room has free time that day. Either booking for that day hasn't opened "
                           "yet (study rooms open only a few days ahead) or the rooms are closed "
                           "(e.g. weekends). Try a nearer date.")
        if (d - datetime.now(TZ).date()).days > 14:
            out["note_booking"] = ("Shown as free on the calendar, but CampusGroups may not accept "
                                   "bookings this far ahead.")
        return out

    # -- my stuff ---------------------------------------------------------

    def my_reservations(self) -> list[dict]:
        """Your upcoming room reservations."""
        rows = self._get("/mobile_ws/v17/mobile_user_rooms_reservations", range=0, limit=50,
                         filter1="upcoming", order="", search_word="")
        out = []
        for r in rows if isinstance(rows, list) else []:
            m = _map_row(r) if isinstance(r, dict) else {}
            if not m.get("room_reservations_id"):
                continue
            out.append({
                "id": str(m.get("room_reservations_id")),
                "room": _strip_html(m.get("room_name")),
                "start": _strip_html(m.get("reservation_date")),
                "title": _strip_html(m.get("name")),
                "status": _strip_html(m.get("reservation_status")),
                "can_cancel": str(m.get("show_cancel_button")) == "1",
            })
        return out

    # -- booking (changes things on CampusGroups) ---------------------------

    MAX_BOOKING_MINUTES = 240

    def _find_room(self, room: str | int, day: date) -> dict:
        """Match a room by id, number ("504") or name ("Geffen 504") in that week's list."""
        rooms = self._rooms_raw(day)
        q = str(room).strip().lower()
        for r in rooms:
            if q == str(r.get("checkbox_id")):
                return r
        for r in rooms:
            name = html.unescape(r.get("name") or "").lower()
            if name == q or name.split(" - ")[0].strip() == q:
                return r
        hits = [r for r in rooms if re.search(rf"\b{re.escape(q)}\b", html.unescape(r.get("name") or "").lower())]
        if len(hits) == 1:
            return hits[0]
        if len(hits) > 1:
            names = ", ".join(html.unescape(h.get("name") or "") for h in hits[:6])
            raise ValueError(f"'{room}' matches several rooms ({names}). Say which one.")
        raise KeyError(f"No bookable room matches '{room}'.")

    def book_room(self, room: str | int, day: str | date, start: str, title: str,
                  minutes: int | None = None, end: str | None = None) -> dict:
        """Book a room. `start`/`end` are "HH:MM" (24h). Checks it's free first and
        confirms the booking appears in your reservations afterwards."""
        d = _parse_date(day, datetime.now(TZ).date())
        title = (title or "").strip()
        if not title:
            raise ValueError("A title is required (it's the booking's purpose, e.g. 'Study').")
        try:
            t0 = datetime.strptime(start, "%H:%M")
        except ValueError:
            raise ValueError("Start time must be HH:MM in 24-hour time, e.g. 15:00.")
        if end:
            minutes = int((datetime.strptime(end, "%H:%M") - t0).total_seconds() // 60)
        if not minutes or minutes <= 0:
            raise ValueError("Give an end time or a duration.")
        if t0.minute % 5:
            raise ValueError("Start time must be on a 5-minute mark (e.g. 15:00, 15:05).")
        if minutes % 15:
            raise ValueError("Duration must be in 15-minute steps (e.g. 30, 45, 60).")
        if minutes > self.MAX_BOOKING_MINUTES:
            raise ValueError(f"That's longer than {self.MAX_BOOKING_MINUTES // 60} hours; book a shorter slot.")
        starts = datetime.combine(d, t0.time(), TZ)
        if starts < datetime.now(TZ):
            raise ValueError("That time has already passed.")

        # Fresh availability (not cached) so we don't book over someone.
        week = d - timedelta(days=(d.weekday() + 1) % 7)
        self._cache.pop(f"rooms:{week.isoformat()}", None)
        r = self._find_room(room, d)
        room_id, room_name = str(r.get("checkbox_id")), html.unescape(r.get("name") or "")
        ends = starts + timedelta(minutes=minutes)
        for b in json.loads(r.get("roomSchedule") or "[]"):
            bs = datetime.fromtimestamp(b["startEpoch"] / 1000, TZ)
            be = datetime.fromtimestamp(b["endEpoch"] / 1000, TZ)
            if bs < ends and be > starts:
                why = ("not open for booking then" if b.get("title") == "Unavailable"
                       else f"already booked {bs:%H:%M}-{be:%H:%M}")
                raise ValueError(f"{room_name} isn't free {starts:%H:%M}-{ends:%H:%M} on {d:%a %b %-d}: {why}.")

        form = self._get_html("/room_reservation_form", ax=1, room=room_id, duration=minutes)
        tag = re.search(r'<input[^>]*name=["\']_csrf["\'][^>]*>', form)
        m = tag and re.search(r'value=["\']([^"\']+)["\']', tag.group(0))
        if not m:
            raise RuntimeError("Couldn't read the booking form from CampusGroups (it may have changed).")
        h12 = starts.strftime("%I")
        data = {"_csrf": m.group(1), "update": "1", "room": room_id,
                "start": starts.strftime("%d %b %y"), "start_hour": h12,
                "start_minute": starts.strftime("%M"), "start_ampm": starts.strftime("%p"),
                "duration": str(minutes), "name": title}
        resp = self._post("/room_reservation_form", data, referer="/room_availability_calendar")
        self._cache.pop(f"rooms:{week.isoformat()}", None)

        # Confirm: the new booking should now be in your reservations.
        want = f"{starts:%b} {starts.day}, {starts.year} {starts:%-I:%M %p}"
        for res in self.my_reservations():
            if res["room"].lower().startswith(room_name.split(" - ")[0].lower()) and want in res["start"]:
                return {"booked": True, "reservation": res, "room": room_name,
                        "date": d.isoformat(), "start": f"{starts:%H:%M}", "end": f"{ends:%H:%M}"}
        detail = _strip_html(resp.text)[:300]
        raise RuntimeError("CampusGroups didn't confirm the booking (it's not in your reservations). "
                           f"The site said: {detail or 'nothing'}")

    def cancel_booking(self, reservation_id: str | int) -> dict:
        """Cancel one of your room reservations by its id (from my_reservations)."""
        rid = str(reservation_id).strip()
        mine = {res["id"]: res for res in self.my_reservations()}
        if rid not in mine:
            raise KeyError(f"No upcoming reservation of yours has id {rid}.")
        if not mine[rid]["can_cancel"]:
            raise ValueError("CampusGroups doesn't allow cancelling that reservation here.")
        page = self._get_html("/room_reservation_details", ax=1, id=rid)
        m = re.search(r'_csrf:\s*"([^"]+)"', page)
        if not m:
            raise RuntimeError("Couldn't read the cancel option from CampusGroups (it may have changed).")
        self._post("/room_reservation_details", {"id": rid, "action": "cancel", "_csrf": m.group(1)},
                   referer="/user_rooms_reservations")
        self._cache = {k: v for k, v in self._cache.items() if not k.startswith("rooms:")}
        still = [res for res in self.my_reservations() if res["id"] == rid and res["status"].lower() != "cancelled"]
        if still:
            raise RuntimeError("CampusGroups didn't confirm the cancellation; the reservation is still listed.")
        return {"cancelled": True, "reservation": mine[rid]}
