#!/bin/sh
# Builds the host tests and lcdsim, then runs the tests.
# Output goes to $BUILD_DIR, default build/ next to this script (gitignored).
# lcdsim is then at "$BUILD_DIR/lcdsim".
set -eu
here=$(cd "$(dirname "$0")" && pwd)
src="$here/../usage_lcd"
out=${BUILD_DIR:-"$here/build"}
mkdir -p "$out"
cxx=${CXX:-g++}
"$cxx" -std=c++11 -Wall -Wextra -Werror -O1 -g -fsanitize=address,undefined -fno-sanitize-recover=all \
  -I "$src" -o "$out/test_display" "$here/test_display.cpp" "$src/UsageDisplay.cpp"
"$cxx" -std=c++11 -Wall -Wextra -Werror -O2 \
  -I "$src" -o "$out/lcdsim" "$here/lcdsim.cpp" "$src/UsageDisplay.cpp"
"$out/test_display"
