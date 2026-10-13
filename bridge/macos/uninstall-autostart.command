#!/bin/bash
# Removes the menu bar app and its autostart (the launchd agent, the bridge's copy and its Python
# environment) and stops the running bridge. The log stays in ~/Library/Logs/usage-lcd.
# shellcheck source-path=SCRIPTDIR source=common.sh
. "$(dirname "$0")/common.sh"

stop_bridges
if [ -f "$PLIST" ]; then
    rm -f "$PLIST" && echo "Autostart removed: $PLIST"
else
    echo "Autostart was not installed."
fi
if [ -d "$APP_BUNDLE" ]; then
    rm -rf "$APP_BUNDLE" && echo "App removed: $APP_BUNDLE"
fi
# start-bridge.command sets the Python environment up again when it needs it.
rm -rf "$APP_DIR"
finish 0
