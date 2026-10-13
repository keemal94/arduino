import errno
import json
import logging
import logging.handlers
import os
import sys
import time
from types import SimpleNamespace

import pytest
import serial

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import usage_lcd_bridge as b  # noqa: E402

NOW = b.parse_time("2026-10-12T18:03:12.500Z")
FIVE = {"kind": "five_hour", "percentUsed": 23.5, "resetsAt": "2026-10-12T21:00:00.000Z"}
SEVEN = {"kind": "seven_day", "percentUsed": 59, "resetsAt": "2026-10-16T09:00:00.000Z"}
GOOD = {"schema": 1, "writtenAt": "2026-10-12T18:03:12.345Z", "windows": [FIVE, SEVEN]}
GOOD_LINE = "U|765|10607|410|313007"


def snap(*windows, **extra):
    return dict({"schema": 1, "windows": list(windows)}, **extra)


def win(kind, pct, resets=None):
    w = {"kind": kind, "percentUsed": pct}
    if resets is not None:
        w["resetsAt"] = resets
    return w


# --- snapshot -> line --------------------------------------------------------

def test_both_windows():
    assert b.snapshot_to_line(GOOD, NOW) == GOOD_LINE


def test_one_window_missing():
    assert b.snapshot_to_line(snap(SEVEN), NOW) == "U|-1|-1|410|313007"
    assert b.snapshot_to_line(snap(FIVE), NOW) == "U|765|10607|-1|-1"


def test_other_kinds_ignored_and_no_known_window_is_W():
    spend = win("spend_limit", 140)
    assert b.snapshot_to_line(snap(spend, FIVE), NOW) == "U|765|10607|-1|-1"
    assert b.snapshot_to_line(snap(spend), NOW) == "W"
    assert b.snapshot_to_line(snap(), NOW) == "W"


def test_resets_at_in_the_past_or_now_means_full():
    assert b.snapshot_to_line(snap(win("five_hour", 90, "2026-10-12T18:00:00Z"), SEVEN), NOW) == \
        "U|1000|-1|410|313007"
    assert b.window_values(win("five_hour", 90, "2026-10-12T18:03:12.500Z"), NOW) == (1000, -1)


def test_resets_at_absent_or_unparseable():
    assert b.window_values(win("five_hour", 23.5), NOW) == (765, -1)
    assert b.window_values(win("five_hour", 23.5, "soon"), NOW) == (765, -1)
    assert b.window_values(win("five_hour", 23.5, 1760000000), NOW) == (765, -1)


def test_seconds_are_floored_and_never_negative():
    assert b.window_values(win("five_hour", 0, "2026-10-12T18:03:12.900Z"), NOW) == (1000, 0)
    assert b.window_values(win("five_hour", 0, "2026-10-12T18:03:14Z"), NOW) == (1000, 1)


@pytest.mark.parametrize("pct, left", [
    (0, 1000), (100, 0), (59, 410), (23.5, 765), (0.1, 999), (33.3, 667),
    (23.45, 766), (23.55, 765), (0.05, 1000), (99.95, 1), (100.05, 0),  # .05 ties round up
    (150, 0), (100.1, 0), (-5, 1000),  # clamped
])
def test_rounding_and_clamping(pct, left):
    assert b.window_values(win("seven_day", pct), NOW) == (left, -1)


@pytest.mark.parametrize("bad", ["50", True, None, float("nan"), float("inf")])
def test_bad_percent_means_window_absent(bad):
    assert b.snapshot_to_line(snap(win("five_hour", bad), SEVEN), NOW) == "U|-1|-1|410|313007"


@pytest.mark.parametrize("data", [None, [], "x", {"windows": [FIVE]}, snap(FIVE, schema=2),
                                  snap(FIVE, schema=True), snap(FIVE, schema="1"), {"schema": 1, "windows": 7}])
def test_no_usable_snapshot_is_W(data):
    assert b.snapshot_to_line(data, NOW) == "W"


def test_parse_time_offsets():
    utc = b.parse_time("2026-10-12T18:03:12Z")
    assert b.parse_time("2026-10-12T20:03:12+02:00") == utc
    assert b.parse_time("2026-10-12T13:03:12-0500") == utc
    assert b.parse_time("2026-10-12T21:03:12+03") == utc
    assert b.parse_time("2026-10-12T18:03:12.250z") == utc + 0.25
    assert b.parse_time("2026-13-12T18:03:12Z") is None
    assert b.parse_time("") is None


