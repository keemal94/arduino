# Shared by the .command scripts next to it (sourced, not run). Written for bash 3.2, the
# bash that macOS ships: no associative arrays, no mapfile.
# shellcheck shell=bash disable=SC2034  # the variables are for the scripts that source this file

if [ -z "${HOME:-}" ] || [ ! -d "$HOME" ]; then
    echo "HOME is not set, so this script cannot tell where to install."
    exit 1
fi

HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
BRIDGE_DIR=$(cd "$HERE/.." && pwd)
LABEL=io.github.keemal94.usage-lcd
# launchd runs a copy of the bridge from here, with its own Python environment, so the project
# folder may live anywhere (macOS keeps launchd agents out of Documents, Desktop and Downloads).
APP_DIR="$HOME/Library/Application Support/usage-lcd"
VENV="$APP_DIR/venv"
VENV_PY="$VENV/bin/python"
LOG_DIR="$HOME/Library/Logs/usage-lcd"
LOG_FILE="$LOG_DIR/bridge.log"      # the bridge writes this one itself
LAUNCHD_LOG="$LOG_DIR/launchd.log"  # what the autostart bridge prints outside its log, e.g. a crash
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
DOMAIN="gui/$(id -u)"
SERVICE="$DOMAIN/$LABEL"

# Gatekeeper asks about each downloaded script once. Once one of these runs, the others may too.
xattr -d com.apple.quarantine "$HERE"/*.command 2>/dev/null

# Keeps the Terminal window readable when the script was double-clicked, then exits with $1.
finish() {
    if [ -t 0 ] && [ -t 1 ]; then
        echo
        read -r -p "Press Return to close this window." _
    fi
    exit "$1"
}

# True when $1 runs and is a Python 3.9 or newer that can make a venv.
usable_python() {
    "$1" -c 'import sys, venv; sys.exit(sys.version_info < (3, 9))' >/dev/null 2>&1
}

# Sets PYTHON: USAGE_LCD_PYTHON, python.org's, Homebrew's, the one on PATH, then Apple's.
find_python() {
    local candidate
    for candidate in "${USAGE_LCD_PYTHON:-}" /Library/Frameworks/Python.framework/Versions/Current/bin/python3 \
        /opt/homebrew/bin/python3 /usr/local/bin/python3 "$(command -v python3)"; do
        # /usr/bin/python3 opens the "install the command line developer tools" dialog when they are missing.
        if [ -n "$candidate" ] && [ "$candidate" != /usr/bin/python3 ] && [ -x "$candidate" ] &&
            usable_python "$candidate"; then
            PYTHON=$candidate
            return 0
        fi
    done
    if xcode-select -p >/dev/null 2>&1 && usable_python /usr/bin/python3; then
        PYTHON=/usr/bin/python3
        return 0
    fi
    return 1
}

# Makes sure $VENV_PY has pyserial. Homebrew's Python refuses pip installs outside a venv (PEP 668).
# A venv that stopped working (e.g. Homebrew removed its Python) is made again.
ensure_venv() {
    if ! "$VENV_PY" -c 'import serial' >/dev/null 2>&1; then
        if ! find_python; then
            echo "Python 3 was not found. Install it from https://www.python.org/downloads/macos/"
            echo "and run this again."
            return 1
        fi
        echo "Setting up the bridge's Python environment with $PYTHON."
        echo "The first time this downloads pyserial, so it needs the internet."
        rm -rf "$VENV"
        if ! mkdir -p "$APP_DIR" || ! "$PYTHON" -m venv "$VENV"; then
            echo "Could not create the Python environment $VENV."
            return 1
        fi
    fi
    # Quick and offline once pyserial is there.
    if ! "$VENV_PY" -m pip install --disable-pip-version-check --quiet -r "$BRIDGE_DIR/requirements.txt"; then
        echo "Could not install pyserial (see above). It needs the internet the first time: try again."
        return 1
    fi
}

agent_loaded() {
    launchctl print "$SERVICE" >/dev/null 2>&1
}

# Unloads the autostart bridge for this login. Its plist stays, so it starts again at the next
# login. Returns 1 if it was not loaded.
stop_agent() {
    local i=0
    agent_loaded || return 1
    launchctl bootout "$SERVICE" >/dev/null 2>&1
    # bootout can return while the bridge is still stopping
    while agent_loaded && [ "$i" -lt 20 ]; do
        sleep 0.5
        i=$((i + 1))
    done
    return 0
}

# The Python processes of this user that run usage_lcd_bridge.py.
bridge_pids() {
    local pid comm
    for pid in $(pgrep -U "$(id -u)" -f 'usage_lcd_bridge[.]py'); do
        [ "$pid" = "$$" ] && continue
        comm=$(ps -o comm= -p "$pid") || continue
        case "${comm##*/}" in
            [Pp]ython*) echo "$pid" ;;
        esac
    done
}

# Stops the bridges started by hand (start-bridge.command). Returns 1 if there were none.
stop_others() {
    local pids i=0
    pids=$(bridge_pids)
    [ -n "$pids" ] || return 1
    # SIGTERM: the bridge closes its port and logs "stopped"
    # shellcheck disable=SC2086  # one argument per PID
    kill $pids 2>/dev/null
    while [ -n "$(bridge_pids)" ] && [ "$i" -lt 10 ]; do
        sleep 0.5
        i=$((i + 1))
    done
    pids=$(bridge_pids)
    # shellcheck disable=SC2086
    [ -z "$pids" ] || kill -9 $pids 2>/dev/null
    return 0
}

# Stops every bridge, the autostart one and those started by hand, and says what it did.
stop_bridges() {
    local stopped=
    stop_agent && stopped=1
    stop_others && stopped=1
    if [ -n "$stopped" ]; then
        echo "The bridge was stopped; its serial port is free now."
        echo "To start it again: double-click install-autostart.command (or log out and in again)."
    else
        echo "The bridge was not running."
    fi
}
