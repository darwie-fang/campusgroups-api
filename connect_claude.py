"""
Adds this project to the Claude desktop app as a tool called "campusgroups".

Run it once from the gsb-api folder, with the .venv active:
    python3 connect_claude.py
Then fully quit the Claude app (Cmd+Q) and reopen it.

It only adds/updates the "campusgroups" entry in Claude's config and keeps a
backup of the previous file. Run with --remove to take it out again.
"""

import json
import os
import shutil
import sys
from pathlib import Path

CONFIG = Path.home() / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"
NAME = "campusgroups"
here = Path(__file__).resolve().parent

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
        print("Note: the .venv doesn't look active. Run `source .venv/bin/activate` first "
              "so Claude uses the right Python.")
    servers[NAME] = {"command": sys.executable, "args": [str(here / "mcp_server.py")]}
    print(f"Added '{NAME}' to {CONFIG}")
    print("Now fully quit the Claude app (Cmd+Q) and reopen it.")

CONFIG.write_text(json.dumps(config, indent=2))