@pytest.mark.skipif(not hasattr(time, "tzset"), reason="needs time.tzset")
def test_parse_time_without_offset_is_local_time(monkeypatch):
    monkeypatch.setenv("TZ", "TST-3")  # POSIX spelling of UTC+3, needs no tz database
    time.tzset()
    try:
        assert b.parse_time("2026-10-12T21:00:00") == b.parse_time("2026-10-12T18:00:00Z")
    finally:
        monkeypatch.undo()
        time.tzset()


# --- snapshot path -----------------------------------------------------------

WIN_ENV = {"USERPROFILE": r"C:\Users\Some Name", "HOME": "/c/Users/Some Name"}


def test_path_userprofile_with_spaces_beats_home():
    assert b.snapshot_path(WIN_ENV) == r"C:\Users\Some Name\.claude\usage-lcd.json"


def test_path_resolution_order():
    env = dict(WIN_ENV, CLAUDE_CONFIG_DIR="D:\\Claude Config\\", USAGE_LCD_FILE=r"E:\x y\snap.json")
    assert b.snapshot_path(env, r"F:\override.json") == r"F:\override.json"
    assert b.snapshot_path(env) == r"E:\x y\snap.json"
    env["USAGE_LCD_FILE"] = ""  # empty counts as unset
    assert b.snapshot_path(env) == r"D:\Claude Config\usage-lcd.json"
    del env["CLAUDE_CONFIG_DIR"]
    assert b.snapshot_path(env) == r"C:\Users\Some Name\.claude\usage-lcd.json"
    del env["USERPROFILE"]
    assert b.snapshot_path(env) == "/c/Users/Some Name/.claude/usage-lcd.json"
    assert b.snapshot_path({"CLAUDE_CONFIG_DIR": "/home/u/cfg/"}) == "/home/u/cfg/usage-lcd.json"
    assert b.snapshot_path({}) is None


def test_path_on_macos_is_home_only():
    # The desktop app and launchd give a Mac process HOME, never USERPROFILE.
    assert b.snapshot_path({"HOME": "/Users/Some Name"}) == "/Users/Some Name/.claude/usage-lcd.json"
    assert b.snapshot_path({"HOME": "/Users/Some Name/", "USERPROFILE": ""}) == \
        "/Users/Some Name/.claude/usage-lcd.json"


# --- tolerant reading --------------------------------------------------------

