"""End to end: snapshot files as the mod writes them, through the real bridge code, to the
firmware's display code (lcdsim, built by firmware/host_test/build.sh). docs/PROTOCOL.md
is the contract. The offline chain needs sh and g++; the live serial chain also needs a
POSIX pty and pyserial."""

import json
import os
import pathlib
import re
import select
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests" / "fixtures"
BRIDGE = ROOT / "bridge" / "usage_lcd_bridge.py"
sys.path.insert(0, str(BRIDGE.parent))
import usage_lcd_bridge as bridge  # noqa: E402

BORDER = "+----------------+"
NOW = bridge.parse_time("2026-10-12T18:29:52Z")

# Per fixture, at NOW: the bridge's line, then the bars page and, 8 s later, the resets page.
OFFLINE = {
    "normal": ("U|765|9008|410|311408",
               ("5h ██████▊░░ 76%", "7d ███▋░░░░░ 41%"),
               ("5h resets  2h30m", "7d resets  3d14h")),
    "one-window": ("U|765|9008|-1|-1",
                   ("5h ██████▊░░ 76%", "7d ░░░░░░░░░ --%"),
                   ("5h resets  2h30m", "7d resets     --")),
    "exhausted-five-hour": ("U|0|2708|77|41408",
                            ("5h LIMIT  45m08s", "7d!▋░░░░░░░░  7%"),
                            ("5h LIMIT  45m00s", "7d resets 11h30m")),
    "reset-passed": ("U|1000|-1|388|311408",
                     ("5h █████████100%", "7d ███▍░░░░░ 38%"),
                     ("5h resets     --", "7d resets  3d14h")),
    "spend-limit-only": ("W",
                         ("Claude usage    ", "waiting for data"),
                         ("Claude usage    ", "waiting for data")),
}


@pytest.fixture(scope="session")
def lcdsim(tmp_path_factory):
    if not (shutil.which("sh") and shutil.which(os.environ.get("CXX", "g++"))):
        pytest.skip("building lcdsim needs sh and g++")
    out = tmp_path_factory.mktemp("lcdsim")
    build = subprocess.run(["sh", str(ROOT / "firmware" / "host_test" / "build.sh")],
                           env=dict(os.environ, BUILD_DIR=str(out)), capture_output=True, text=True)
    assert build.returncode == 0, build.stdout + build.stderr
    return str(out / "lcdsim")


def run_lcdsim(lcdsim, commands):
    """(lines the display sent, frames shown) for lcdsim's stdin commands."""
    out = subprocess.run([lcdsim], input="".join(c + "\n" for c in commands), capture_output=True,
                         encoding="utf-8", check=True).stdout.splitlines()
    sent = [line[3:] for line in out if line.startswith("TX ")]
    rows = [line for line in out if line != BORDER and not line.startswith("TX ")]
    return sent, [tuple(rows[i:i + 2]) for i in range(0, len(rows), 2)]


# --- the fixtures are what the mod writes ---------------------------------------

def test_mod_tests_expect_the_normal_fixture():
    source = (ROOT / "mod" / "tests" / "usage-lcd.test.ts").read_text(encoding="utf-8")
    literal = re.search(r"const NORMAL_JSON = `(.*?)`", source, re.S)
    assert literal, "NORMAL_JSON not found in the mod's tests"
    # A template literal reads CRLF as LF.
    assert literal.group(1).replace("\r\n", "\n") == (FIXTURES / "normal.json").read_bytes().decode("utf-8")


def test_fixtures_are_byte_for_byte_what_the_mod_writes():
    if not shutil.which("node"):
        pytest.skip("needs node to run the mod's snapshot code")
    script = """
import { readFileSync } from 'node:fs'
import { buildSnapshot, serialize, toWindows } from %s
for (const path of process.argv.slice(1)) {
  const text = readFileSync(path, 'utf8')
  const { writtenAt, windows } = JSON.parse(text)
  if (serialize(buildSnapshot(toWindows(windows), Date.parse(writtenAt))) !== text) console.log('differs: ' + path)
}
""" % json.dumps((ROOT / "mod" / "hooks" / "snapshot.ts").as_uri())
    paths = sorted(str(p) for p in FIXTURES.glob("*.json"))
    assert len(paths) == len(OFFLINE)
    run = subprocess.run(["node", "--input-type=module", "-e", script] + paths, capture_output=True, text=True)
    if "ERR_UNKNOWN_FILE_EXTENSION" in run.stderr:
        pytest.skip("this node cannot import TypeScript (needs 22.18 or newer)")
    assert run.returncode == 0 and run.stdout == "", run.stdout + run.stderr


