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
MYCBS = "https://mycbs.gsb.columbia.edu"
TWELVE = "https://business-columbia.12twenty.com"
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
        # myCBS (people directory) has its own login, through the same Columbia SSO.
        # Usually no second Duo push, since you just signed in.
        try:
            page.goto(f"{MYCBS}/people")
            page.wait_for_load_state("networkidle")
            if "/login" in page.url:
                import re as _re
                # The cookie banner can sit on top of the page and swallow the click.
                # It can appear a few seconds after the page, so wait for it.
                for loc in (page.get_by_role("button", name=_re.compile(r"^\s*reject all\s*$", _re.I)),
                            page.get_by_text(_re.compile(r"^\s*reject all\s*$", _re.I))):
                    try:
                        loc.first.click(timeout=8000)
                        break
                    except Exception:
                        pass
                # The button has been "Sign in with your SSO Account" and is now
                # "Alumni/UNI Login"; match either (link or button).
                sso = _re.compile(r"alumni\s*/\s*uni login|uni login|sign in with your sso|sso", _re.I)
                clicked = False
                for loc in (page.get_by_role("button", name=sso), page.get_by_role("link", name=sso),
                            page.get_by_text(sso)):
                    try:
                        loc.first.click(timeout=4000)
                        clicked = True
                        break
                    except Exception:
                        try:                        # something still on top: click through it
                            loc.first.click(timeout=2000, force=True)
                            clicked = True
                            break
                        except Exception:
                            pass
                if not clicked:
                    print("Click 'Alumni/UNI Login' in the browser window to finish.")
                try:
                    page.wait_for_url(lambda url: url.startswith(MYCBS) and "/login" not in url,
                                      timeout=20_000)
                except Exception:
                    # Auto-click didn't get through: ask the user, but don't block the
                    # rest of the login (12twenty) for long. myCBS is only for email lookup.
                    print("\n  >>> In the browser window, click 'Alumni/UNI Login' on myCBS.")
                    print("      (Skipping it in 2 minutes if not; only classmate email lookup needs it.)\n")
                    page.wait_for_url(lambda url: url.startswith(MYCBS) and "/login" not in url,
                                      timeout=120_000)
                page.wait_for_load_state("networkidle")
        except Exception:
            print("Note: couldn't sign in to myCBS, so looking up classmates' emails won't work. "
                  "Everything else will. Run the login again to retry.")
        # 12twenty (career management: jobs, OCI, events), same Columbia SSO. Don't trust
        # the URL: keep asking 12twenty "who am I?" until it knows you, clicking its
        # sign-in button if one shows up.
        try:
            import re as _re
            page.goto(f"{TWELVE}/dashboard")
            deadline, clicked = time.time() + 120, False
            while not _tw_page_signed_in(page):
                if time.time() > deadline:
                    raise TimeoutError
                if not clicked and page.url.startswith(TWELVE):
                    for pat in ("sso", "columbia", "sign in", "log in", "login"):
                        try:
                            page.get_by_role("link", name=_re.compile(pat, _re.I)).or_(
                                page.get_by_role("button", name=_re.compile(pat, _re.I))).first.click(timeout=1500)
                            clicked = True
                            break
                        except Exception:
                            pass
                page.wait_for_timeout(2000)
            page.wait_for_load_state("networkidle")
        except Exception:
            print("Note: couldn't sign in to 12twenty (events and CMC appointments won't work). "
                  "Everything else works; run the login again to retry.")
        SESSION_DIR.mkdir(mode=0o700, exist_ok=True)
        ctx.storage_state(path=str(SESSION_FILE))
        os.chmod(SESSION_FILE, 0o600)
        browser.close()
    print(f"Logged in. Session saved to {SESSION_FILE} (only your user can read it).")


