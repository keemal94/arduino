#!/bin/bash
# Makes the menu bar app "Claude Usage LCD" (in ~/Applications), which runs the bridge, and
# starts it now and at every login with a launchd agent. Safe to run again (e.g. after updating
# or moving this folder): it replaces the old setup. From Terminal, arguments go to the bridge,
# e.g.:  install-autostart.command --port /dev/cu.usbserial-110
# Run without arguments, it keeps the ones given before; uninstall-autostart.command clears them.
# shellcheck source-path=SCRIPTDIR source=common.sh
. "$(dirname "$0")/common.sh"

ensure_venv || finish 1
if ! mkdir -p "$APP_DIR" "$LOG_DIR" "$(dirname "$PLIST")" ||
    ! cp "$BRIDGE_DIR/usage_lcd_bridge.py" "$HERE/usage_lcd_menubar.py" "$APP_DIR/"; then
    echo "Could not copy the bridge to $APP_DIR."
    finish 1
fi

# A running bridge would keep its old settings, and holds the lock the new one needs.
stop_agent
stop_others

# The app: a launcher that runs the copy with the Python environment, and no Dock icon.
rm -rf "$APP_BUNDLE"
if ! mkdir -p "$APP_BUNDLE/Contents/MacOS" "$APP_BUNDLE/Contents/Resources" ||
    ! cat >"$APP_EXEC" <<'SH'; then
#!/bin/sh
# Claude Usage LCD: the menu bar app that bridge/macos/install-autostart.command set up.
APP_DIR="$HOME/Library/Application Support/usage-lcd"
if [ ! -x "$APP_DIR/venv/bin/python" ]; then
    osascript -e 'display alert "Claude Usage LCD is not set up" message "Double-click install-autostart.command in the bridge/macos folder of the project."'
    exit 1
fi
exec "$APP_DIR/venv/bin/python" "$APP_DIR/usage_lcd_menubar.py" "$@"
SH
    echo "Could not make $APP_BUNDLE."
    finish 1
fi
chmod 755 "$APP_EXEC"
"$VENV_PY" "$APP_DIR/usage_lcd_menubar.py" --make-icon "$APP_BUNDLE/Contents/Resources/AppIcon.icns" ||
    echo "(The app gets the standard icon.)"

# Absolute paths only: launchd expands no ~ and runs no shell.
if ! "$VENV_PY" -I - "$APP_BUNDLE" "$PLIST" "$LABEL" "$APP_EXEC" "$LAUNCHD_LOG" "$#" "$@" <<'PY'; then
import os
import plistlib
import sys

bundle, plist, label, program, out, given, *extra = sys.argv[1:]
if given == "0":  # a double-click: keep the options given before, e.g. --port
    try:
        with open(plist, "rb") as f:
            extra = plistlib.load(f)["ProgramArguments"][1:]
    except (OSError, ValueError, KeyError, plistlib.InvalidFileException):
        pass
    if extra:
        print("Keeping the options given before: " + " ".join(extra))
with open(os.path.join(bundle, "Contents", "Info.plist"), "wb") as f:
    plistlib.dump({
        "CFBundleName": "Claude Usage LCD",
        "CFBundleDisplayName": "Claude Usage LCD",
        "CFBundleIdentifier": label,
        "CFBundleExecutable": os.path.basename(program),
        "CFBundleIconFile": "AppIcon",
        "CFBundlePackageType": "APPL",
        "CFBundleShortVersionString": "0.1.0",
        "CFBundleVersion": "0.1.0",
        "LSMinimumSystemVersion": "11.0",
        "LSUIElement": True,  # a menu bar app: no Dock icon
    }, f)
with open(plist, "wb") as f:
    plistlib.dump({
        "Label": label,
        "ProgramArguments": [program] + extra,
        "RunAtLoad": True,
        "KeepAlive": {"SuccessfulExit": False},  # start again after a crash, not after Quit
        "ThrottleInterval": 30,
        "LimitLoadToSessionType": "Aqua",  # it draws in the menu bar
        "ProcessType": "Interactive",
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
        echo "launchd did not start the app: $error"
        echo "If you switched off its background item, switch it on in System Settings > General >"
        echo "Login Items & Extensions > Allow in the Background, then run this again."
        finish 1
    fi
    sleep 1
done
echo "Autostart installed: $PLIST"

# Running, and still running 2 s later (a bad argument or a second app exits right away)?
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
    echo "The app stopped right away. Run start-bridge.command to see why."
    for log in "$LAUNCHD_LOG" "$LOG_FILE"; do
        [ -s "$log" ] && { echo "--- end of $log"; tail -n 5 "$log"; }
    done
    finish 1
fi
echo "Claude Usage LCD is running: its icon is in the menu bar, at the top right of the screen."
echo "It starts at every login. The app is $APP_BUNDLE."
echo "Log file: $LOG_FILE"
echo "Before uploading a sketch with the Arduino IDE, choose Stop LCD Bridge in its menu."
finish 0
