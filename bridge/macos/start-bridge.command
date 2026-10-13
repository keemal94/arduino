#!/bin/bash
# Runs the bridge in this Terminal window (Ctrl+C or closing the window stops it). From Terminal,
# arguments are passed on, e.g.:
#   start-bridge.command --list    start-bridge.command --port /dev/cu.usbserial-110 --verbose
# shellcheck source-path=SCRIPTDIR source=common.sh
. "$(dirname "$0")/common.sh"

ensure_venv || finish 1
"$VENV_PY" "$BRIDGE_DIR/usage_lcd_bridge.py" "$@"
status=$?
[ "$status" -eq 0 ] && exit 0
echo
echo "The bridge has stopped (see the messages above)."
echo "Log file: $LOG_FILE"
finish "$status"