# Environments as Windows gives them; the first is the desktop app's default.
ENVS = [
    {"USERPROFILE": "C:\\Users\\Some Name"},
    {"USERPROFILE": "C:\\Users\\Yükselen\\", "HOME": "C:\\Users\\Yükselen\\git-home"},
    {"USERPROFILE": "C:\\Users\\Some Name", "CLAUDE_CONFIG_DIR": "D:\\claude config\\"},
    {"USERPROFILE": "C:\\Users\\Some Name", "CLAUDE_CONFIG_DIR": "C:/Users/Some Name/.claude-work"},
    {"USERPROFILE": "C:\\Users\\Some Name", "CLAUDE_CONFIG_DIR": "", "USAGE_LCD_FILE": ""},
    {"USERPROFILE": "C:\\Users\\Some Name", "CLAUDE_CONFIG_DIR": "D:\\cfg", "USAGE_LCD_FILE": "E:\\lcd\\u.json"},
    {"USERPROFILE": "", "HOME": "/home/some one"},
    {},
]


def test_mod_writes_where_the_bridge_reads():
    if not shutil.which("node"):
        pytest.skip("needs node to run the mod's snapshot code")
    script = """
import { resolvePath } from %s
for (const env of JSON.parse(process.argv[1])) console.log(JSON.stringify(resolvePath(env) ?? null))
""" % json.dumps((ROOT / "mod" / "hooks" / "snapshot.ts").as_uri())
    run = subprocess.run(["node", "--input-type=module", "-e", script, json.dumps(ENVS)],
                         capture_output=True, encoding="utf-8")
    if "ERR_UNKNOWN_FILE_EXTENSION" in run.stderr:
        pytest.skip("this node cannot import TypeScript (needs 22.18 or newer)")
    assert run.returncode == 0, run.stderr
    mod = [json.loads(line) for line in run.stdout.splitlines()]
    assert mod == [bridge.snapshot_path(env) for env in ENVS]
    assert mod[0] == "C:\\Users\\Some Name\\.claude\\usage-lcd.json"


# --- (a) offline chain: fixture -> bridge mapping -> display ---------------------

@pytest.mark.parametrize("name", sorted(OFFLINE))
def test_offline_chain(lcdsim, name):
    line, bars, resets = OFFLINE[name]
    reader = bridge.SnapshotReader(str(FIXTURES / (name + ".json")))
    reader.poll()
    assert bridge.snapshot_to_line(reader.snapshot, NOW) == line
    # 8 s later the resets page shows: first as the Arduino counted down alone, then
    # after the line the bridge computes for that moment. Both must agree.
    sent, frames = run_lcdsim(lcdsim, ["@1000", line, "#show", "@9000", "#show",
                                       bridge.snapshot_to_line(reader.snapshot, NOW + 8), "#show"])
    assert sent == []  # no E|bad-line
    assert frames == [bars, resets, resets]


# --- (b) live chain: the real bridge process on a pty --------------------------

class FakeArduino:
    """The sketch's part on a pty: lines from the bridge go to lcdsim at a clock that
    follows real time, lcdsim's replies go back; `screen` is what the LCD shows."""

    def __init__(self, lcdsim):
        import tty
        self.master, self.slave = os.openpty()  # holding the slave open keeps the master readable
        tty.setraw(self.slave)
        self.port = os.ttyname(self.slave)
        self.sim = subprocess.Popen([lcdsim], stdin=subprocess.PIPE, stdout=subprocess.PIPE, encoding="utf-8")
        self.t0 = time.monotonic()
        self.received, self.screen, self.error = [], None, None
        self.stopping = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _run(self):
        buf = b""
        try:
            while not self.stopping.is_set():
                lines = []
                if select.select([self.master], [], [], 0.1)[0]:
                    *raw, buf = (buf + os.read(self.master, 1024)).split(b"\n")
                    lines = [line.decode("ascii") for line in raw]
                    self.received += lines
                self._show(lines)
        except Exception as e:  # reported by wait_for
            self.error = e

    def _show(self, lines):
        ms = int((time.monotonic() - self.t0) * 1000)
        self.sim.stdin.write("".join(c + "\n" for c in ["@%d" % ms] + lines + ["#show"]))
        self.sim.stdin.flush()
        rows = []
        while True:
            out = self.sim.stdout.readline()
            if not out:
                raise RuntimeError("lcdsim exited")
            out = out.rstrip("\n")
            if out.startswith("TX "):
                os.write(self.master, out[3:].encode("ascii") + b"\n")
            elif out != BORDER:
                rows.append(out)
            elif len(rows) == 2:
                break
        self.screen = tuple(rows)

    def wait_for(self, check, seconds, what):
        end = time.monotonic() + seconds
        while time.monotonic() < end and self.error is None:
            if check():
                return
            time.sleep(0.05)
        pytest.fail("%s within %.1f s; error %r, screen %r, received %r"
                    % (what, seconds, self.error, self.screen, self.received[-5:]))

    def close(self):
        self.stopping.set()
        self.thread.join(2)
        self.sim.kill()
        self.sim.wait()
        os.close(self.master)
        os.close(self.slave)


