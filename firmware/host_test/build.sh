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

# lcdsim: the interface documented at the top of lcdsim.cpp.
cr=$(printf '\r')
got=$(printf '%s\n' '#show' '?' 'U|765|4980|410|266400' '#show' "@8000$cr" "#show$cr" 'bogus' | "$out/lcdsim" --raw)
want='+----------------+
Claude usage LCD
waiting for PC..
+----------------+
TX R|usage-lcd|1
+----------------+
5h 555555400 76%
7d 555300000 41%
+----------------+
+----------------+
5h resets  1h22m
7d resets  3d01h
+----------------+
TX E|bad-line'
[ "$got" = "$want" ] || { printf 'lcdsim: unexpected output:\n%s\n' "$got"; exit 1; }
got=$(printf '%s\n' 'U|765|-1|-1|-1' '#show' | "$out/lcdsim" | sed -n 2p)
[ "$got" = '5h ██████▊░░ 76%' ] || { echo "lcdsim: unexpected bar cells: $got"; exit 1; }
rc=0
echo '@x' | "$out/lcdsim" 2>/dev/null || rc=$?
[ "$rc" = 2 ] || { echo "lcdsim: bad @ value gave exit status $rc, not 2"; exit 1; }
echo "lcdsim: ok"
