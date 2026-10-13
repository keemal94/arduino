#!/bin/bash
# Runs the bridge in the background now and at every login, with a launchd agent. Safe to run
# again (e.g. after updating or moving this folder): it replaces the old setup. From Terminal,
# arguments go to the bridge, e.g.:  install-autostart.command --port /dev/cu.usbserial-110
# shellcheck source-path=SCRIPTDIR source=common.sh
. "$(dirname "$0")/common.sh"

ensure_venv || finish 1
if ! mkdir -p "$APP_DIR" "$LOG_DIR" "$(dirname "$PLIST")" ||
    ! cp "$BRIDGE_DIR/usage_lcd_bridge.py" "$BRIDGE_DIR/requirements.txt" "$APP_DIR/"; then
    echo "Could not copy the bridge to $APP_DIR."
    finish 1
fi

# A running bridge would keep its old settings, and holds the lock the new one needs.
stop_agent
stop_others

# Absolute paths only: launchd expands no ~ and runs no shell.
if ! "$VENV_PY" - "$PLIST" "$LABEL" "$VENV_PY" "$APP_DIR/usage_lcd_bridge.py" "$LAUNCHD_LOG" "$@" <<'PY'; then
import plistlib
import sys

plist, label, python, bridge, out, *extra = sys.argv[1:]
with open(plist, "wb") as f:
    plistlib.dump({
        "Label": label,
        "ProgramArguments": [python, bridge, "--no-console-log"] + extra,
        "RunAtLoad": True,
        "KeepAlive": {"SuccessfulExit": False},  # start again after a crash, not after a clean stop
        "ThrottleInterval": 30,
        "StandardOutPath": out,
        "StandardErrorPath": out,
    }, f)
PY
    echo "Could not write $PLIST."
    finish 1
fi
chmod 644 "$PLIST"  # launchd refuses a plist others may write

launchctl enable "$SERVICE" 2>/dev/null  # in case it was disabled before
tries=0
until error=$(launchctl bootstrap "$DOMAIN" "$PLIST" 2>&1); do
    tries=$((tries + 1))
    if [ "$tries" -ge 5 ]; then
        echo "launchd did not start the bridge: $error"
        echo "If you switched off its background item, switch it on in System Settings > General >"
        echo "Login Items & Extensions > Allow in the Background, then run this again."
        finish 1
    fi
    sleep 1
done
echo "Autostart installed: $PLIST"

# Running, and still running 2 s later (a bad argument or a second bridge exits right away)?
running_pid() {
    launchctl list "$LABEL" 2>/dev/null | sed -n 's/^[[:space:]]*"PID" = \([0-9][0-9]*\);$/\1/p'
}
pid=
for _ in 1 2 3 4 5 6 7 8 9 10; do
    pid=$(running_pid)
    [ -n "$pid" ] && break
    sleep 0.5
done
[ -n "$pid" ] && sleep 2
if [ -z "$pid" ] || [ "$(running_pid)" != "$pid" ]; then
    echo "The bridge stopped right away. Run start-bridge.command to see why."
    for log in "$LAUNCHD_LOG" "$LOG_FILE"; do
        [ -s "$log" ] && { echo "--- end of $log"; tail -n 5 "$log"; }
    done
    finish 1
fi
echo "The bridge is running in the background."
echo "Log file: $LOG_FILE"
echo "Before uploading a sketch with the Arduino IDE, double-click stop-bridge.command to free the port."
finish 0
