#!/bin/bash
# Checks the Mac installer scripts: syntax, the channel option, and the
# "Python not installed" message, using a fake Mac (no network, no real Python).
set -u
cd "$(dirname "$0")/.."
fail() { echo "FAIL: $*"; exit 1; }
bash -n install.sh || fail "install.sh syntax"
bash -n setup.sh   || fail "setup.sh syntax"

T="$(mktemp -d)"; trap 'rm -rf "$T"' EXIT
mkdir -p "$T/bin" "$T/home/Projects/gsb-api"
printf '#!/bin/sh\necho Darwin\n' > "$T/bin/uname"
printf '#!/bin/sh\nexit 1\n' > "$T/bin/python3"
chmod +x "$T/bin/"*
cp setup.sh gsb_client.py requirements.txt "$T/home/Projects/gsb-api/"

out="$(HOME="$T/home" PATH="$T/bin:/usr/bin:/bin" bash "$T/home/Projects/gsb-api/setup.sh" 2>&1)"
echo "$out" | grep -q "Python isn't installed" || fail "no-python message"
echo "$out" | grep -q "install.sh | bash$"      || fail "main channel install line"

echo dev > "$T/home/Projects/gsb-api/.channel"
out="$(HOME="$T/home" PATH="$T/bin:/usr/bin:/bin" bash "$T/home/Projects/gsb-api/setup.sh" 2>&1)"
echo "$out" | grep -q "bash -s -- dev"          || fail "dev channel install line"
echo "$out" | grep -q "Channel: dev"            || fail "dev channel banner"

out="$(bash install.sh 'bad;name' 2>&1)"; echo "$out" | grep -q "Unknown channel" || fail "channel validation"
echo "scripts OK"
