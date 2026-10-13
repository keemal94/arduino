#!/bin/bash
# Stops every running bridge (frees the serial port, e.g. before uploading a sketch).
# Autostart stays installed; start it again with install-autostart.command or start-bridge.command.
# shellcheck source-path=SCRIPTDIR source=common.sh
. "$(dirname "$0")/common.sh"

stop_bridges
finish 0
