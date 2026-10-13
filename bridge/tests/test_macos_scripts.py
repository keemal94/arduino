"""The macOS scripts in bridge/macos. The file checks run everywhere, since the scripts must
survive a Windows checkout. The behaviour tests run on macOS with launchctl replaced and pgrep
fenced in, so they never touch the real launchd agent or a bridge that is running. Making the
Python environment downloads pyserial, so they start with one made already."""

import filecmp
import os
import plistlib
import shutil
import signal
import stat
import subprocess
import sys
import time

import pytest

BRIDGE_DIR = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
ROOT = os.path.dirname(BRIDGE_DIR)
MACOS = os.path.join(BRIDGE_DIR, "macos")
COMMANDS = ["install-autostart.command", "start-bridge.command", "stop-bridge.command",
            "uninstall-autostart.command"]
LABEL = "io.github.keemal94.usage-lcd"


# --- the files ---------------------------------------------------------------------

@pytest.mark.parametrize("name", COMMANDS + ["common.sh"])
def test_lf_and_macos_bash(name):
    with open(os.path.join(MACOS, name), "rb") as f:
        data = f.read()
    assert b"\r" not in data
    if name.endswith(".command"):
        assert data.startswith(b"#!/bin/bash\n")  # the bash 3.2 macOS ships, not a newer one on PATH


def test_commands_are_executable_in_git():
    git = shutil.which("git")
    listed = git and subprocess.run([git, "ls-files", "-s", "--", "bridge/macos"], cwd=ROOT,
                                    capture_output=True, encoding="utf-8")
    if not listed or listed.returncode != 0 or not listed.stdout:
        pytest.skip("not a git checkout")
    modes = {line.split("\t")[1].rsplit("/", 1)[1]: line.split()[0] for line in listed.stdout.splitlines()}
    # Finder runs a .command only if it is executable; a ZIP from GitHub keeps git's mode.
    assert modes == dict({name: "100755" for name in COMMANDS}, **{"common.sh": "100644"})


@pytest.mark.skipif(not os.path.exists("/bin/bash"), reason="needs /bin/bash")
@pytest.mark.parametrize("name", COMMANDS + ["common.sh"])
def test_bash_syntax(name):
    run = subprocess.run(["/bin/bash", "-n", os.path.join(MACOS, name)], capture_output=True, encoding="utf-8")
    assert run.returncode == 0, run.stderr


def test_shellcheck():
    shellcheck = shutil.which("shellcheck") or shutil.which("shellcheck", path=os.path.dirname(sys.executable))
    if not shellcheck:
        pytest.skip("shellcheck is not installed")
    run = subprocess.run([shellcheck, "-x", "--shell=bash"] + COMMANDS + ["common.sh"], cwd=MACOS,
                         capture_output=True, encoding="utf-8")
    assert run.returncode == 0, run.stdout + run.stderr


# --- what they do, on macOS ---------------------------------------------------------

macos_only = pytest.mark.skipif(sys.platform != "darwin", reason="needs macOS (launchd, plutil, xattr)")

# Records each call; "loaded" stands for the agent being in launchd.
FAKE_LAUNCHCTL = r"""#!/bin/bash
echo "$*" >> "$FAKE_STATE/launchctl.log"
case "$1" in
    print) [ -f "$FAKE_STATE/loaded" ] ;;
    bootout) [ -f "$FAKE_STATE/loaded" ] && rm "$FAKE_STATE/loaded" ;;
    bootstrap)
        if [ -n "${FAKE_BOOTSTRAP_FAILS:-}" ]; then echo "Bootstrap failed: 5: Input/output error" >&2; exit 5; fi
        touch "$FAKE_STATE/loaded" ;;
    list)
        [ -f "$FAKE_STATE/loaded" ] || exit 113
        printf '{\n\t"Label" = "%s";\n\t"LastExitStatus" = 0;\n' "$2"
        [ -n "${FAKE_NO_PID:-}" ] || printf '\t"PID" = 4242;\n'
        printf '};\n' ;;
esac
"""
# The real pgrep, but only processes this test started can be found (and so stopped).
FAKE_PGREP = r"""#!/bin/bash
/usr/bin/pgrep "$@" | grep -Fx -f "$FAKE_STATE/pids"
"""


