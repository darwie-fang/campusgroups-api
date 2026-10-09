"""
Adds this project to the Claude desktop app as a tool called "campusgroups".

setup.sh runs this for you. To run it by hand, from the project folder:
    .venv/bin/python connect_claude.py          (Mac)
    .venv\\Scripts\\python connect_claude.py      (Windows)
Then fully quit the Claude app and reopen it.

It only adds/updates the "campusgroups" entry in Claude's config and keeps a
backup of the previous file. Run with --remove to take it out again.
"""

import json
import os
import shutil
import sys
from pathlib import Path

NAME = "campusgroups"
here = Path(__file__).resolve().parent
quiet = "--quiet" in sys.argv


def config_path() -> Path:
    if sys.platform == "win32":
        return Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming")) / "Claude" / "claude_desktop_config.json"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"
    sys.exit("The Claude desktop app runs on Mac and Windows only.")


CONFIG = config_path()
config = {}
if CONFIG.exists():
    text = CONFIG.read_text().strip()
    if text:
        try:
            config = json.loads(text)
        except ValueError:
            sys.exit(f"{CONFIG} isn't valid JSON, so I won't touch it. Fix or move it, then rerun.")
    shutil.copy(CONFIG, CONFIG.with_name(CONFIG.name + ".bak"))
else:
    CONFIG.parent.mkdir(parents=True, exist_ok=True)

servers = config.setdefault("mcpServers", {})
if "--remove" in sys.argv:
    servers.pop(NAME, None)
    print(f"Removed '{NAME}' from {CONFIG}")
else:
    if ".venv" not in sys.executable:
        print("Note: this isn't the project's .venv Python, so Claude may not find the packages. "
              "Run it with the .venv Python instead (see the top of this file).")
    servers[NAME] = {"command": sys.executable, "args": [str(here / "mcp_server.py")]}
    if not quiet:
        print(f"Added '{NAME}' to {CONFIG}")
        print("Now fully quit the Claude app and reopen it.")

CONFIG.write_text(json.dumps(config, indent=2))