def test_reader_keeps_last_good_snapshot(tmp_path):
    path = tmp_path / "Some Name" / "usage-lcd.json"
    path.parent.mkdir()
    reader = b.SnapshotReader(str(path))
    reader.poll()
    assert reader.snapshot is None  # missing file

    good = json.dumps(GOOD)
    path.write_text(good, encoding="utf-8")
    reader.poll()
    assert b.snapshot_to_line(reader.snapshot, NOW) == GOOD_LINE

    for broken in (good[: len(good) // 2], "", "not json", "[1, 2]"):
        path.write_text(broken, encoding="utf-8")
        reader.poll()
        assert reader.snapshot == GOOD
    path.unlink()
    reader.poll()
    assert reader.snapshot == GOOD

    path.write_text(json.dumps(snap(FIVE, schema=2)), encoding="utf-8")
    reader.poll()
    assert b.snapshot_to_line(reader.snapshot, NOW) == "W"

    path.write_bytes(b"\xef\xbb\xbf" + good.encode())  # UTF-8 BOM
    reader.poll()
    assert reader.snapshot == GOOD

    path.write_text(json.dumps(snap()), encoding="utf-8")  # empty windows
    reader.poll()
    assert b.snapshot_to_line(reader.snapshot, NOW) == "W"


def test_normal_fixture_as_the_mod_writes_it():
    # tests/fixtures/normal.json is byte for byte what the mod's own tests expect it to write.
    reader = b.SnapshotReader(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..",
                                           "tests", "fixtures", "normal.json"))
    reader.poll()
    assert reader.snapshot == dict(GOOD, source="usage-lcd mod 0.1.0")
    assert b.snapshot_to_line(reader.snapshot, NOW) == GOOD_LINE


def test_reader_stats_before_reading(tmp_path, monkeypatch):
    path = tmp_path / "usage-lcd.json"
    path.write_text(json.dumps(GOOD), encoding="utf-8")
    opens = []
    monkeypatch.setattr(b, "open", lambda *a, **k: opens.append(a) or open(*a, **k), raising=False)
    reader = b.SnapshotReader(str(path))
    for _ in range(3):
        reader.poll()
    assert len(opens) == 1
    path.write_text(json.dumps(snap(FIVE)), encoding="utf-8")
    reader.poll()
    assert len(opens) == 2 and reader.snapshot == snap(FIVE)


# --- serial session ----------------------------------------------------------

HELLO = b"R|usage-lcd|1\r\n"


class Clock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t

    def advance(self, s):
        self.t += s


class FakeSerial:
    """Answers like the sketch would; read() advances the clock like a 0.1 s port timeout."""

    def __init__(self, clock, hello_at=None, answer_query=False, stale=b""):
        self.clock, self.answer_query = clock, answer_query
        self.incoming = bytearray(stale)
        self.events = [] if hello_at is None else [(clock() + hello_at, HELLO)]
        self.written, self.closed, self.fail, self.fail_write = [], False, None, None

    def _due(self):
        for ev in [e for e in self.events if e[0] <= self.clock()]:
            self.incoming += ev[1]
            self.events.remove(ev)

    @property
    def in_waiting(self):
        if self.fail:
            raise self.fail
        self._due()
        return len(self.incoming)

    def read(self, n=1):
        self._due()
        if not self.incoming:
            self.clock.advance(0.1)
            self._due()
        data = bytes(self.incoming[:n])
        del self.incoming[:n]
        return data

    def write(self, data):
        if self.fail or self.fail_write:
            raise self.fail or self.fail_write
        self.written.append(data)
        if data == b"?\n" and self.answer_query:
            self.incoming += HELLO

    def reset_input_buffer(self):
        self.incoming.clear()

    def close(self):
        self.closed = True


class StaticReader:
    def __init__(self, snapshot):
        self.snapshot = snapshot

    def poll(self):
        pass


def make_bridge(clock, ports, vids=None, snapshot=GOOD, **kw):
    """ports: name -> list of FakeSerial or exceptions, used one per open (the last one repeats)."""
    opened = []

    def open_port(name):
        opened.append(name)
        queue = ports[name]
        item = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(item, Exception):
            raise item
        return item

    infos = [SimpleNamespace(device=n, vid=(vids or {}).get(n), pid=None) for n in ports]
    kw.setdefault("list_ports", lambda: infos)
    bridge = b.Bridge(StaticReader(snapshot), open_port=open_port, monotonic=clock, wall=lambda: NOW,
                      sleep=clock.advance, **kw)
    return bridge, opened


def lines(fake):
    return [w.decode() for w in fake.written]


@pytest.fixture(autouse=True)
def debug_logs(caplog):
    caplog.set_level(logging.DEBUG, logger="usage_lcd_bridge")


def test_handshake_hello_drains_stale_input(caplog):
    clock = Clock()
    fake = FakeSerial(clock, hello_at=1.5, stale=b"old junk line\n")
    bridge, opened = make_bridge(clock, {"COM3": [fake]})
    bridge.step()
    assert bridge.link is fake and opened == ["COM3"]
    assert lines(fake) == [GOOD_LINE + "\n"]  # no "?" needed
    assert 1.5 <= clock() - 100 < 2
    assert "old junk" not in caplog.text and "arduino: R|usage-lcd|1" in caplog.text


def test_handshake_via_query():
    clock = Clock()
    fake = FakeSerial(clock, answer_query=True)
    bridge, _ = make_bridge(clock, {"COM3": [fake]})
    bridge.step()
    assert bridge.link is fake
    assert lines(fake) == ["?\n", GOOD_LINE + "\n"]
    assert 4 <= clock() - 100 < 4.5


def test_silent_port_rejected_known_vendor_first():
    clock = Clock()
    silent, board = FakeSerial(clock), FakeSerial(clock, hello_at=1.0)
    bridge, opened = make_bridge(clock, {"COM7": [board], "COM3": [silent]}, vids={"COM3": 0x1A86})
    bridge.step()
    assert opened == ["COM3", "COM7"]
    assert silent.closed and lines(silent) == ["?\n"]
    assert bridge.link is board and bridge.link_name == "COM7"


def test_no_board_retries_every_5s_and_logs_once(caplog):
    clock = Clock()
    bridge, opened = make_bridge(clock, {"COM3": [FakeSerial(clock)]})
    bridge.step()
    assert bridge.link is None and opened == ["COM3"]
    clock.advance(4.9)
    bridge.step()
    assert opened == ["COM3"]
    clock.advance(0.1)
    bridge.step()
    assert opened == ["COM3", "COM3"]
    infos = [r for r in caplog.records if "no answer" in r.message and r.levelno == logging.INFO]
    assert len(infos) == 1


def test_no_ports_at_all(caplog):
    clock = Clock()
    bridge, _ = make_bridge(clock, {}, list_ports=lambda: [])
    bridge.step()
    assert bridge.link is None and "no serial ports found" in caplog.text


def test_busy_port_then_free(caplog):
    clock = Clock()
    busy = serial.SerialException("could not open port 'COM3': PermissionError(13, 'Access is denied.', None, 5)")
    fake = FakeSerial(clock, hello_at=1.0)
    bridge, opened = make_bridge(clock, {"COM3": [busy, fake]}, port="COM3",
                                 list_ports=lambda: pytest.fail("--port must not list ports"))
    bridge.step()
    assert bridge.link is None and "COM3 is busy" in caplog.text
    clock.advance(5)
    bridge.step()
    assert bridge.link is fake and opened == ["COM3", "COM3"]


@pytest.mark.parametrize("error", [
    # pyserial's flock: another program opened it with exclusive=True
    serial.SerialException(errno.EAGAIN, "Could not exclusively lock port /dev/cu.usbserial-110: "
                                         "[Errno 35] Resource temporarily unavailable"),
    # TIOCEXCL: the Arduino IDE's Serial Monitor holds it
    serial.SerialException(errno.EBUSY, "could not open port /dev/cu.usbserial-110: "
                                        "[Errno 16] Resource busy: '/dev/cu.usbserial-110'"),
])
def test_busy_port_on_macos(error, caplog):
    clock = Clock()
    bridge, _ = make_bridge(clock, {"/dev/cu.usbserial-110": [error]}, port="/dev/cu.usbserial-110")
    bridge.step()
    assert bridge.link is None and "/dev/cu.usbserial-110 is busy" in caplog.text


def test_vanished_port_logged():
    clock = Clock()
    gone = serial.SerialException("could not open port 'COM3': FileNotFoundError(2, 'not found', None, 2)")
    bridge, _ = make_bridge(clock, {"COM3": [gone]})
    bridge.step()
    assert bridge.link is None and "COM3: cannot open" in str(bridge.reported)


@pytest.mark.parametrize("where", ["fail", "fail_write"])  # unplugged: in_waiting fails too, or only write
@pytest.mark.parametrize("error", [serial.SerialException("ClearCommError failed (PermissionError(13, ...))"),
                                   serial.SerialTimeoutException("Write timeout"),
                                   PermissionError(13, "The device does not recognize the command")])
def test_link_failure_reconnects(where, error, caplog):
    clock = Clock()
    first, second = FakeSerial(clock, hello_at=1.0), FakeSerial(clock, hello_at=1.0)
    bridge, opened = make_bridge(clock, {"COM3": [first, second]})
    bridge.step()
    assert bridge.link is first
    setattr(first, where, error)
    clock.advance(10)
    bridge.step()
    assert bridge.link is None and first.closed and "connection lost" in caplog.text
    clock.advance(4)
    bridge.step()
    assert opened == ["COM3"]
    clock.advance(1)
    bridge.step()
    assert bridge.link is second and lines(second) == [GOOD_LINE + "\n"]


def test_probe_closes_the_port_on_any_error():
    clock = Clock()
    fake = FakeSerial(clock, hello_at=1.0)

    def broken_read(n=1):
        raise ValueError("driver returned junk")

    fake.read = broken_read
    bridge, _ = make_bridge(clock, {"COM3": [fake]})
    with pytest.raises(ValueError):  # run() logs it and retries
        bridge.step()
    assert fake.closed and bridge.link is None


def test_send_on_change_and_10s_heartbeat():
    clock = Clock()
    fake = FakeSerial(clock, hello_at=1.0)
    bridge, _ = make_bridge(clock, {"COM3": [fake]}, snapshot=None)
    bridge.step()
    assert lines(fake) == ["W\n"]
    clock.advance(9.9)
    bridge.step()
    assert lines(fake) == ["W\n"]
    clock.advance(0.1)
    bridge.step()
    assert lines(fake) == ["W\n", "W\n"]
    bridge.reader.snapshot = GOOD
    clock.advance(0.2)
    bridge.step()
    assert lines(fake)[-1] == GOOD_LINE + "\n" and len(fake.written) == 3


def test_file_polled_about_once_a_second():
    clock = Clock()
    polls = []
    bridge = b.Bridge(SimpleNamespace(snapshot=None, poll=lambda: polls.append(clock())), dry_run=True,
                      monotonic=clock, wall=lambda: NOW)
    for _ in range(10):
        bridge.step()
        clock.advance(0.25)
    assert polls == [100.0, 101.0, 102.0]


def test_arduino_restart_resends_and_errors_logged(caplog):
    clock = Clock()
    fake = FakeSerial(clock, hello_at=1.0)
    bridge, _ = make_bridge(clock, {"COM3": [fake]})
    bridge.step()
    fake.incoming += b"E|no-lcd\r\n"
    bridge.step()
    assert len(fake.written) == 1
    assert any(r.levelno == logging.WARNING and "E|no-lcd" in r.message and "SDA/SCL" in r.message
               for r in caplog.records)
    fake.incoming += HELLO
    bridge.step()
    assert lines(fake) == [GOOD_LINE + "\n"] * 2


class Stopper:
    """A threading.Event whose wait() moves the test clock; it sets itself after `waits` waits."""

    def __init__(self, clock, waits=None):
        self.clock, self.left, self.flag = clock, waits, False

    def is_set(self):
        return self.flag

    def set(self):
        self.flag = True

    def wait(self, seconds):
        self.clock.advance(seconds)
        if self.left is not None:
            self.left -= 1
            self.flag = self.flag or self.left <= 0
        return self.flag


def test_run_until_stopped_then_close_the_port():
    # The macOS menu bar app runs the bridge on a thread and stops it from the menu.
    clock = Clock()
    fake = FakeSerial(clock, hello_at=1.0)
    bridge, _ = make_bridge(clock, {"COM3": [fake]})
    bridge.run(Stopper(clock, waits=3))
    assert lines(fake) == [GOOD_LINE + "\n"] and fake.closed and bridge.link is None


def test_stop_cuts_a_probe_short():
    clock = Clock()
    silent, other = FakeSerial(clock), FakeSerial(clock, hello_at=1.0)
    bridge, opened = make_bridge(clock, {"COM3": [silent], "COM4": [other]})
    stop = Stopper(clock)
    read = silent.read
    silent.read = lambda n=1: stop.set() or read(n)  # stop while it waits for the hello
    bridge.run(stop)
    assert clock() - 100 < 0.5 and silent.closed and lines(silent) == []  # no "?" sent
    assert opened == ["COM3"] and bridge.link is None


def test_dry_run_prints_lines(capsys):
    clock = Clock()
    bridge = b.Bridge(StaticReader(GOOD), dry_run=True, monotonic=clock, wall=lambda: NOW,
                      list_ports=lambda: pytest.fail("dry run must not touch ports"))
    bridge.step()
    bridge.step()
    assert capsys.readouterr().out == GOOD_LINE + "\n"


def test_run_survives_unexpected_errors(caplog):
    clock = Clock()
    bridge = b.Bridge(StaticReader(GOOD), dry_run=True, monotonic=clock, wall=lambda: NOW)
    calls = []

    def step():
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("boom")
        if len(calls) == 3:
            raise KeyboardInterrupt

    bridge.step, bridge.sleep = step, clock.advance
    with pytest.raises(KeyboardInterrupt):
        bridge.run()
    assert len(calls) == 3 and "unexpected error" in caplog.text


def test_port_order():
    vids = [("COM1", None), ("COM3", 0x10C4), ("COM4", 0x1A86), ("COM5", 0x2341), ("COM6", 0x0403),
            ("COM8", 0x2A03), ("COM9", 0x1234)]
    infos = [SimpleNamespace(device=d, vid=v) for d, v in vids]
    assert b.ordered_ports(infos) == ["COM5", "COM8", "COM4", "COM6", "COM3", "COM1", "COM9"]
    assert b.ordered_ports(infos, preferred="COM9")[0] == "COM9"


def test_port_order_skips_bluetooth():
    infos = [SimpleNamespace(device="COM3", vid=None, hwid="BTHENUM\\{00001101-0000-1000-8000-00805F9B34FB}_LOCALMFG&0002\\7&1"),
             SimpleNamespace(device="COM4", vid=None, hwid="ACPI\\PNP0501\\1"),
             SimpleNamespace(device="COM5", vid=0x1A86, hwid="USB VID:PID=1A86:7523 SER= LOCATION=1-2")]
    assert b.ordered_ports(infos) == ["COM5", "COM4"]


def mac_port(device, vid=None):
    """A port as pyserial lists it on macOS: USB ones carry ids, the rest hwid "n/a"."""
    hwid = "USB VID:PID=%04X:7523 LOCATION=0-1.1" % vid if vid is not None else "n/a"
    return SimpleNamespace(device=device, vid=vid, hwid=hwid)


MAC_PORTS = [
    mac_port("/dev/cu.Bluetooth-Incoming-Port"),
    mac_port("/dev/cu.RedmiBuds6Active"),  # a paired Bluetooth device
    mac_port("/dev/cu.debug-console"),
    mac_port("/dev/cu.SomeUsbGadget", 0x1234),  # on USB, but no USB-serial name and an unknown vendor
    mac_port("/dev/tty.usbserial-110", 0x1A86),  # the dial-in twin; pyserial lists only cu
    mac_port("/dev/cu.wchusbserial120"),  # a vendor driver that gives no USB ids
    mac_port("/dev/cu.SLAB_USBtoUART", 0x10C4),
    mac_port("/dev/cu.usbserial-A10K", 0x0403),
    mac_port("/dev/cu.usbserial-110", 0x1A86),  # CH340 clone, Apple's driver
    mac_port("/dev/cu.usbmodem14101", 0x2341),  # original UNO or Mega
]


def test_port_order_on_macos():
    assert b.ordered_ports(MAC_PORTS) == [
        "/dev/cu.usbmodem14101", "/dev/cu.usbserial-110", "/dev/cu.usbserial-A10K", "/dev/cu.SLAB_USBtoUART",
        "/dev/cu.wchusbserial120", "/dev/cu.SomeUsbGadget"]
    assert b.ordered_ports(MAC_PORTS, preferred="/dev/cu.SomeUsbGadget")[0] == "/dev/cu.SomeUsbGadget"


@pytest.mark.parametrize("device", ["/dev/cu.Bluetooth-Incoming-Port", "/dev/cu.RedmiBuds6Active",
                                    "/dev/cu.debug-console", "/dev/tty.usbserial-110", "/dev/tty.usbmodem14101",
                                    "/dev/tty.Bluetooth-Incoming-Port"])
def test_macos_ports_never_probed(device):
    assert b.skip_port(mac_port(device, 0x1A86 if "usb" in device else None))


@pytest.mark.parametrize("device", ["COM3", "/dev/ttyUSB0", "/dev/ttyACM0", "/dev/ttyS0"])
def test_other_systems_ports_are_not_skipped_by_the_macos_rules(device):
    assert not b.skip_port(SimpleNamespace(device=device, vid=None))


def test_macos_bluetooth_ports_never_opened(caplog):
    clock = Clock()
    ports = {name: [FakeSerial(clock, hello_at=1.0)] for name in
             ("/dev/cu.Bluetooth-Incoming-Port", "/dev/cu.debug-console", "/dev/cu.RedmiBuds6Active")}
    bridge, opened = make_bridge(clock, ports)
    bridge.step()
    assert opened == [] and bridge.link is None and "no serial ports found" in caplog.text

    board = FakeSerial(clock, hello_at=1.0)
    ports["/dev/cu.usbserial-110"] = [board]
    clock.advance(5)
    bridge, opened = make_bridge(clock, ports, vids={"/dev/cu.usbserial-110": 0x1A86})
    bridge.step()
    assert opened == ["/dev/cu.usbserial-110"] and bridge.link is board


def test_port_option_is_never_filtered():
    clock = Clock()
    fake = FakeSerial(clock, hello_at=1.0)
    bridge, opened = make_bridge(clock, {"/dev/tty.usbserial-110": [fake]}, port="/dev/tty.usbserial-110",
                                 list_ports=lambda: pytest.fail("--port must not list ports"))
    bridge.step()
    assert opened == ["/dev/tty.usbserial-110"] and bridge.link is fake


class FakePort:
    def __init__(self, calls):
        self.calls = calls

    def fileno(self):
        return 7

    def close(self):
        self.calls.append("close")


def fake_open(monkeypatch, ioctl):
    calls = []
    monkeypatch.setattr(b.serial, "Serial", lambda *a, **k: calls.append(("open", k["exclusive"])) or FakePort(calls))
    monkeypatch.setattr(b.fcntl, "ioctl", lambda fd, request: calls.append(("ioctl", fd, request)) or ioctl())
    return calls


posix_only = pytest.mark.skipif(b.termios is None, reason="POSIX only: Windows opens COM ports exclusively")


@posix_only
def test_open_takes_the_tty_exclusively(monkeypatch):
    # flock alone does not stop the Arduino IDE's upload or Serial Monitor; TIOCEXCL does.
    calls = fake_open(monkeypatch, lambda: None)
    port = b.open_serial("/dev/cu.usbserial-110")
    assert isinstance(port, FakePort) and calls == [("open", True), ("ioctl", 7, b.termios.TIOCEXCL)]


@posix_only
def test_open_ignores_tiocexcl_on_a_non_terminal(monkeypatch):
    def ioctl():
        raise OSError(errno.ENOTTY, "Inappropriate ioctl for device")

    calls = fake_open(monkeypatch, ioctl)
    assert isinstance(b.open_serial("/dev/cu.usbserial-110"), FakePort) and "close" not in calls


@posix_only
def test_open_closes_the_port_when_tiocexcl_fails(monkeypatch):
    def ioctl():
        raise OSError(errno.EIO, "Input/output error")

    calls = fake_open(monkeypatch, ioctl)
    with pytest.raises(OSError):
        b.open_serial("/dev/cu.usbserial-110")
    assert calls[-1] == "close"


def test_line_buffer():
    buf = b.LineBuffer()
    assert buf.feed(b"R|usage-") == []
    assert buf.feed(b"lcd|1\r\nE|bad") == ["R|usage-lcd|1"]
    assert buf.feed(b"-line\n") == ["E|bad-line"]
    assert buf.feed(b"\x00\xf0R|usage-lcd|1\r\n") == ["R|usage-lcd|1"]  # reset glitch byte
    assert buf.feed(b"x" * 300) == [] and buf.buf == b""


@pytest.fixture
def own_handlers():
    """Removes the handlers main()/setup_logging() add to the module logger."""
    before = set(b.log.handlers)
    yield
    for h in set(b.log.handlers) - before:
        b.log.removeHandler(h)
        h.close()


def test_single_instance_lock():
    first = b.acquire_lock(0)
    port = first.getsockname()[1]
    try:
        with pytest.raises(OSError):
            b.acquire_lock(port)
    finally:
        first.close()
    b.acquire_lock(port).close()  # free again once the first one is gone


def test_lock_socket_is_exclusive_on_windows(monkeypatch):
    calls = []

    class FakeSocket:
        def __init__(self, *args):
            pass

        def setsockopt(self, level, option, value):
            calls.append(option)

        def bind(self, address):
            calls.append(address)

    monkeypatch.setattr(b.socket, "SO_EXCLUSIVEADDRUSE", -5, raising=False)  # its value on Windows
    monkeypatch.setattr(b.socket, "socket", FakeSocket)
    b.acquire_lock(47631)
    assert calls == [-5, ("127.0.0.1", 47631)]  # and never SO_REUSEADDR


@pytest.mark.parametrize("platform, stopper", [("win32", " stop-bridge.cmd stops it;"),
                                               ("darwin", " bridge/macos/stop-bridge.command stops it;")])
def test_second_bridge_exits_3(platform, stopper, tmp_path, monkeypatch, caplog, own_handlers):
    first = b.acquire_lock(0)
    monkeypatch.setenv("USAGE_LCD_LOCK_PORT", str(first.getsockname()[1]))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setattr(sys, "platform", platform)
    # A lock that lets it through must fail here, not hang probing the real serial ports.
    monkeypatch.setattr(b.Bridge, "run", lambda self, stop=None: pytest.fail("the lock let a second bridge run"))
    try:
        assert b.main(["--file", str(tmp_path / "usage-lcd.json")]) == 3
    finally:
        first.close()
    assert "another bridge is already running" in caplog.text and stopper in caplog.text


def test_file_only_logging(tmp_path, own_handlers):
    # The macOS menu bar app: launchd keeps its console output in a file of its own.
    path = tmp_path / "usage-lcd" / "bridge.log"
    b.setup_logging(False, str(path), console=False)
    b.log.info("logged to the file only")
    assert [type(h) for h in set(b.log.handlers)] == [logging.handlers.RotatingFileHandler]
    assert "logged to the file only" in path.read_text(encoding="utf-8")


def test_log_file_per_system(tmp_path):
    home = os.path.expanduser("~")
    assert b.log_file({}, "darwin") == os.path.join(home, "Library", "Logs", "usage-lcd", "bridge.log")
    assert b.log_file({}, "linux") == os.path.join(home, ".cache", "usage-lcd", "bridge.log")
    for platform in ("win32", "darwin", "linux"):
        assert b.log_file({"LOCALAPPDATA": str(tmp_path)}, platform) == \
            os.path.join(str(tmp_path), "usage-lcd", "bridge.log")


@pytest.mark.parametrize("platform, hint", [("win32", "-m pip install -r"), ("linux", "-m pip install -r"),
                                            ("darwin", "bridge/macos/start-bridge.command")])
def test_pip_hint(platform, hint, monkeypatch):
    monkeypatch.setattr(sys, "platform", platform)
    assert hint in b.pip_hint()


def test_no_console_like_pythonw(tmp_path, monkeypatch, own_handlers):
    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "stderr", None)
    path = tmp_path / "Some Name" / "usage-lcd" / "bridge.log"
    b.setup_logging(False, str(path))
    b.log.info("logged without a console")
    b.StdoutLink().write(b"W\n")
    assert [type(h) for h in set(b.log.handlers)] == [logging.handlers.RotatingFileHandler]
    assert "logged without a console" in path.read_text(encoding="utf-8")