def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_live_serial_chain(lcdsim, tmp_path):
    if not hasattr(os, "openpty") or sys.platform == "win32":
        pytest.skip("needs a POSIX pty")
    pytest.importorskip("serial", reason="the bridge needs pyserial")
    start = time.time()
    normal = json.loads((FIXTURES / "normal.json").read_text(encoding="utf-8"))

    def snapshot(p5, p7):
        # resetsAt 59 s past whole minutes, so the shown times hold for the test's length
        normal["windows"][0].update(percentUsed=p5, resetsAt=iso(start + 2 * 3600 + 30 * 60 + 59))
        normal["windows"][1].update(percentUsed=p7, resetsAt=iso(start + 3 * 86400 + 14 * 3600 + 30 * 60 + 59))
        return json.dumps(normal, indent=2) + "\n"

    snap = tmp_path / "Some Name" / "usage-lcd.json"
    snap.parent.mkdir()
    snap.write_text(snapshot(23.5, 59), encoding="utf-8")
    fake = FakeArduino(lcdsim)
    env = dict(os.environ, LOCALAPPDATA=str(tmp_path), USAGE_LCD_LOCK_PORT=str(free_port()))
    proc = subprocess.Popen([sys.executable, str(BRIDGE), "--port", fake.port, "--file", str(snap)],
                            env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    log = tmp_path / "usage-lcd" / "bridge.log"
    try:
        # Handshake: no hello after opening (a pty does not reset the board), so the bridge
        # asks with "?" and the display code answers.
        fake.wait_for(lambda: fake.screen == ("5h ██████▊░░ 76%", "7d ███▋░░░░░ 41%"), 10, "first bars")
        assert fake.received[0] == "?" and re.fullmatch(r"U\|765\|\d+\|410\|\d+", fake.received[1])
        assert "connected to the usage LCD on " + fake.port in log.read_text(encoding="utf-8")

        snap.write_text(snapshot(61.5, 70.2), encoding="utf-8")
        fake.wait_for(lambda: fake.screen == ("5h ███▍░░░░░ 38%", "7d ██▋░░░░░░ 29%"), 3, "updated bars")
        resets = ("5h resets  2h30m", "7d resets  3d14h")

        # Garbage keeps the last good snapshot: same numbers, no W.
        seen = len(fake.received)
        snap.write_text("garbage, not a snapshot {", encoding="utf-8")
        time.sleep(2.5)
        assert fake.screen in (("5h ███▍░░░░░ 38%", "7d ██▋░░░░░░ 29%"), resets)
        after = fake.received[seen:]
        assert after and all(re.fullmatch(r"U\|385\|\d+\|298\|\d+", line) for line in after), after

        # The resets page comes 8 s after the first U line.
        fake.wait_for(lambda: fake.screen == resets, 9, "resets page")

        snap.write_text(json.dumps(dict(normal, schema=2)), encoding="utf-8")
        fake.wait_for(lambda: fake.screen == ("Claude usage    ", "waiting for data"), 3, "waiting for data")

        proc.send_signal(signal.SIGINT)  # Ctrl+C
        assert proc.wait(5) == 0
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
        fake.close()


# --- (c) one bridge at a time, as real processes --------------------------------

def wait_for_log(log, text, count=1, seconds=10):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if log.exists() and log.read_text(encoding="utf-8").count(text) >= count:
            return
        time.sleep(0.05)
    pytest.fail("%r (x%d) not logged within %d s: %r"
                % (text, count, seconds, log.read_text(encoding="utf-8") if log.exists() else None))


def test_only_one_bridge_runs(tmp_path):
    # The lock is a bound TCP port on 127.0.0.1: while one bridge runs a second exits 3, and
    # once it has stopped another can start. SIGTERM (launchctl bootout, stop-bridge.command)
    # stops a bridge as Ctrl+C does.
    if sys.platform == "win32":
        pytest.skip("sends SIGTERM")
    pytest.importorskip("serial", reason="the bridge needs pyserial")
    env = dict(os.environ, HOME=str(tmp_path), LOCALAPPDATA=str(tmp_path), USAGE_LCD_LOCK_PORT=str(free_port()))
    missing = tmp_path / "no-such-port"
    cmd = [sys.executable, str(BRIDGE), "--port", str(missing), "--file", str(tmp_path / "usage-lcd.json")]
    log = tmp_path / "usage-lcd" / "bridge.log"
    running = "%s: cannot open" % missing  # logged from the bridge's loop, after it took the lock
    procs = []

    def start():
        procs.append(subprocess.Popen(cmd, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
        return procs[-1]

    try:
        first = start()
        wait_for_log(log, running)
        second = subprocess.run(cmd, env=env, capture_output=True, encoding="utf-8", timeout=10)
        assert second.returncode == 3 and "another bridge is already running" in second.stderr, second.stderr
        assert first.poll() is None

        first.send_signal(signal.SIGTERM)
        assert first.wait(5) == 0
        wait_for_log(log, "INFO stopped")

        third = start()
        wait_for_log(log, running, count=2)
        third.send_signal(signal.SIGTERM)
        assert third.wait(5) == 0
    finally:
        for proc in procs:
            if proc.poll() is None:
                proc.kill()
                proc.wait()
