#!/usr/bin/env python3
"""Claude usage LCD bridge: reads the snapshot file written by the usage-lcd
mod and sends it to the Arduino over USB serial. The contract is
docs/PROTOCOL.md (sections 1 and 2). Python 3.9+, needs pyserial."""

import argparse
import errno
import json
import logging
import logging.handlers
import math
import os
import re
import signal
import socket
import sys
import time
from datetime import datetime, timezone
from decimal import ROUND_FLOOR, Decimal

try:
    import serial
    from serial.tools import list_ports as _list_ports
except ImportError:  # --dry-run and --version work without it
    serial = None
try:
    import fcntl
    import termios
except ImportError:  # Windows
    fcntl = termios = None

VERSION = "0.1.0"
BAUD = 9600
HELLO = "R|usage-lcd|"
# Probe order: Arduino, Arduino, CH340, FTDI, CP210x, then everything else.
VENDORS = {0x2341: "Arduino", 0x2A03: "Arduino", 0x1A86: "CH340", 0x0403: "FTDI", 0x10C4: "CP210x"}
KNOWN_VIDS = list(VENDORS)
# macOS names USB serial ports like this (original boards, CH340 with Apple's or WCH's driver,
# FTDI, CP210x); they are probed before all others.
MAC_USB_SERIAL = ("/dev/cu.usbmodem", "/dev/cu.usbserial", "/dev/cu.wchusbserial", "/dev/cu.SLAB_USBtoUART")
POLL_S = 1.0
HEARTBEAT_S = 10.0
RETRY_S = 5.0
HELLO_WAIT_S = 4.0
QUERY_WAIT_S = 1.5
LOCK_PORT = 47631
R_MAX = 2147483647  # the firmware rejects larger seconds-to-reset
HINTS = {"E|no-lcd": " (no LCD answered on I2C: check SDA/SCL wiring and LCD power)"}

log = logging.getLogger("usage_lcd_bridge")


# --- 1. Snapshot file -------------------------------------------------------

def _join(base, *parts):
    sep = "\\" if "\\" in base else "/"
    return sep.join([base.rstrip("\\/")] + list(parts))


def snapshot_path(env, override=None):
    """PROTOCOL.md 1: --file, USAGE_LCD_FILE, CLAUDE_CONFIG_DIR, USERPROFILE, HOME."""
    if override:
        return override
    if env.get("USAGE_LCD_FILE"):
        return env["USAGE_LCD_FILE"]
    for var, parts in (("CLAUDE_CONFIG_DIR", ()), ("USERPROFILE", (".claude",)), ("HOME", (".claude",))):
        if env.get(var):
            return _join(env[var], *parts, "usage-lcd.json")
    return None


class SnapshotReader:
    """Polls the file (stat first); a bad read keeps the last good snapshot."""

    def __init__(self, path):
        self.path = path
        self.snapshot = None
        self._seen = None  # (mtime, size) of the last good read

    def poll(self):
        try:
            st = os.stat(self.path)
        except OSError:
            self._seen = None
            return
        seen = (st.st_mtime_ns, st.st_size)
        if seen == self._seen:
            return
        try:
            with open(self.path, "rb") as f:
                data = json.loads(f.read().decode("utf-8-sig"))
        except (OSError, ValueError, RecursionError) as e:
            log.debug("snapshot not readable yet: %s", e)
            return
        if isinstance(data, dict):
            self.snapshot, self._seen = data, seen


# --- Snapshot -> serial line ---------------------------------------------

_ISO = re.compile(r"(\d{4})-(\d\d)-(\d\d)[Tt ](\d\d):(\d\d)(?::(\d\d)(?:[.,](\d+))?)?\s*([Zz]|[+-]\d\d(?::?\d\d)?)?$")