class Mac:
    """A home folder for the scripts, with a Python environment already made."""

    def __init__(self, tmp_path):
        self.home = tmp_path / "Some Name"  # a space, as in many user names
        self.state = tmp_path / "state"
        fakes = tmp_path / "bin"
        for d in (self.home, self.state, fakes):
            d.mkdir()
        (self.state / "pids").write_text("")
        for name, text in (("launchctl", FAKE_LAUNCHCTL), ("pgrep", FAKE_PGREP)):
            (fakes / name).write_text(text)
            (fakes / name).chmod(0o755)
        self.app = self.home / "Library" / "Application Support" / "usage-lcd"
        self.python = self.app / "venv" / "bin" / "python"
        self.python.parent.mkdir(parents=True)
        self.python.write_text('#!/bin/sh\nexec "%s" "$@"\n' % sys.executable)  # has pyserial
        self.python.chmod(0o755)
        self.logs = self.home / "Library" / "Logs" / "usage-lcd"
        self.plist = self.home / "Library" / "LaunchAgents" / (LABEL + ".plist")
        self.domain = "gui/%d" % os.getuid()
        self.env = {"HOME": str(self.home), "PATH": "%s:/usr/bin:/bin:/usr/sbin:/sbin" % fakes,
                    "FAKE_STATE": str(self.state)}
        self.spawned = []

    def run(self, script, *args, folder=MACOS, **env):
        # umask 002, as some Macs have: launchd refuses a plist the group may write
        return subprocess.run(["/bin/bash", "-c", 'umask 002; exec /bin/bash "$0" "$@"', os.path.join(folder, script)]
                              + list(args),
                              env=dict(self.env, **env), stdin=subprocess.DEVNULL, capture_output=True,
                              encoding="utf-8", timeout=120)

    def launchctl_calls(self):
        path = self.state / "launchctl.log"
        return path.read_text().splitlines() if path.exists() else []

    def spawn(self, *argv):
        """Starts argv detached (launchd reaps it, no zombie) and lets the fake pgrep see it."""
        pid = int(subprocess.run(["/bin/sh", "-c", '"$@" >/dev/null 2>&1 & echo $!', "sh"] + list(argv),
                                 capture_output=True, encoding="utf-8", check=True).stdout)
        self.spawned.append(pid)
        with open(self.state / "pids", "a") as f:
            f.write("%d\n" % pid)
        return pid

    def cleanup(self):
        for pid in self.spawned:
            if alive(pid):
                os.kill(pid, signal.SIGKILL)


def alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def gone_within(pid, seconds):
    end = time.monotonic() + seconds
    while alive(pid) and time.monotonic() < end:
        time.sleep(0.05)
    return not alive(pid)


@pytest.fixture
def mac(tmp_path):
    m = Mac(tmp_path)
    yield m
    m.cleanup()


@macos_only
def test_install_writes_the_agent_and_starts_it(mac):
    run = mac.run("install-autostart.command", "--port", "/dev/cu.usbserial-110")
    assert run.returncode == 0, run.stdout + run.stderr
    assert "The bridge is running in the background." in run.stdout

    with open(mac.plist, "rb") as f:
        agent = plistlib.load(f)
    assert agent == {
        "Label": LABEL,
        "ProgramArguments": [str(mac.python), str(mac.app / "usage_lcd_bridge.py"), "--no-console-log",
                             "--port", "/dev/cu.usbserial-110"],
        "RunAtLoad": True,
        "KeepAlive": {"SuccessfulExit": False},
        "ThrottleInterval": 30,
        "StandardOutPath": str(mac.logs / "launchd.log"),
        "StandardErrorPath": str(mac.logs / "launchd.log"),
    }
    assert stat.S_IMODE(os.stat(mac.plist).st_mode) == 0o644
    lint = subprocess.run(["plutil", "-lint", str(mac.plist)], capture_output=True, encoding="utf-8")
    assert lint.returncode == 0, lint.stdout
    # launchd runs this copy, so the project folder may be anywhere
    for name in ("usage_lcd_bridge.py", "requirements.txt"):
        assert filecmp.cmp(os.path.join(BRIDGE_DIR, name), str(mac.app / name), shallow=False)
    assert mac.logs.is_dir()  # launchd cannot start a job whose log folder is missing
    assert "bootstrap %s %s" % (mac.domain, mac.plist) in mac.launchctl_calls()