def _tw_page_signed_in(page) -> bool:
    """True once 12twenty's API recognises you in the login browser."""
    try:
        if not page.url.startswith(TWELVE):
            return False
        return bool(page.evaluate("""async () => {
            const r = await fetch('/api/v2/account/current-user', {headers: {Accept: 'application/json'}});
            if (r.status !== 200) return false;
            const t = await r.text(); if (!t.trim().startsWith('{')) return false;
            const j = JSON.parse(t); return !!(j && j.Id);
        }"""))
    except Exception:
        return False


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
        """JSON from CampusGroups. Only a redirect to the login page (or 401/403) means
        you're signed out; anything else odd (a busy-server error page, an empty reply)
        is retried once and then reported as a hiccup, not as 'log in again'."""
        for attempt in range(2):
            r = self._session().get(f"{BASE}{path}", params=params, timeout=20,
                                    allow_redirects=False)
            body = r.text.strip()
            loc = r.headers.get("Location", "")
            if r.status_code in (401, 403) or (r.status_code in (301, 302) and
                                               ("login" in loc.lower() or "/auth" in loc)):
                self._s = None
                raise SessionExpired("CampusGroups session expired. Run: python gsb.py login")
            if r.status_code == 200 and body.startswith(("[", "{")):
                return json.loads(body)
            if "/auth/login" in body[:3000]:
                self._s = None
                raise SessionExpired("CampusGroups session expired. Run: python gsb.py login")
            time.sleep(1.5)
        raise RuntimeError(f"CampusGroups didn't answer properly (status {r.status_code}); "
                           "it may be busy. Try again in a moment.")

    def _require_login(self) -> None:
        """The events feed still answers when you're signed out, but leaves out private and
        members-only events, so a lapsed login looks like 'fewer events', not an error.
        Check the login first (cached for 10 minutes)."""
        hit = self._cache.get("login-ok")
        if hit and time.time() - hit[0] < 600:
            return
        self._get("/mobile_ws/v17/mobile_header_groups", search="", all="false")
        self._cache["login-ok"] = (time.time(), True)

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
        except SessionExpired as e:
            return {"logged_in": False, "detail": str(e)}
        try:
            self._mycbs_search("zz-login-check")      # a name nobody has; just tests the session
            directory = True
        except SessionExpired:
            directory = False
        except Exception:
            directory = None                           # site trouble, not a login problem
        try:
            self._tw_student_id()                      # signed out = empty answer, not an error
            careers = True
        except SessionExpired:
            careers = False
        except Exception:
            careers = None
        return {"logged_in": True, "directory": directory, "careers": careers}

    # -- myCBS people directory (one person at a time) ----------------------

    LOOKUP_LIMIT_PER_HOUR = 20
    LOOKUP_LOG = SESSION_DIR / "lookups.json"     # only hashed names + times, never names/emails

    def _mycbs_search(self, who: str) -> list:
        params = {"page": 1, "per_page": 18, "sort_by": "last_seen_at", "order": "desc",
                  "source": "people_directory_list",
                  "query[exclude_current_user]": "false", "query[last_location]": "false",
                  "query[include_users_with_no_locations]": "false", "query[who]": who}
        data = self._mycbs_get("/frontoffice/api/users", params)
        if isinstance(data, dict):
            for key in ("users", "data", "results", "items", "records"):
                if isinstance(data.get(key), list):
                    return data[key]
            return []
        return data if isinstance(data, list) else []

    def _mycbs_get(self, path: str, params: dict) -> object:
        r = self._session().get(f"{MYCBS}{path}", params=params, timeout=20, allow_redirects=False,
                                headers={"Accept": "application/json", "Referer": f"{MYCBS}/people"})
        if r.status_code in (301, 302, 401, 403) or not r.text.strip().startswith(("[", "{")):
            raise SessionExpired("myCBS session expired. Run the login again.")
        return r.json()

    @staticmethod
    def _person_summary(u: dict) -> dict:
        """Name, class year and email from a directory record (attributes are a list of
        {attribute_type, display_name, value})."""
        def walk(o):
            if isinstance(o, dict):
                yield o
                for v in o.values():
                    yield from walk(v)
            elif isinstance(o, list):
                for v in o:
                    yield from walk(v)
        email = year = None
        for d in walk(u):
            if d.get("attribute_type") == "email" and isinstance(d.get("value"), str) and not email:
                email = d["value"].strip()
            if re.search(r"class year|affiliation", str(d.get("display_name", "")), re.I) and d.get("value") and not year:
                year = _strip_html(str(d["value"]))
        first = u.get("first_name") or u.get("firstname") or u.get("firstName")
        last = u.get("last_name") or u.get("lastname") or u.get("lastName")
        name = (u.get("full_name") or u.get("fullName") or u.get("display_name") or u.get("name")
                or " ".join(x for x in (first, last) if x) or "")
        name = str(name).strip()
        return {"name": html.unescape(name), "class": year, "email": email}

    def _check_lookup_limit(self, key: str) -> None:
        import hashlib
        h = hashlib.sha256(key.encode()).hexdigest()
        now = time.time()
        try:
            log = json.loads(self.LOOKUP_LOG.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            log = {}
        log = {k: t for k, t in log.items() if now - t < 3600}
        if h not in log and len(log) >= self.LOOKUP_LIMIT_PER_HOUR:
            raise ValueError(f"You've looked up {self.LOOKUP_LIMIT_PER_HOUR} different people in the "
                             "last hour. That's the limit; try again a bit later.")
        log[h] = log.get(h, now)
        try:
            SESSION_DIR.mkdir(mode=0o700, exist_ok=True)
            self.LOOKUP_LOG.write_text(json.dumps(log), encoding="utf-8")
        except OSError:
            pass

    def find_person_email(self, name: str, class_year: str | None = None) -> dict:
        """Email of ONE named person from the myCBS directory. Needs a first and last
        name; returns an email only for a single exact match. Nothing is saved."""
        words = re.findall(r"[^\W\d_][\w'\-.]*", name or "")
        if len(words) < 2 or any(len(w) < 2 for w in words[:2]):
            raise ValueError("Give the person's first and last name (e.g. 'Sam Davies').")
        norm = " ".join(w.lower() for w in words)
        self._check_lookup_limit(norm)
        people = [self._person_summary(u) for u in self._mycbs_search(" ".join(words))]
        exact = [p for p in people if " ".join(re.findall(r"[^\W\d_][\w'\-.]*", p["name"].lower())) == norm]
        if class_year:
            exact = [p for p in exact if p["class"] and class_year.lower() in p["class"].lower()]
        if len(exact) == 1 and exact[0]["email"]:
            return {"found": True, "name": exact[0]["name"], "class": exact[0]["class"], "email": exact[0]["email"]}
        if len(exact) == 1:
            return {"found": False, "message": f"{exact[0]['name']} is in the directory but hasn't shared an email."}
        if len(exact) > 1:
            return {"found": False, "message": "Several people have that name. Ask the user which one, "
                    "then call again with class_year.",
                    "candidates": [{"name": p["name"], "class": p["class"]} for p in exact[:5]]}
        close = [p["name"] for p in people][:5]
        return {"found": False, "message": f"No exact match for '{name}' in the myCBS directory.",
                "did_you_mean": close}

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
            today = datetime.now(TZ).date()
            end = today + timedelta(days=14)
            self.all_events(today, end, max_pages=5)        # fills the cache below
            raw_rows = self._cache.get(f"all:{today.isoformat()}:{end.isoformat()}", (0, []))[1]
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
                   registered_only: bool = False, max_pages: int = 15) -> dict:
        today = datetime.now(TZ).date()
        d0 = _parse_date(start, today)
        d1 = _parse_date(end, d0 + timedelta(days=7))
        self._require_login()

        def load():
            # `range` and `limit` count the feed's internal rows (including ones it then
            # hides), not events: page with range += limit. A big limit covers weeks per
            # request; filter8/filter9 are the page's "From Date"/"To Date" boxes.
            seen, out, rng, step = set(), [], 0, 1000
            for _ in range(max_pages):
                rows = self._get("/mobile_ws/v17/mobile_events_list", range=rng, limit=step,
                                 filter4_contains="OR", filter4_notcontains="OR",
                                 order="undefined", search_word="",
                                 filter8=d0.isoformat(), filter9=d1.isoformat())
                if not rows:
                    break
                rng += step
                for e in (_map_row(r) for r in rows if isinstance(r, dict)):
                    key = e.get("eventId")
                    if not e.get("eventDates") or key in seen:
                        continue
                    seen.add(key)
                    out.append(e)
            return out

        raw = self._cached(f"all:{d0.isoformat()}:{d1.isoformat()}", load)
        events = [self._clean_event(e) for e in raw]
        events = [e for e in events if e["start"] and d0 <= date.fromisoformat(e["start"][:10]) <= d1]
        if club:
            try:
                name = (self.resolve_club(club)["name"] or "").lower()
            except KeyError:
                name = club.lower()
            events = [e for e in events if name and (name in e["club"].lower() or club.lower() in e["club"].lower())]
        if q:
            # Comma-separated words match any of them: "trip, trek, ski".
            terms = [t.strip().lower() for t in q.split(",") if t.strip()]
            events = [e for e in events if any(t in e["title"].lower() or t in e["club"].lower()
                                               for t in terms)]
        if tag:
            tl = tag.lower()
            events = [e for e in events if any(tl in t.lower() for t in e["tags"])]
        if registered_only:
            events = [e for e in events if e["registered"]]
        return {"start": d0.isoformat(), "end": d1.isoformat(), "count": len(events), "events": events}

    # -- free food ----------------------------------------------------------

    # CampusGroups event pages carry an organiser-set "Food Provided" flag:
    #   <!-- Food Provided --> <div ...><div ...><span class="... mdi-food"> ... Food Provided
    # (the comment is always there; the div only when the box was ticked).
    FOOD_FLAG_RE = re.compile(r"<!--\s*Food Provided\s*-->\s*<div[^>]*>\s*<div[^>]*>\s*<span[^>]*mdi-food", re.I)
    FOOD_WORDS = re.compile(r"\b(free food|food|lunch|dinner|breakfast|brunch|pizza|bagels?|snacks?|"
                            r"refreshments?|catered|catering|reception|happy hour|drinks|"
                            r"hors d'?oeuvres|tacos?|dumplings?|ice cream|wine)\b", re.I)
    ONLINE = re.compile(r"\b(virtual|zoom|online|webinar|teams)\b", re.I)

    def _food_provided(self, event_id: str) -> bool | None:
        """True/False from the event page; None if the page couldn't be read. Some pages
        (private or members-only events) redirect, which isn't a logged-out session, so it
        mustn't stop the whole search."""
        key = f"food:{event_id}"
        hit = self._cache.get(key)
        if hit and time.time() - hit[0] < self.cache_seconds:
            return hit[1]
        page = self._event_page(event_id)
        if page is None:
            return None
        val = bool(self.FOOD_FLAG_RE.search(page))
        self._cache[key] = (time.time(), val)
        return val

    def _event_page(self, event_id: str) -> str | None:
        """The event's page. /rsvp_boot?id=N always redirects to the host club's copy
        (/{club}/rsvp_boot?id=N), so follow it, but only within CampusGroups and never to a
        login page. Alumni events can redirect off-site; those are skipped."""
        try:
            r = self._session().get(f"{BASE}/rsvp_boot", params={"id": event_id}, timeout=15)
        except requests.RequestException:
            return None
        if r.status_code != 200 or not r.url.startswith(BASE) or "/login" in r.url:
            return None
        return r.text

    def free_food(self, start: str | date | None = None, end: str | date | None = None,
                  include_paid: bool = False) -> dict:
        """In-person CampusGroups events with food: the organiser ticked "Food Provided", or
        the title mentions food. Default: the rest of today and tomorrow (New York time)."""
        from concurrent.futures import ThreadPoolExecutor
        now = datetime.now(TZ)
        d0 = _parse_date(start, now.date())
        d1 = _parse_date(end, d0 + timedelta(days=1))
        if d1 < d0:
            raise ValueError("The end date is before the start date.")
        if (d1 - d0).days > 6:
            raise ValueError("Pick a week or less; each event page has to be checked.")
        cutoff = now - timedelta(minutes=90)          # skip things long under way

        def live(start_iso: str | None) -> bool:
            if not start_iso:
                return False
            s = datetime.fromisoformat(start_iso)
            s = s if s.tzinfo else s.replace(tzinfo=TZ)
            return s >= cutoff

        out = []
        cg = [e for e in self.all_events(d0, d1)["events"]
              if live(e["start"]) and not self.ONLINE.search(e["location"] or "")]
        if not include_paid:
            cg = [e for e in cg if not e["price"] or re.search(r"free|\$0(\.00)?\b", e["price"], re.I)]
        # Each event page is a separate request, so keep within a time budget and say so
        # if some weren't checked (Claude's tool calls time out after about a minute).
        deadline = time.time() + 35
        def check(e):
            return self._food_provided(str(e["id"])) if time.time() < deadline else None
        with ThreadPoolExecutor(max_workers=4) as pool:   # gentle on CampusGroups
            flags = list(pool.map(check, cg))
        unchecked = sum(1 for f in flags if f is None)
        for e, fed in zip(cg, flags):
            word = self.FOOD_WORDS.search(e["title"])
            if word and re.search(r"\bcatering\b", e["title"], re.I):
                word = None        # e.g. "Executive Education: Catering" is a booking, not an event
            if not (fed or word):
                continue
            out.append({"title": e["title"], "host": e["club"], "start": e["start"][:16].replace("T", " "),
                        "when": e["when"], "location": e["location"],
                        "food": "Food Provided (set by organiser)" if fed else f"title mentions '{word.group(0)}'",
                        "confidence": "high" if fed else "low",
                        "price": e["price"], "registration": e["registration"],
                        "registered": e["registered"], "source": "CampusGroups", "url": e["url"]})

        out.sort(key=lambda x: (x["start"], x["confidence"] != "high"))
        res_note = ({"note": f"{unchecked} of {len(cg)} event pages couldn't be checked (private, or "
                             "out of time); try a shorter date range for those days."} if unchecked else {})
        return {"start": d0.isoformat(), "end": d1.isoformat(), "count": len(out), "events": out,
                **res_note, "caveat": "Food Provided is ticked by organisers, so some events with food won't show it. "
                          "Many events need registration; don't just turn up for the food."}

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

    # -- 12twenty: CMC appointments (fellows, advisers, coaches) ---------------

    APPT_DURATIONS = (15, 30, 45, 60)

    def _tw(self, method: str, path: str, body: object = None, referer: str = "/appointments") -> object:
        """JSON call to 12twenty with your session. Raises SessionExpired if signed out."""
        r = self._session().request(method, f"{TWELVE}{path}", json=body, timeout=30,
                                    allow_redirects=False,
                                    headers={"Accept": "application/json",
                                             "Referer": f"{TWELVE}{referer}"})
        if r.status_code in (301, 302, 401, 403):
            raise SessionExpired("12twenty session expired. Run the login again.")
        if r.status_code >= 400:
            raise RuntimeError(f"12twenty said {r.status_code}: {_strip_html(r.text)[:200]}")
        if not r.text.strip():
            return None
        if not r.text.strip().startswith(("[", "{")):
            raise SessionExpired("12twenty session expired. Run the login again.")
        return r.json()

    def _tw_student_id(self) -> int:
        def load():
            me = self._tw("GET", "/api/v2/account/current-user") or {}
            if not me:       # 12twenty answers "204 No Content" when you're not signed in
                raise SessionExpired("Not signed in to 12twenty. Run the login again.")
            # Confirmed live: for students, current-user's Id is the student id.
            for k in ("StudentId", "Id", "EntityId", "UserId"):
                v = me.get(k) if isinstance(me, dict) else None
                if isinstance(v, int) and v > 10**9:
                    return v
            for v in (me.values() if isinstance(me, dict) else []):
                if isinstance(v, dict) and isinstance(v.get("StudentId"), int):
                    return v["StudentId"]
            raise RuntimeError("Couldn't work out your 12twenty student ID.")
        return self._cached("tw:me", load)

    @staticmethod
    def _utc_to_local(s: str | None) -> datetime | None:
        if not s:
            return None
        d = datetime.fromisoformat(s.replace("Z", "").split(".")[0])
        return d.replace(tzinfo=ZoneInfo("UTC")).astimezone(TZ)

    @staticmethod
    def _local_to_utc_str(d: datetime) -> str:
        return d.astimezone(ZoneInfo("UTC")).strftime("%Y-%m-%dT%H:%M:%S")

    def _tw_calendar(self, d0: date, d1: date) -> list[dict]:
        """Raw 12twenty calendar items between two dates (your appointments = TypeId 1,
        open availability blocks = TypeId 2)."""
        start = datetime.combine(d0, datetime.min.time(), TZ)
        end = datetime.combine(d1 + timedelta(days=1), datetime.min.time(), TZ)
        body = {"ShouldCalculateTotal": True, "PageNumber": 1, "PageSize": 2147483647, "IsSortAsc": True,
                "Filters": [], "StartTime": self._local_to_utc_str(start) + ".000Z",
                "EndTime": self._local_to_utc_str(end) + ".000Z",
                "ShowAppointments": True, "ShowAvailableBlocks": True, "ShowPersonalEvents": False,
                "StudentId": self._tw_student_id()}
        data = self._tw("POST", "/api/v2/appointment-calendar-events/post-query", body) or {}
        return data.get("Items", []) if isinstance(data, dict) else []

    def appointment_slots(self, start: str | date | None = None, end: str | date | None = None,
                          adviser: str | None = None, appointment_type: str | None = None) -> dict:
        """Open availability with CMC fellows/advisers/coaches. `adviser` matches part of a
        name; `appointment_type` e.g. 'Fellow'."""
        today = datetime.now(TZ).date()
        d0 = _parse_date(start, today)
        d1 = _parse_date(end, d0 + timedelta(days=6))
        if (d1 - d0).days > 31:
            raise ValueError("Pick a range of a month or less.")
        out = []
        for it in self._tw_calendar(d0, d1):
            if it.get("TypeId") != 2:
                continue
            name = it.get("AdviserName") or ""
            if adviser and _norm_name(adviser) not in _norm_name(name):
                continue
            if appointment_type and appointment_type.lower() not in (it.get("PreferredAppointmentTypeName") or "").lower() \
                    and it.get("PreferredAppointmentTypeName"):
                continue
            s, e = self._utc_to_local(it["StartTime"]), self._utc_to_local(it["EndTime"])
            earliest = self._utc_to_local(it.get("MinAvailableBookingTime"))
            if earliest and e <= earliest:
                continue
            if it.get("HasMaxAppointmentsReached"):
                continue
            out.append({"block_id": it["EntityId"], "adviser": name, "date": s.date().isoformat(),
                        "from": f"{max(s, earliest) if earliest else s:%H:%M}", "until": f"{e:%H:%M}",
                        "bookable_from": f"{earliest:%a %b} {earliest.day} {earliest:%H:%M}" if earliest else None})
        out.sort(key=lambda x: (x["date"], x["from"], x["adviser"]))
        return {"start": d0.isoformat(), "end": d1.isoformat(), "count": len(out), "slots": out}

    def my_appointments(self, days: int = 60) -> list[dict]:
        today = datetime.now(TZ).date()
        out = []
        for it in self._tw_calendar(today, today + timedelta(days=days)):
            if it.get("TypeId") != 1:
                continue
            s, e = self._utc_to_local(it["StartTime"]), self._utc_to_local(it["EndTime"])
            out.append({"id": str(it["EntityId"]), "with": it.get("AdviserName"),
                        "date": s.date().isoformat(), "start": f"{s:%H:%M}", "end": f"{e:%H:%M}",
                        "type": it.get("PreferredAppointmentTypeName"),
                        "virtual": bool(it.get("Location") and "virtual-meetings" in str(it.get("Location")))})
        return sorted(out, key=lambda x: (x["date"], x["start"]))

    def book_appointment(self, adviser: str, day: str | date, start: str, topic: str,
                         minutes: int = 30, appointment_type: str | None = None) -> dict:
        """Book a CMC appointment in one of the adviser's open blocks. Times are New York time."""
        topic = (topic or "").strip()
        if not topic:
            raise ValueError("A topic is required (what you'd like to discuss).")
        if minutes not in self.APPT_DURATIONS:
            raise ValueError("Length must be 15, 30, 45 or 60 minutes.")
        d = _parse_date(day, datetime.now(TZ).date())
        try:
            t0 = datetime.strptime(start, "%H:%M")
        except ValueError:
            raise ValueError("Start time must be HH:MM in 24-hour time, e.g. 14:00.")
        if t0.minute % 5:
            raise ValueError("Start time must be on a 5-minute mark.")
        starts = datetime.combine(d, t0.time(), TZ)
        ends = starts + timedelta(minutes=minutes)

        items = self._tw_calendar(d, d)
        mine = [i for i in items if i.get("TypeId") == 1]
        for m in mine:
            ms, me = self._utc_to_local(m["StartTime"]), self._utc_to_local(m["EndTime"])
            if ms < ends and me > starts:
                raise ValueError(f"You already have an appointment then ({ms:%H:%M}-{me:%H:%M} with {m.get('AdviserName')}).")
        blocks = [i for i in items if i.get("TypeId") == 2 and _norm_name(adviser) in _norm_name(i.get("AdviserName") or "")]
        names = sorted({b.get("AdviserName") for b in blocks})
        if len(names) > 1:
            raise ValueError(f"'{adviser}' matches several people ({', '.join(names)}). Say which one.")
        fit = [b for b in blocks if self._utc_to_local(b["StartTime"]) <= starts and self._utc_to_local(b["EndTime"]) >= ends]
        if not fit:
            if not blocks:
                raise ValueError(f"{adviser} has no open time on {d:%a %b} {d.day}.")
            spans = ", ".join(f"{self._utc_to_local(b['StartTime']):%H:%M}-{self._utc_to_local(b['EndTime']):%H:%M}" for b in blocks)
            raise ValueError(f"{names[0]} isn't free {starts:%H:%M}-{ends:%H:%M} that day. Open: {spans}.")
        b = fit[0]
        earliest = self._utc_to_local(b.get("MinAvailableBookingTime"))
        if earliest and starts < earliest:
            raise ValueError(f"That's too soon; the earliest bookable time is {earliest:%a %b} {earliest.day} {earliest:%H:%M}.")

        block = self._tw("GET", f"/api/v2/appointment-blocks/{b['EntityId']}") or {}
        type_ids = block.get("AvailableAppointmentTypeIds") or []
        type_names = [t.strip() for t in (block.get("AvailableAppointmentTypeNames") or "").split(",") if t.strip()]
        type_id = type_ids[0] if type_ids else None
        if appointment_type and len(type_ids) == len(type_names):
            for tid, tn in zip(type_ids, type_names):
                if appointment_type.lower() in tn.lower():
                    type_id = tid
        elif len(type_ids) > 1 and len(type_ids) == len(type_names):
            raise ValueError("This slot offers several appointment types (" + ", ".join(type_names) +
                             "). Ask the user which one, then pass appointment_type.")
        adviser_obj = block.get("Adviser") or {"Id": b["AdviserId"], "AdviserId": b["AdviserId"]}
        payload = {"Id": 0, "isNew": True, "ReasonForVisitIds": [], "CustomAttributeValues": {},
                   "StudentId": self._tw_student_id(),
                   "StartTime": self._local_to_utc_str(starts),
                   "EndTime": self._local_to_utc_str(ends) + ".000Z",
                   "TimeZoneId": block.get("TimeZoneId") or 2,
                   "AdviserId": b["AdviserId"], "Adviser": adviser_obj,
                   "AppointmentBlockId": b["EntityId"], "AppointmentBlock": block,
                   "PreferredAppointmentTypeId": type_id,
                   "LocationTypeId": 1, "VirtualLocationTypeId": 1, "Topic": topic}
        created = self._tw("POST", "/api/v2/appointments", payload)
        # Confirm it's on your calendar.
        for m in self.my_appointments(days=max(1, (d - datetime.now(TZ).date()).days + 1)):
            if m["date"] == d.isoformat() and m["start"] == f"{starts:%H:%M}" and \
                    _norm_name(m["with"] or "") == _norm_name(b.get("AdviserName") or ""):
                return {"booked": True, "appointment": m, "topic": topic}
        cid = created.get("Id") if isinstance(created, dict) else None
        raise RuntimeError("12twenty didn't confirm the booking (it's not on your calendar)"
                           + (f"; it returned appointment {cid}" if cid else "") + ".")

    def cancel_appointment(self, appointment_id: str | int) -> dict:
        aid = str(appointment_id).strip()
        mine = {m["id"]: m for m in self.my_appointments()}
        if aid not in mine:
            raise KeyError(f"No upcoming appointment of yours has id {aid}.")
        self._tw("PUT", f"/api/v2/appointments/{aid}/cancel", {})
        rec = self._tw("GET", f"/api/v2/appointments/{aid}") or {}
        if str(rec.get("StatusName", "")).lower() not in ("canceled", "cancelled"):
            raise RuntimeError("12twenty didn't confirm the cancellation.")
        return {"cancelled": True, "appointment": mine[aid]}

    # -- 12twenty: career events (info sessions, coffee chats, workshops) ------

    EVENTS_URL = f"{TWELVE}/events#/events/"

    def _tw_upcoming_events(self) -> list[dict]:
        """Every upcoming 12twenty event you can see (raw). Cached for the session."""
        def load():
            out, page = [], 1
            while True:
                body = {"ShouldCalculateTotal": True, "PageNumber": page, "PageSize": 500,
                        "IsSortAsc": True, "sortBy": 1, "StudentGroupIds": [],
                        # 15 = "all future" in 12twenty's date filter
                        "Filters": [{"AttributeName": "event__end_date", "SelectedId": 15, "TypeId": 1,
                                     "HasValue": True, "IsExcluded": False, "Id": 0}]}
                data = self._tw("POST", "/api/v2/Events/post-query", body, referer="/events") or {}
                items = data.get("Items", []) if isinstance(data, dict) else []
                out += items
                if len(items) < 500 or page >= 10:
                    return out
                page += 1
        return self._cached("tw:events", load)

    def _tw_my_registrations(self) -> dict:
        """{event_id: registration} for your 12twenty event sign-ups (incl. waitlists)."""
        data = self._tw("GET", f"/api/v2/event-student-registrations?studentId={self._tw_student_id()}"
                               "&includeTimeSlotInfo=true&PageSize=1000", referer="/events") or {}
        items = data.get("Items", []) if isinstance(data, dict) else []
        return {str(r["EventId"]): r for r in items if r.get("EventId")}

    def _event_summary(self, e: dict, reg: dict | None = None) -> dict:
        s, en = self._utc_to_local(e.get("StartDate")), self._utc_to_local(e.get("EndDate"))
        opens = self._utc_to_local(e.get("RegistrationBeginDate"))
        closes = self._utc_to_local(e.get("RegistrationEndDate"))
        # Your registrations list is the source of truth (the search results' IsAttending
        # flag isn't always filled in).
        registered = bool(reg)
        waitlisted = bool(reg and reg.get("IsOnWaitlist"))
        out = {"id": str(e["Id"]), "name": (e.get("Name") or "").strip(),
               "company": e.get("CompanyName"), "type": e.get("EventTypeName"),
               "date": s.date().isoformat() if s else None,
               "start": f"{s:%H:%M}" if s else None, "end": f"{en:%H:%M}" if en else None,
               "location": e.get("Location"), "status": e.get("StatusName"),
               "registered": registered and not waitlisted, "waitlisted": waitlisted,
               "link": f"{self.EVENTS_URL}{e['Id']}"}
        if s and en and en.date() != s.date():
            out["end_date"] = en.date().isoformat()
        if opens and opens > datetime.now(TZ):
            out["registration_opens"] = f"{opens:%a %b} {opens.day} {opens:%H:%M}"
        if closes:
            out["registration_closes"] = f"{closes:%a %b} {closes.day} {closes:%H:%M}"
        if e.get("HasWaitlist") and e.get("NumWaitlisted"):
            out["num_waitlisted"] = e["NumWaitlisted"]
        return out

    def career_events(self, start: str | date | None = None, end: str | date | None = None,
                      company: str | None = None, keyword: str | None = None,
                      event_type: str | None = None, registered_only: bool = False) -> dict:
        """Upcoming 12twenty events (employer info sessions, coffee chats, CMC workshops).
        Dates are New York time; default range is the next 14 days."""
        today = datetime.now(TZ).date()
        d0 = _parse_date(start, today)
        d1 = _parse_date(end, d0 + timedelta(days=13))
        if d1 < d0:
            raise ValueError("The end date is before the start date.")
        regs = self._tw_my_registrations()
        out = []
        for e in self._tw_upcoming_events():
            ev = self._event_summary(e, regs.get(str(e.get("Id"))))
            if not ev["date"] or not (d0.isoformat() <= ev["date"] <= d1.isoformat()):
                continue
            if registered_only and not (ev["registered"] or ev["waitlisted"]):
                continue
            if company and _norm_name(company) not in _norm_name(ev["company"] or "") + _norm_name(ev["name"]):
                continue
            if event_type and _norm_name(event_type).rstrip("s") not in _norm_name(ev["type"] or ""):
                continue
            if keyword:
                blob = _norm_name(" ".join([ev["name"], ev["company"] or "", ev["type"] or "",
                                            _strip_html(e.get("Description") or "")]))
                if not all(_norm_name(w) in blob for w in keyword.split() if _norm_name(w)):
                    continue
            out.append(ev)
        out.sort(key=lambda x: (x["date"], x["start"] or "", x["name"]))
        return {"start": d0.isoformat(), "end": d1.isoformat(), "count": len(out), "events": out}

    def my_event_registrations(self) -> list[dict]:
        """Upcoming 12twenty events you're signed up (or waitlisted) for."""
        regs = self._tw_my_registrations()
        events = {str(e["Id"]): e for e in self._tw_upcoming_events()}
        out = []
        for eid, reg in regs.items():
            if eid in events:
                ev = self._event_summary(events[eid], reg)
                ev["registration_id"] = str(reg["Id"])
                if reg.get("TimeSlotStartTime"):
                    ts = self._utc_to_local(reg["TimeSlotStartTime"])
                    ev["your_time_slot"] = f"{ts:%H:%M}" if ts else None
                out.append(ev)
        return sorted(out, key=lambda x: (x["date"] or "", x["start"] or ""))

    def _find_event(self, event_id: str | int) -> dict:
        eid = str(event_id).strip()
        if not eid.isdigit():
            raise ValueError("Pass the event's id (from the events list), not its name.")
        e = self._tw("GET", f"/api/v2/Events/{eid}", referer="/events")
        if not isinstance(e, dict) or not e.get("Id"):
            raise KeyError(f"No 12twenty event has id {eid}.")
        return e

    def event_details(self, event_id: str | int) -> dict:
        """Full details for one event, plus whether you can sign up through Claude."""
        e = self._find_event(event_id)
        reg = self._tw_my_registrations().get(str(e["Id"]))
        out = self._event_summary(e, reg)
        rules = e.get("RegistrationRules") or {}
        desc = _strip_html(e.get("Description") or "")
        out.update({
            "description": desc[:1500] + ("…" if len(desc) > 1500 else ""),
            "format": e.get("EventFormatName"),
            "industry": e.get("ConsolidatedIndustryName"),
            "audience": e.get("TargetAudience"),
            "dress": e.get("DressAttireName"),
            "work_authorization": e.get("WorkAuthRequirementName"),
            "other_info": _strip_html(e.get("OtherInformation") or "") or None,
            "registration_instructions": _strip_html(e.get("RegistrationInstructions") or "") or None,
            "seats": e.get("TotalSeats"), "attendees": e.get("NumAttendees"),
            "can_register": bool(rules.get("CanRegister")),
            "can_join_waitlist": bool(rules.get("CanRegisterForWaitlist")),
            "cannot_register_reason": rules.get("CannotRegisterReason"),
        })
        blockers = self._event_blockers(e)
        if blockers:
            out["sign_up_on_website"] = blockers
        if reg:
            out["registration_id"] = str(reg["Id"])
        return {k: v for k, v in out.items() if v not in (None, "", [])}

    @staticmethod
    def _event_blockers(e: dict) -> list[str]:
        """Things Claude can't fill in for you; these events need the website."""
        why = []
        if e.get("IsTimeSlotEvent"):
            why.append("you pick a time slot")
        if e.get("PreSurveyMetadataJson"):
            why.append("it has registration questions")
        if any(d.get("IsRequired") for d in (e.get("RegistrationDocumentTypes") or [])):
            names = ", ".join(d.get("Name") or "document" for d in e["RegistrationDocumentTypes"] if d.get("IsRequired"))
            why.append(f"it needs a {names} upload")
        if e.get("PaymentConfigId") or e.get("AddOns"):
            why.append("it has a fee or add-ons")
        if e.get("RegistrationTypeList"):
            why.append("you choose a registration type")
        return why

    def register_event(self, event_id: str | int, join_waitlist: bool = False) -> dict:
        """Sign up for a 12twenty event. If it's full and has a waitlist, join_waitlist=True
        puts you on the waitlist instead."""
        e = self._find_event(event_id)
        eid = str(e["Id"])
        link = f"{self.EVENTS_URL}{eid}"
        regs = self._tw_my_registrations()
        if eid in regs:
            what = "on the waitlist for" if regs[eid].get("IsOnWaitlist") else "already registered for"
            raise ValueError(f"You're {what} '{e.get('Name')}'.")
        blockers = self._event_blockers(e)
        if blockers:
            raise ValueError(f"This one has to be done on 12twenty because {' and '.join(blockers)}: {link}")
        rules = e.get("RegistrationRules") or {}
        if not rules.get("CanRegister"):
            if rules.get("CanRegisterForWaitlist"):
                if not join_waitlist:
                    raise ValueError("The event is full, but there's a waitlist. Ask the user whether to "
                                     "join it, then call again with join_waitlist=true.")
            else:
                raise ValueError(rules.get("CannotRegisterReason") or "12twenty isn't taking sign-ups for this event right now.")
        payload = {"EventId": e["Id"], "StudentId": self._tw_student_id(), "TimeSlotId": None,
                   "registrationDocuments": [], "PreSurveyResponseJson": "{}"}
        self._tw("POST", "/api/v2/event-student-registrations", payload, referer=f"/events")
        self._cache.pop("tw:events", None)
        reg = self._tw_my_registrations().get(eid)
        if not reg:
            raise RuntimeError(f"12twenty didn't confirm the sign-up. Check the event page: {link}")
        ev = self._event_summary(e, reg)
        ev["registration_id"] = str(reg["Id"])
        res = {"registered": not reg.get("IsOnWaitlist"), "waitlisted": bool(reg.get("IsOnWaitlist")), "event": ev}
        if reg.get("WaitlistPosition"):
            res["waitlist_position"] = reg["WaitlistPosition"]
        instr = _strip_html(e.get("RegistrationInstructions") or "")
        if instr:
            res["also_note"] = instr      # e.g. "also register on the company's Zoom link"
        return res

    def cancel_event_registration(self, event_id: str | int) -> dict:
        """Cancel your sign-up for an event (or leave its waitlist)."""
        eid = str(event_id).strip()
        regs = self._tw_my_registrations()
        if eid not in regs:
            raise KeyError(f"You're not signed up for event {eid}.")
        reg = regs[eid]
        events = {str(e["Id"]): e for e in self._tw_upcoming_events()}
        summary = self._event_summary(events[eid], reg) if eid in events else {"id": eid}
        self._tw("DELETE", f"/api/v2/event-student-registrations/{reg['Id']}", referer=f"/events")
        self._cache.pop("tw:events", None)
        if eid in self._tw_my_registrations():
            raise RuntimeError("12twenty didn't confirm the cancellation; you're still signed up.")
        return {"cancelled": True, "was_waitlisted": bool(reg.get("IsOnWaitlist")), "event": summary}


def _norm_name(s: str) -> str:
    return re.sub(r"[^a-z]", "", (s or "").lower())