def parse_time(text):
    """ISO 8601 timestamp -> epoch seconds, or None. No offset means local time (ISO 8601, JS Date.parse)."""
    m = _ISO.match(text.strip()) if isinstance(text, str) else None
    if not m:
        return None
    y, mo, d, h, mi, s, frac, tz = m.groups()
    try:
        t = datetime(int(y), int(mo), int(d), int(h), int(mi), int(s or 0),
                     tzinfo=timezone.utc if tz else None).timestamp()
    except (ValueError, OverflowError, OSError):  # local times before 1970 fail on Windows
        return None
    if frac:
        t += float("0." + frac)
    if tz and tz.upper() != "Z":
        offset = int(tz[1:3]) * 3600 + (int(tz[-2:]) * 60 if len(tz) > 3 else 0)
        t += offset if tz[0] == "-" else -offset
    return t


def _is_number(v):
    return not isinstance(v, bool) and (isinstance(v, int) or (isinstance(v, float) and math.isfinite(v)))


def _find(windows, kind):
    for w in windows:
        if isinstance(w, dict) and w.get("kind") == kind and _is_number(w.get("percentUsed")):
            return w
    return None


def window_values(win, now):
    """(l, r) for one window at epoch time `now`, as PROTOCOL.md 2 defines them."""
    if win is None:
        return -1, -1
    resets = parse_time(win.get("resetsAt"))
    if resets is not None and resets <= now:
        return 1000, -1
    tenths = (100 - Decimal(repr(win["percentUsed"]))) * 10  # Decimal: .05 ties round exactly
    left = int((tenths + Decimal("0.5")).to_integral_value(rounding=ROUND_FLOOR))  # round half up
    r = -1 if resets is None else min(R_MAX, max(0, math.floor(resets - now)))
    return max(0, min(1000, left)), r


def snapshot_to_line(snapshot, now):
    """The line to send (without newline) for a parsed snapshot, or None, at epoch time `now`."""
    if not isinstance(snapshot, dict):
        return "W"
    schema = snapshot.get("schema")
    if isinstance(schema, bool) or schema != 1:
        return "W"
    windows = snapshot.get("windows")
    windows = windows if isinstance(windows, list) else []
    w5, w7 = _find(windows, "five_hour"), _find(windows, "seven_day")
    if w5 is None and w7 is None:
        return "W"
    return "U|%d|%d|%d|%d" % (window_values(w5, now) + window_values(w7, now))


# --- 2. Serial link ---------------------------------------------------------

def open_serial(name):
    ser = serial.Serial(name, BAUD, timeout=0.1, write_timeout=2, exclusive=True)
    try:
        lock_tty(ser)
    except OSError:
        _close(ser)
        raise
    return ser


def lock_tty(ser):
    """POSIX: exclusive=True only takes a flock, which the Arduino IDE's upload and Serial Monitor
    ignore. With TIOCEXCL their open fails with "Resource busy" instead of sharing the board.
    (Windows opens a COM port exclusively anyway.)"""
    if termios is None or not hasattr(termios, "TIOCEXCL"):
        return
    try:
        fcntl.ioctl(ser.fileno(), termios.TIOCEXCL)
    except OSError as e:
        if e.errno not in (errno.ENOTTY, errno.EINVAL):  # not a terminal: nothing to lock
            raise


def list_serial_ports():
    return list(_list_ports.comports())


def port_rank(p, preferred=None):
    vid = getattr(p, "vid", None)
    return (p.device != preferred, not p.device.startswith(MAC_USB_SERIAL),
            KNOWN_VIDS.index(vid) if vid in KNOWN_VIDS else len(KNOWN_VIDS))


def is_bluetooth(p):
    """Windows' serial-over-Bluetooth links: opening one can block for seconds."""
    return (getattr(p, "hwid", None) or "").upper().startswith("BTHENUM")