@macos_only
def test_install_again_replaces_the_running_agent(mac):
    assert mac.run("install-autostart.command").returncode == 0
    first = mac.plist.read_bytes()
    run = mac.run("install-autostart.command")
    assert run.returncode == 0, run.stdout + run.stderr
    assert mac.plist.read_bytes() == first
    calls = mac.launchctl_calls()
    bootstraps = [i for i, c in enumerate(calls) if c.startswith("bootstrap ")]
    assert len(bootstraps) == 2
    assert "bootout %s/%s" % (mac.domain, LABEL) in calls[bootstraps[0]:bootstraps[1]]


@macos_only
def test_install_reports_a_bridge_that_stops_at_once(mac):
    run = mac.run("install-autostart.command", FAKE_NO_PID="1")
    assert run.returncode == 1 and "The bridge stopped right away" in run.stdout


@macos_only
def test_install_explains_a_refused_agent(mac):
    run = mac.run("install-autostart.command", FAKE_BOOTSTRAP_FAILS="1")
    assert run.returncode == 1
    assert "Input/output error" in run.stdout and "Allow in the Background" in run.stdout


@macos_only
def test_stop_then_uninstall(mac):
    assert mac.run("install-autostart.command").returncode == 0
    bridge = mac.spawn(sys.executable, "-c", "import time; time.sleep(60)", "usage_lcd_bridge.py")
    other = mac.spawn("/bin/sh", "-c", "sleep 60; :", "usage_lcd_bridge.py")  # not a Python

    started = time.monotonic()
    run = mac.run("stop-bridge.command")
    assert run.returncode == 0 and "The bridge was stopped" in run.stdout, run.stdout
    assert time.monotonic() - started < 4  # SIGTERM did it, not the SIGKILL 5 s later
    assert gone_within(bridge, 5) and alive(other)
    assert "bootout %s/%s" % (mac.domain, LABEL) in mac.launchctl_calls()
    assert mac.plist.exists()  # autostart stays installed

    run = mac.run("uninstall-autostart.command")
    assert run.returncode == 0 and "Autostart removed: %s" % mac.plist in run.stdout, run.stdout
    assert not mac.plist.exists() and not mac.app.exists()
    run = mac.run("uninstall-autostart.command")
    assert run.returncode == 0
    assert "The bridge was not running." in run.stdout and "Autostart was not installed." in run.stdout


@macos_only
def test_start_bridge_runs_the_bridge_and_clears_quarantine(mac, tmp_path):
    # A copy of the folder as Safari leaves a download: every script quarantined.
    copy = tmp_path / "Downloads" / "bridge"
    shutil.copytree(BRIDGE_DIR, str(copy), ignore=shutil.ignore_patterns("tests", "__pycache__"))
    scripts = [str(copy / "macos" / name) for name in COMMANDS]
    subprocess.run(["xattr", "-w", "com.apple.quarantine", "0081;00000000;Safari;"] + scripts, check=True)

    run = mac.run("start-bridge.command", "--version", folder=str(copy / "macos"))
    assert run.returncode == 0 and "usage_lcd_bridge 0.1.0" in run.stdout, run.stdout + run.stderr
    left = subprocess.run(["xattr", "-p", "com.apple.quarantine"] + scripts, capture_output=True, encoding="utf-8")
    assert left.stdout == ""  # one approved script lets the others run too
