import json
import logging
import os
import socket
import sys
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
    assert b.parse_time("2026-10-12T18:03:12") == utc
    assert b.parse_time("2026-10-12T18:03:12.250z") == utc + 0.25
    assert b.parse_time("2026-13-12T18:03:12Z") is None
    assert b.parse_time("") is None


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
        self.written, self.closed, self.fail = [], False, None

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
        if self.fail:
            raise self.fail
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


def test_vanished_port_logged():
    clock = Clock()
    gone = serial.SerialException("could not open port 'COM3': FileNotFoundError(2, 'not found', None, 2)")
    bridge, _ = make_bridge(clock, {"COM3": [gone]})
    bridge.step()
    assert bridge.link is None and "COM3: cannot open" in str(bridge.reported)


@pytest.mark.parametrize("error", [serial.SerialException("WriteFile failed"),
                                   serial.SerialTimeoutException("Write timeout"),
                                   PermissionError(13, "The device does not recognize the command")])
def test_write_failure_reconnects(error, caplog):
    clock = Clock()
    first, second = FakeSerial(clock, hello_at=1.0), FakeSerial(clock, hello_at=1.0)
    bridge, opened = make_bridge(clock, {"COM3": [first, second]})
    bridge.step()
    assert bridge.link is first
    first.fail = error
    clock.advance(10)
    bridge.step()
    assert bridge.link is None and first.closed and "connection lost" in caplog.text
    clock.advance(4)
    bridge.step()
    assert opened == ["COM3"]
    clock.advance(1)
    bridge.step()
    assert bridge.link is second and lines(second) == [GOOD_LINE + "\n"]


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


def test_line_buffer():
    buf = b.LineBuffer()
    assert buf.feed(b"R|usage-") == []
    assert buf.feed(b"lcd|1\r\nE|bad") == ["R|usage-lcd|1"]
    assert buf.feed(b"-line\n") == ["E|bad-line"]
    assert buf.feed(b"\x00\xf0R|usage-lcd|1\r\n") == ["R|usage-lcd|1"]  # reset glitch byte
    assert buf.feed(b"x" * 300) == [] and buf.buf == b""


def test_single_instance_lock():
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()
    first = b.acquire_lock(port)
    assert first is not None
    try:
        assert b.acquire_lock(port) is None
    finally:
        first.close()


def test_version(capsys):
    with pytest.raises(SystemExit) as exit_info:
        b.main(["--version"])
    assert exit_info.value.code == 0 and b.VERSION in capsys.readouterr().out