def skip_port(p):
    """Ports never probed: Windows' Bluetooth links; on macOS the /dev/tty.* twin of each port
    (its open can wait for a carrier) and every /dev/cu.* port that is not on USB:
    Bluetooth-Incoming-Port, debug-console and each paired Bluetooth device."""
    name = p.device
    if is_bluetooth(p) or name.startswith("/dev/tty."):
        return True
    return name.startswith("/dev/cu.") and getattr(p, "vid", None) is None and not name.startswith(MAC_USB_SERIAL)


def ordered_ports(ports, preferred=None):
    """Device names: the last good port, then macOS USB serial names, then known vendor ids in
    order, then the rest. Ports skip_port() names are never probed."""
    return [p.device for p in sorted(ports, key=lambda p: port_rank(p, preferred)) if not skip_port(p)]


def _close(ser):
    try:
        ser.close()
    except Exception:
        pass


class LineBuffer:
    def __init__(self):
        self.buf = b""

    def feed(self, data):
        *lines, self.buf = (self.buf + data).split(b"\n")
        if len(self.buf) > 256:  # noise without newlines
            self.buf = b""
        # keep printable ASCII only: drops \r and the junk byte some boards send on reset
        return [bytes(c for c in line if 32 <= c < 127).decode("ascii") for line in lines]


class StdoutLink:
    """--dry-run: prints the lines instead of sending them."""
    in_waiting = 0

    def write(self, data):
        print(data.decode("ascii").rstrip("\n"), flush=True)

    def close(self):
        pass


