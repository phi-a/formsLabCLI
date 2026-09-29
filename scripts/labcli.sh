#!/usr/bin/env sh
# Launch the lab console from the checkout's venv.
#
# The Windows twin is labcli.cmd. This one needs no codepage juggling -- a
# POSIX terminal is already UTF-8 -- but it does the same two useful things:
# resolve the venv relative to itself, and run from the repo root so the
# default output directory is <repo>/outputs.
set -eu

REPO=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
CONSOLE="$REPO/.venv/bin/labcli"

if [ ! -x "$CONSOLE" ]; then
    echo "No venv at $REPO/.venv." >&2
    echo "Create one and install the console:" >&2
    echo "    python3 -m venv .venv" >&2
    echo "    .venv/bin/pip install -e ." >&2
    exit 1
fi

cd "$REPO"
exec "$CONSOLE" "$@"
