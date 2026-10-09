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
            clubs = []
            for section in data:
                if section.get("type") != "group":   # "My Groups" (skip "Most Recent")
                    continue
                for g in section.get("groups", []):
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

    def _rooms_raw(self) -> list[dict]:
        def load():
            seen, out, rng = set(), [], 0
            while rng < 1000:
                rows = self._get("/mobile_ws/v17/mobile_room_availability_calendar", range=rng,
                                 limit=200, filter1=1, filter7=1, order="undefined", search_word="")
                new = [_map_row(r) for r in rows if isinstance(r, dict)]
                new = [r for r in new if r.get("checkbox_id") not in seen]
                if not new:
                    break
                for r in new:
                    seen.add(r.get("checkbox_id"))
                out.extend(new)
                rng += len(rows)
            return out
        return self._cached("rooms", load)

    def free_rooms(self, day: str | date | None = None, start: str | None = None,
                   end: str | None = None, building: str | None = None,
                   min_capacity: int | None = None, room_type: str | None = None,
                   min_minutes: int = 30) -> dict:
        """Free time on `day`. If start/end ("14:00") are given, only rooms free
        for that whole window. Bookers' names in the source data are discarded."""
        d = _parse_date(day, datetime.now(TZ).date())
        rooms = self._rooms_raw()

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
            raise ValueError(
                f"{d} is outside the window CampusGroups currently shows "
                f"({min(covered)} to {max(covered)}).")

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

        return {"date": d.isoformat(), "window": [start, end] if start else None,
                "count": len(results), "rooms": results}

    # -- my stuff ---------------------------------------------------------

    def my_reservations(self) -> list[dict]:
        rows = self._get("/mobile_ws/v17/mobile_user_rooms_reservations", range=0, limit=50,
                         filter1="upcoming", order="", search_word="")
        # Field layout not seen yet (you had no reservations) - return mapped rows,
        # with HTML stripped, so whatever the site sends comes through readably.
        out = []
        for r in rows if isinstance(rows, list) else []:
            m = _map_row(r) if isinstance(r, dict) else {}
            out.append({k: _strip_html(v) if isinstance(v, str) else v for k, v in m.items()})
        return out
