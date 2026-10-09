#!/bin/bash
# One-command setup for the GSB CampusGroups API (macOS).
#
#   bash setup.sh
#
# Safe to run again any time: it skips what's already done, rebuilds what's
# broken, and only asks you to sign in when your login has expired.

PROJECT_NAME="gsb-api"
TARGET="$HOME/Projects/$PROJECT_NAME"

bold()  { printf "\033[1m%s\033[0m\n" "$*"; }
ok()    { printf "  \033[32m✓\033[0m %s\n" "$*"; }
info()  { printf "  %s\n" "$*"; }
fail()  { printf "\n\033[31m✗ %s\033[0m\n" "$1"; shift; for l in "$@"; do printf "  %s\n" "$l"; done; echo; exit 1; }
ask()   { local a; read -r -p "  $1 [Y/n] " a </dev/tty; [[ -z "$a" || "$a" =~ ^[Yy] ]]; }

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR" || fail "Couldn't open the project folder."

echo
bold "GSB CampusGroups API: setup"
echo

# --- 0. Right machine, right folder ---------------------------------------
[[ "$(uname)" == "Darwin" ]] || fail "This setup script is for Macs." \
  "On Windows, follow the 'Manual setup' steps in README.md."
[[ -f "$DIR/gsb_client.py" && -f "$DIR/requirements.txt" ]] || fail \
  "This doesn't look like the project folder (gsb_client.py is missing)." \
  "Run setup.sh from inside the downloaded project folder."

# macOS stops the Claude app from running things in these folders.
case "$DIR/" in
  "$HOME/Downloads/"*|"$HOME/Desktop/"*|"$HOME/Documents/"*|"$HOME/Library/Mobile Documents/"*)
    bold "1. Folder location"
    info "This folder is in a protected place ($(dirname "$DIR"))."
    info "The Claude app isn't allowed to run tools from there, so it needs to move."
    if [[ -e "$TARGET" ]]; then
      fail "Can't move it: $TARGET already exists." \
        "If that's an older copy, delete or rename it, then run this again."
    fi
    if ask "Move it to $TARGET?"; then
      mkdir -p "$HOME/Projects" && mv "$DIR" "$TARGET" || fail "Couldn't move the folder."
      DIR="$TARGET"; cd "$DIR" || fail "Couldn't open $DIR."
      ok "Moved to $DIR"
    else
      fail "Setup stopped. Move the folder somewhere like ~/Projects and run this again."
    fi
    ;;
  *)
    bold "1. Folder location"
    ok "$DIR"
    ;;
esac

# --- 2. Python ---------------------------------------------------------------
bold "2. Python"
if ! command -v python3 >/dev/null 2>&1 || ! python3 -c "" >/dev/null 2>&1; then
  fail "Python isn't installed yet." \
    "A pop-up should ask to install the 'command line developer tools'." \
    "Click Install, wait for it to finish (a few minutes), then run this again." \
    "No pop-up? Run:  xcode-select --install"
fi
PYVER="$(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' \
  || fail "Python $PYVER is too old. You need 3.9 or newer." \
       "Install it from https://www.python.org/downloads/ then run this again."
ok "Python $PYVER"

# --- 3. Packages ---------------------------------------------------------------
bold "3. Packages (first time: 1-3 minutes)"
VPY="$DIR/.venv/bin/python"
venv_ok() {
  [[ -x "$VPY" ]] || return 1
  # A venv only works in the folder it was created in.
  [[ "$("$VPY" -c 'import sys,os; print(os.path.realpath(sys.prefix))' 2>/dev/null)" \
     == "$(cd "$DIR/.venv" && pwd -P)" ]]
}
if ! venv_ok; then
  rm -rf "$DIR/.venv"
  python3 -m venv "$DIR/.venv" || fail "Couldn't create the package folder (.venv)."
  info "Created a private package folder (.venv)"
fi
"$VPY" -m pip install -q --disable-pip-version-check -r "$DIR/requirements.txt" \
  || fail "Installing packages failed." \
       "Check your internet connection, then run this again." \
       "Still failing? Send the error above to whoever shared this with you."
ok "Packages installed"

info "Downloading the login browser (Chromium)..."
"$VPY" -m playwright install chromium >/dev/null 2>&1 \
  || "$VPY" -m playwright install chromium \
  || fail "Couldn't download the login browser." "Check your internet connection, then run this again."
ok "Login browser ready"

# --- 4. CampusGroups login ------------------------------------------------------
bold "4. CampusGroups login"
if "$VPY" "$DIR/gsb.py" status >/dev/null 2>&1; then
  ok "Already logged in"
else
  info "A browser window will open. Sign in with your UNI and password,"
  info "approve the Duo push, and the window closes by itself."
  "$VPY" "$DIR/gsb.py" login || fail "Login didn't finish." \
    "Run this again and complete the sign-in within 5 minutes."
  "$VPY" "$DIR/gsb.py" status >/dev/null 2>&1 \
    || fail "Logged in, but CampusGroups didn't accept the session." "Run this again."
  ok "Logged in"
fi

# --- 5. Connect to the Claude app ------------------------------------------------
bold "5. Connect to the Claude app"
if [[ ! -d "/Applications/Claude.app" && ! -d "$HOME/Applications/Claude.app" ]]; then
  info "Heads up: the Claude desktop app isn't installed. Get it at https://claude.ai/download"
  info "(The browser version of Claude can't use local tools.)"
fi
CLAUDE_CFG="$HOME/Library/Application Support/Claude/claude_desktop_config.json"
BEFORE="$(cat "$CLAUDE_CFG" 2>/dev/null)"
"$VPY" "$DIR/connect_claude.py" --quiet || fail "Couldn't update the Claude app's settings."
if [[ "$(cat "$CLAUDE_CFG" 2>/dev/null)" == "$BEFORE" ]]; then
  ok "Already connected to Claude"; CHANGED=0
else
  ok "Added 'campusgroups' to Claude"; CHANGED=1
fi

echo
bold "Done!"
if [[ "$CHANGED" == 0 ]]; then
  info "Nothing to restart. If Claude was already open, you're good to go."
elif pgrep -xq "Claude"; then
  info "Claude needs a restart to load the new tool."
  if ask "Restart the Claude app now?"; then
    osascript -e 'quit app "Claude"' >/dev/null 2>&1
    for _ in $(seq 1 20); do pgrep -xq "Claude" || break; sleep 0.5; done
    open -a "Claude" && ok "Claude restarted"
  else
    info "Quit Claude with Cmd+Q and reopen it when you're ready."
  fi
else
  info "Open the Claude app."
fi
echo
info "Then try asking Claude:"
info "  \"What's the AI Club running next week?\""
info "  \"Find me a free study room in Geffen tomorrow from 2 to 4\""
echo
info "Login expired later? Just run this again:  bash $DIR/setup.sh"
echo