@pytest.mark.parametrize("ports, want", [
    ([SimpleNamespace(device="COM3", vid=0x1A86, pid=0x7523, description="USB-SERIAL CH340 (COM3)"),
      SimpleNamespace(device="COM1", vid=None, pid=None, description="Communications Port (COM1)")],
     ["COM3     1A86:7523  CH340    USB-SERIAL CH340 (COM3)",
      "COM1     no USB id           Communications Port (COM1)"]),
    ([SimpleNamespace(device="/dev/cu.Bluetooth-Incoming-Port", vid=None, pid=None, description="n/a"),
      SimpleNamespace(device="/dev/cu.usbserial-110", vid=0x1A86, pid=0x7523, description="USB Serial")],
     ["/dev/cu.usbserial-110           1A86:7523  CH340    USB Serial",
      "/dev/cu.Bluetooth-Incoming-Port no USB id           n/a"]),
])
def test_list(ports, want, monkeypatch, capsys):
    monkeypatch.setattr(b, "list_serial_ports", lambda: ports)
    assert b.main(["--list"]) == 0
    assert capsys.readouterr().out.splitlines() == want


def test_version(capsys):
    with pytest.raises(SystemExit) as exit_info:
        b.main(["--version"])
    assert exit_info.value.code == 0 and b.VERSION in capsys.readouterr().out


def test_far_reset_is_capped():
    win = {"kind": "five_hour", "percentUsed": 10, "resetsAt": "9999-01-01T00:00:00Z"}
    assert b.window_values(win, 0) == (900, b.R_MAX)
