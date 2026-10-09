# GSB CampusGroups API

Ask Claude about Columbia Business School clubs, events and study rooms in plain English:

> *"What's the AI Club running next week?"*
> *"Any venture capital events on Thursday?"*
> *"Find me a free study room in Geffen tomorrow from 2 to 4."*

It reads **your own** CampusGroups account (groups.gsb.columbia.edu) and runs only on your laptop.
It's read-only: it never registers, books or changes anything.

---

## Getting started (Mac, about 5 minutes)

**You need:** a Mac, the [Claude desktop app](https://claude.ai/download), and your UNI + Duo.
Python comes with your Mac; if it's missing, the setup will tell you how to get it.

1. Open **Terminal** (press `Cmd + Space`, type `Terminal`, press Enter).
2. Copy this line, paste it into Terminal, and press **Enter**:

   ```
   curl -fsSL https://raw.githubusercontent.com/darwie-fang/courseworks-api/main/install.sh | bash
   ```

3. When a browser window opens, sign in with your **UNI and password** and approve **Duo**.
   The window closes by itself.
4. When it asks to restart Claude, press **Enter**.
5. Ask Claude: *"What's the AI Club running next week?"*

That's it. The project lives in `~/Projects/gsb-api`.

### When your login expires

Every week or two CampusGroups signs you out. Claude will tell you, and give you this line to paste
into Terminal (then sign in with Duo again):

```
bash ~/Projects/gsb-api/setup.sh
```

### Get the latest version

Paste the install line from step 2 again. It updates the code and keeps your login.

---

## Troubleshooting

| You see | What to do |
|---|---|
| A pop-up asking to install **command line developer tools** | Click **Install**, wait for it to finish, then paste the install line again. |
| Claude says it has no CampusGroups tool | Fully quit Claude (`Cmd + Q`, not just closing the window) and reopen it. Still missing? Start a **new chat**. |
| "Not logged in" | Run `bash ~/Projects/gsb-api/setup.sh` and sign in. |
| You pasted a command and nothing happened | That Terminal window is probably busy running something. Open a new window (`Cmd + N`) and paste it there. |
| Anything else | Run `bash ~/Projects/gsb-api/setup.sh` again. It's safe to repeat and explains what's wrong. |

**Important:** don't keep the project in Downloads, Desktop or Documents. macOS blocks the Claude app
from running tools in those folders. (The installer puts it in `~/Projects` for you.)

---

## What it can answer

| Ask Claude about | Behind the scenes |
|---|---|
| Your clubs | `list_my_clubs` |
| One club's events ("ai club", "ABA", "tech club") | `get_club_events` |
| Events across all clubs, by date, keyword, topic or "ones I registered for" | `get_all_events` |
| Free rooms by date, time, building and size | `find_free_rooms` |
| Your upcoming room reservations | `my_room_reservations` |

Room availability covers about the current week, the same as the CampusGroups site.

---

## Privacy

- Your **password is never seen or stored**. Signing in happens in a normal browser window.
- Only the resulting session cookies are saved, in `~/.gsb-api/session.json`, readable only by you.
  `python3 gsb.py logout` (inside the project folder) deletes them.
- Nothing leaves your laptop except requests to CampusGroups itself.
- CampusGroups' room data includes other students' names. This tool drops them and only shows free/busy times.
- **Never share** your `~/.gsb-api` folder. It's your login. Sharing the project folder or the GitHub link is fine.

---

## Manual setup (Windows, or if you prefer doing it yourself)

From inside the project folder (not in Downloads, Desktop or Documents):

**Mac**
```
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m playwright install chromium
.venv/bin/python gsb.py login
.venv/bin/python connect_claude.py
```

**Windows** (PowerShell)
```
py -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python -m playwright install chromium
.venv\Scripts\python gsb.py login
.venv\Scripts\python connect_claude.py
```

Then fully quit and reopen the Claude app.

---

## For developers

The same features are available as a local web API:

```
.venv/bin/python gsb.py serve        # http://127.0.0.1:8765/docs
```

| Endpoint | |
|---|---|
| `GET /status` | Is the saved session valid? |
| `GET /clubs` | Your clubs |
| `GET /clubs/{club}/events?start=&end=` | One club's events |
| `GET /events?start=&end=&club=&q=&tag=&registered_only=` | All events, filterable |
| `GET /rooms/free?date=&start=&end=&building=&min_capacity=&type=` | Free rooms |
| `GET /me/reservations` | Your upcoming reservations |

Or skip the server: `gsb.py clubs`, `gsb.py events "ai club"`, `gsb.py all-events`,
`gsb.py rooms 2026-10-09 14:00 16:00 Geffen`, `gsb.py reservations`, `gsb.py status`.

**Files:** `gsb_client.py` (login, requests, parsing), `api.py` (web API), `mcp_server.py` (Claude tools),
`gsb.py` (command line), `connect_claude.py` (adds the tool to Claude), `setup.sh` / `install.sh` (setup).

**How it works:** it calls the same internal JSON endpoints the CampusGroups website uses, with your
logged-in session. These are undocumented, so a CampusGroups update can break things until the parsing
in `gsb_client.py` is fixed. The all-events feed returns ~20 events per request, so a full week takes a
few seconds the first time; results are cached for 30 minutes.

*Unofficial personal project. Not affiliated with Columbia University or CampusGroups.*
