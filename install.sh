#!/bin/bash
# Downloads the GSB CampusGroups API into ~/Projects/gsb-api and runs setup.
# Paste into Terminal:
#
#   curl -fsSL https://raw.githubusercontent.com/darwie-fang/campusgroups-api/main/install.sh | bash
#
# Running it again updates the code to the latest version (your login and
# settings are kept).
#
# Testers (QA channel): add the channel name at the end:
#   curl -fsSL https://raw.githubusercontent.com/darwie-fang/campusgroups-api/main/install.sh | bash -s -- dev
# Back to the normal version: run the plain line above.

REPO="darwie-fang/campusgroups-api"
TARGET="$HOME/Projects/gsb-api"
CHANNEL="${1:-main}"

fail() { printf "\n\033[31m✗ %s\033[0m\n\n" "$*"; exit 1; }

[[ "$CHANNEL" =~ ^[A-Za-z0-9._-]+$ ]] || fail "Unknown channel '$CHANNEL'."
[[ "$(uname)" == "Darwin" ]] || fail "This installer is for Macs. On Windows, see the README: https://github.com/$REPO"

TMP="$(mktemp -d)" || fail "Couldn't create a temporary folder."
trap 'rm -rf "$TMP"' EXIT

if [[ "$CHANNEL" == "main" ]]; then
  echo "Downloading the latest version..."
else
  printf "Downloading the \033[1m%s\033[0m (testing) version...\n" "$CHANNEL"
fi
curl -fsSL "https://github.com/$REPO/archive/refs/heads/$CHANNEL.zip" -o "$TMP/code.zip" \
  || fail "Download failed. Check your internet connection (and the channel name '$CHANNEL') and try again."
unzip -q "$TMP/code.zip" -d "$TMP" || fail "Couldn't unpack the download."
SRC="$(find "$TMP" -mindepth 1 -maxdepth 1 -type d | head -n 1)"
[[ -f "$SRC/setup.sh" ]] || fail "The download looks incomplete. Try again in a minute."

if [[ -d "$TARGET" ]]; then
  [[ -f "$TARGET/gsb_client.py" ]] || fail "$TARGET exists but isn't this project. Rename or remove it, then try again."
  echo "Updating your copy in $TARGET"
else
  echo "Installing into $TARGET"
  mkdir -p "$TARGET" || fail "Couldn't create $TARGET."
fi
# Copy the code over; leaves .venv (packages) alone. Your login lives in ~/.gsb-api.
cp -R "$SRC/." "$TARGET/" || fail "Couldn't copy the files into $TARGET."
echo "$CHANNEL" > "$TARGET/.channel"

exec bash "$TARGET/setup.sh"