class Bridge:
    """Polls the snapshot and keeps the Arduino up to date. Clock, port opening
    and port listing are injectable for tests."""

    def __init__(self, reader, port=None, dry_run=False, open_port=None, list_ports=None,
                 monotonic=time.monotonic, wall=time.time, sleep=time.sleep):
        self.reader, self.port, self.dry_run = reader, port, dry_run
        self.open_port = open_port or open_serial
        self.list_ports = list_ports or list_serial_ports
        self.monotonic, self.wall, self.sleep = monotonic, wall, sleep
        self.link = self.link_name = self.preferred = None
        self.rx = LineBuffer()
        self.last_rx = None
        self.sent_line = self.sent_kind = None
        self.sent_at = 0.0
        self.next_poll = self.next_connect = monotonic()
        self.reported = set()  # problems already logged at INFO since the last connect
        self.stop = None  # set by run()

    def run(self, stop=None):
        """Runs forever or, given a threading.Event, until it is set; then closes the port."""
        self.stop = stop
        pause = self.sleep if stop is None else stop.wait
        while not self.stopping():
            try:
                self.step()
            except Exception:  # never let a background bridge die
                log.exception("unexpected error; retrying in %d s", RETRY_S)
                self.close()
                pause(RETRY_S)
            pause(0.2)
        self.close()

    def stopping(self):
        return self.stop is not None and self.stop.is_set()

    def step(self):
        now = self.monotonic()
        if now >= self.next_poll:
            self.reader.poll()
            self.next_poll = now + POLL_S
        if self.link is None and (now < self.next_connect or not self.connect()):
            return
        line = snapshot_to_line(self.reader.snapshot, self.wall())
        try:
            if self.link.in_waiting:
                if any(rx.startswith(HELLO) for rx in self._read(self.link)):
                    self.sent_line = None  # the Arduino restarted: send again now
            if line != self.sent_line or self.monotonic() - self.sent_at >= HEARTBEAT_S:
                self._send(line)
        except OSError as e:  # SerialException is an OSError too
            log.warning("%s: connection lost (%s); retrying every %d s", self.link_name, e, RETRY_S)
            self.close()

    def connect(self):
        if self.dry_run:
            self.link, self.link_name = StdoutLink(), "stdout"
            return True
        names = [self.port] if self.port else ordered_ports(self.list_ports(), self.preferred)
        if not names:
            self._report("no serial ports found: is the Arduino plugged in?")
        for name in names:
            if self.stopping():
                break
            ser = self._probe(name)
            if ser is not None:
                self.link, self.link_name, self.preferred = ser, name, name
                self.sent_line = self.sent_kind = None
                self.reported.clear()
                log.info("connected to the usage LCD on %s", name)
                return True
        self.next_connect = self.monotonic() + RETRY_S
        return False

    def close(self):
        if self.link is not None:
            _close(self.link)
        self.link = None
        self.next_connect = self.monotonic() + RETRY_S

    def _probe(self, name):
        try:
            ser = self.open_port(name)
        except OSError as e:
            # Windows says "PermissionError(13, 'Access is denied.'...)" in the user's language
            busy = isinstance(e, PermissionError) or "PermissionError" in str(e)
            # EAGAIN: another program holds the port's flock (pyserial's exclusive=True)
            if busy or e.errno in (errno.EACCES, errno.EBUSY, errno.EAGAIN):
                self._report("%s is busy or access is denied: close the Arduino IDE Serial Monitor "
                             "or any other program using it (%s)" % (name, e))
            else:
                self._report("%s: cannot open (%s)" % (name, e))
            return None
        found = False
        try:
            ser.reset_input_buffer()  # drop stale bytes; the board is still booting
            self.rx = LineBuffer()
            found = self._await_hello(ser, HELLO_WAIT_S)
            if not found and not self.stopping():
                ser.write(b"?\n")
                found = self._await_hello(ser, QUERY_WAIT_S)
            if not found and not self.stopping():
                self._report("%s: no answer from the usage_lcd sketch (is it uploaded?)" % name)
        except OSError as e:
            self._report("%s: %s" % (name, e))
        finally:
            if not found:  # whatever went wrong, never keep the port (Windows opens it exclusively)
                _close(ser)
        return ser if found else None

    def _await_hello(self, ser, seconds):
        end = self.monotonic() + seconds
        while self.monotonic() < end and not self.stopping():
            if any(line.startswith(HELLO) for line in self._read(ser)):
                return True
        return False

    def _read(self, ser):
        lines = self.rx.feed(ser.read(ser.in_waiting or 1))  # waits at most the port timeout
        for line in lines:
            if line and (line == self.last_rx or not line.startswith(("R|", "E|"))):
                log.debug("arduino: %r", line)
            elif line:
                log.log(logging.WARNING if line[0] == "E" else logging.INFO,
                        "arduino: %s%s", line, HINTS.get(line, ""))
            self.last_rx = line or self.last_rx
        return lines

    def _send(self, line):
        self.link.write((line + "\n").encode("ascii"))
        log.log(logging.INFO if line[0] != self.sent_kind else logging.DEBUG, "sent %s", line)
        self.sent_line, self.sent_kind, self.sent_at = line, line[0], self.monotonic()

    def _report(self, msg):
        log.log(logging.DEBUG if msg in self.reported else logging.INFO, msg)
        self.reported.add(msg)


# --- Process plumbing ---------------------------------------------------------

