"""The macOS menu bar app's logic (bridge/macos/usage_lcd_menubar.py), without AppKit."""

import logging
import os
import sys
import threading

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "macos"))
import usage_lcd_menubar as m  # noqa: E402

b = m.bridge
NOW = b.parse_time("2026-10-12T18:03:12.500Z")
GOOD = {"schema": 1, "windows": [
    {"kind": "five_hour", "percentUsed": 23.5, "resetsAt": "2026-10-12T21:00:00.000Z"},
    {"kind": "seven_day", "percentUsed": 59, "resetsAt": "2026-10-16T09:00:00.000Z"}]}


@pytest.mark.parametrize("seconds, shown", [
    (-1, "--"), (0, "now"), (1, "1s"), (45, "45s"), (59, "59s"), (60, "1m00s"), (1385, "23m05s"),
    (3599, "59m59s"), (3600, "1h00m"), (4980, "1h23m"), (86399, "23h59m"), (86400, "1d00h"),
    (3 * 86400 + 4 * 3600, "3d04h"), (100 * 86400 - 1, "99d23h"), (100 * 86400, "99d+")])
def test_format_reset_as_the_lcd(seconds, shown):
    assert m.format_reset(seconds) == shown


def test_window_text():
    assert m.window_text("5h", 765, 9008) == "5h: 76% left, resets in 2h30m"
    assert m.window_text("5h", 0, 2708) == "5h: LIMIT, resets in 45m08s"
    assert m.window_text("7d", 1000, -1) == "7d: 100% left, reset time unknown"
    assert m.window_text("7d", 5, 0) == "7d: 0% left, resets now"
    assert m.window_text("7d", -1, -1) == "7d: no reading"


class FakeWorker:
    """Stands in for a Bridge: runs until stopped, as Bridge.run(stop) does."""

    def __init__(self):
        self.link_name, self.started, self.finished = None, threading.Event(), False

    def run(self, stop):
        self.started.set()
        stop.wait(10)
        self.finished = True


class Clock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


@pytest.fixture
def lock_port():
    s = b.acquire_lock(0)
    port = s.getsockname()[1]
    s.close()
    return port


def test_start_takes_the_lock_and_stop_frees_port_and_lock(lock_port):
    workers = []
    service = m.Service("unused", lock_port, new_bridge=lambda: workers.append(FakeWorker()) or workers[-1])
    service.start()
    try:
        assert service.running and workers[0].started.wait(5)
        with pytest.raises(OSError):
            b.acquire_lock(lock_port)  # a second bridge, e.g. start-bridge.command, exits 3
        workers[0].link_name = "/dev/cu.usbserial-110"
        assert service.link_name == "/dev/cu.usbserial-110"
    finally:
        service.stop()
    assert workers[0].finished and not service.running and service.link_name is None
    b.acquire_lock(lock_port).close()  # free again
    service.start()
    service.stop()
    assert len(workers) == 2


def test_waits_for_another_bridge_then_takes_over(lock_port, caplog):
    caplog.set_level(logging.INFO, logger="usage_lcd_bridge")
    clock = Clock()
    other = b.acquire_lock(lock_port)
    service = m.Service("unused", lock_port, new_bridge=FakeWorker, clock=clock)
    try:
        service.start()
        assert service.busy and not service.running and service.wanted
        clock.t += 4.9
        service.tick()
        assert not service.running
        other.close()
        clock.t += 0.1
        service.tick()
        assert service.running and not service.busy
    finally:
        other.close()
        service.stop()
    assert sum("another bridge is already running" in r.message for r in caplog.records) == 1


def test_a_stopped_service_does_not_start_by_itself(lock_port):
    clock = Clock()
    service = m.Service("unused", lock_port, new_bridge=FakeWorker, clock=clock)
    service.start()
    service.stop()
    clock.t += 60
    service.tick()
    assert not service.running and not service.wanted


class FakeService:
    def __init__(self, running=False, busy=False, link_name=None):
        self.running, self.busy, self.link_name = running, busy, link_name


def test_menu_while_connected():
    state = m.menu_state(FakeService(running=True, link_name="/dev/cu.usbserial-110"), GOOD, NOW)
    assert state == {"title": "76%", "dimmed": False, "status": "LCD: connected on /dev/cu.usbserial-110",
                     "windows": ["5h: 76% left, resets in 2h56m", "7d: 41% left, resets in 3d14h"]}


def test_menu_while_stopped_still_shows_the_usage():
    state = m.menu_state(FakeService(), GOOD, NOW)
    assert state["title"] == "76%" and state["dimmed"] and state["status"] == "LCD: stopped, the serial port is free"


def test_menu_states_without_data():
    looking = m.menu_state(FakeService(running=True), None, NOW)
    assert looking["title"] == "--%" and not looking["dimmed"]
    assert looking["status"] == "LCD: looking for the Arduino…"
    assert looking["windows"][0].startswith("No usage data yet")
    busy = m.menu_state(FakeService(busy=True), {"schema": 1, "windows": GOOD["windows"][1:]}, NOW)
    assert busy["title"] == "--%" and busy["status"].startswith("LCD: another bridge is running")
    assert busy["windows"] == ["5h: no reading", "7d: 41% left, resets in 3d14h"]


@pytest.mark.skipif(sys.platform == "win32", reason="flock")
def test_single_instance(tmp_path):
    path = str(tmp_path / "usage-lcd" / "menubar.lock")
    first = m.single_instance(path)
    assert first is not None and m.single_instance(path) is None
    first.close()
    again = m.single_instance(path)
    assert again is not None
    again.close()
