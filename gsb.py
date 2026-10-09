"""
Command line for the GSB CampusGroups API.

  python gsb.py login        # one-time: sign in with UNI + Duo in a browser window
  python gsb.py logout       # delete the saved session
  python gsb.py serve        # start the API at http://127.0.0.1:8765 (docs at /docs)

Quick answers without the server:
  python gsb.py clubs
  python gsb.py events "ai club" [START] [END]
  python gsb.py all-events [START] [END]
  python gsb.py rooms 2026-09-29 14:00 16:00 Geffen
  python gsb.py reservations
"""

import json
import sys

from gsb_client import GSBClient, SessionExpired, login, logout


def main(argv: list[str]) -> None:
    if not argv:
        print(__doc__)
        return
    cmd, args = argv[0], argv[1:]
    if cmd == "login":
        return login()
    if cmd == "logout":
        return logout()
    if cmd == "serve":
        import uvicorn
        uvicorn.run("api:app", host="127.0.0.1", port=8765)
        return

    gsb = GSBClient()
    arg = lambda i: args[i] if len(args) > i else None
    try:
        if cmd == "clubs":
            out = gsb.my_clubs()
        elif cmd == "events":
            out = gsb.club_events(args[0], arg(1), arg(2))
        elif cmd == "all-events":
            out = gsb.all_events(arg(0), arg(1))
        elif cmd == "rooms":
            out = gsb.free_rooms(arg(0), arg(1), arg(2), arg(3))
        elif cmd == "reservations":
            out = gsb.my_reservations()
        else:
            print(__doc__)
            return
    except SessionExpired as e:
        sys.exit(str(e))
    print(json.dumps(out, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main(sys.argv[1:])