def acquire_lock(port):
    """Single instance: binds 127.0.0.1:<port> and returns the socket; raises OSError if it is taken.
    Never SO_REUSEADDR: on Windows that would let a second bridge bind the same port."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):  # Windows: forbid sharing the port
            s.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        s.bind(("127.0.0.1", port))
    except OSError:
        s.close()
        raise
    return s


def log_file(env, platform=None):
    if env.get("LOCALAPPDATA"):
        return os.path.join(env["LOCALAPPDATA"], "usage-lcd", "bridge.log")
    if (platform or sys.platform) == "darwin":  # where Console.app shows a user's logs
        return os.path.join(os.path.expanduser("~"), "Library", "Logs", "usage-lcd", "bridge.log")
    return os.path.join(os.path.expanduser("~"), ".cache", "usage-lcd", "bridge.log")


def setup_logging(verbose, path, console=True):
    """console=False: the log file only (the macOS menu bar app, whose stderr launchd keeps apart)."""
    log.setLevel(logging.DEBUG if verbose else logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    handlers = [logging.StreamHandler()] if console and sys.stderr is not None else []  # pythonw has no console
    error = None
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        handlers.append(logging.handlers.RotatingFileHandler(path, maxBytes=256 * 1024, backupCount=1,
                                                             encoding="utf-8"))
    except OSError as e:
        error = e
    for h in handlers:
        h.setFormatter(fmt)
        log.addHandler(h)
    if error:
        log.warning("cannot write the log file %s: %s", path, error)


def pip_hint():
    if sys.platform == "darwin":  # Homebrew's Python refuses pip installs outside a venv (PEP 668)
        return ("pyserial is not installed. Double-click bridge/macos/start-bridge.command: "
                "it sets up a Python environment with pyserial.")
    req = os.path.join(os.path.dirname(os.path.abspath(__file__)), "requirements.txt")
    return 'pyserial is not installed. Run: "%s" -m pip install -r "%s"' % (sys.executable, req)


def _interrupt(signum, frame):
    raise KeyboardInterrupt


def main(argv=None):
    p = argparse.ArgumentParser(
        description="Shows Claude usage on the Arduino LCD (docs/PROTOCOL.md).",
        epilog="Log: %s. Only one bridge runs at a time (it holds 127.0.0.1:%d; "
               "USAGE_LCD_LOCK_PORT changes that port)." % (log_file(os.environ), LOCK_PORT))
    p.add_argument("--port", help="use this serial port only, e.g. COM5 (default: find the board)")
    p.add_argument("--file", help="snapshot file to read (default: as in PROTOCOL.md)")
    p.add_argument("--list", action="store_true", help="list serial ports and exit")
    p.add_argument("--dry-run", action="store_true", help="print the lines instead of opening a port")
    p.add_argument("--verbose", "-v", action="store_true", help="log every line sent and received")
    p.add_argument("--version", action="version", version="usage_lcd_bridge " + VERSION)
    args = p.parse_args(argv)

    if args.list:
        if serial is None:
            print(pip_hint())
            return 1
        ports = sorted(list_serial_ports(), key=port_rank)
        width = max([8] + [len(port.device) for port in ports])  # macOS names are long
        for port in ports:
            ids = "%04X:%04X" % (port.vid, port.pid) if port.vid is not None else "no USB id"
            print("%-*s %-10s %-8s %s" % (width, port.device, ids, VENDORS.get(port.vid, ""), port.description))
        if not ports:
            print("No serial ports found.")
        return 0

    setup_logging(args.verbose, log_file(os.environ))
    path = snapshot_path(os.environ, args.file)
    if not path:
        log.error("cannot tell where the snapshot file is: set USAGE_LCD_FILE or use --file")
        return 1
    lock = None
    if not args.dry_run:
        if serial is None:
            log.error(pip_hint())
            return 1
        lock_port = int(os.environ.get("USAGE_LCD_LOCK_PORT") or LOCK_PORT)
        try:
            lock = acquire_lock(lock_port)
        except OSError as e:
            # The OS error tells "in use" apart from a port Windows reserved (e.g. for Hyper-V).
            stopper = "bridge/macos/stop-bridge.command" if sys.platform == "darwin" else "stop-bridge.cmd"
            log.info("another bridge is already running: 127.0.0.1:%d is taken (%s). %s stops it; "
                     "if none is running, set USAGE_LCD_LOCK_PORT to another port. Exiting.", lock_port, e, stopper)
            return 3
    log.info("usage_lcd_bridge %s reading %s", VERSION, path)
    bridge = Bridge(SnapshotReader(path), port=args.port, dry_run=args.dry_run)
    if sys.platform != "win32":  # launchctl bootout and stop-bridge.command send SIGTERM: stop as on Ctrl+C
        signal.signal(signal.SIGTERM, _interrupt)
    try:
        bridge.run()
    except KeyboardInterrupt:
        log.info("stopped")
    finally:
        bridge.close()
        if lock:
            lock.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
