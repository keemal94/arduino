#!/bin/bash
# Stops every running bridge, the menu bar app included (frees the serial port, e.g. before
# uploading a sketch). Autostart stays installed; open Claude Usage LCD to start it again.
# shellcheck source-path=SCRIPTDIR source=common.sh
. "$(dirname "$0")/common.sh"

stop_bridges
if [ -n "$STOPPED" ] && [ -d "$APP_BUNDLE" ]; then
    echo "To start it again: open Claude Usage LCD in your Applications folder (or log out and in)."
elif [ -n "$STOPPED" ]; then
    echo "To start it again: double-click start-bridge.command or install-autostart.command."
fi
finish 0
