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

@pytest.mark.parametrize("name", COMMANDS + ["common.sh", "usage_lcd_menubar.py", "requirements.txt"])
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
    assert modes == dict({name: "100755" for name in COMMANDS},
                         **{name: "100644" for name in ("common.sh", "usage_lcd_menubar.py", "requirements.txt")})


def test_scripts_stay_lf_on_any_checkout():
    git = shutil.which("git")
    names = ["bridge/macos/%s" % name for name in COMMANDS + ["common.sh"]] + ["firmware/host_test/build.sh"]
    attrs = git and subprocess.run([git, "check-attr", "eol", "--"] + names, cwd=ROOT, capture_output=True,
                                   encoding="utf-8")
    if not attrs or attrs.returncode != 0:
        pytest.skip("not a git checkout")
    assert attrs.stdout.splitlines() == ["%s: eol: lf" % name for name in names]  # .gitattributes


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

# Records each call, every argument ended by \x1f so that a quoting mistake shows. "loaded"
# stands for the agent being in launchd. As the real one can, bootout returns while the job
# is still going away: it stays loaded for two more prints, and a bootstrap meanwhile fails.
FAKE_LAUNCHCTL = r"""#!/bin/bash
printf '%s\037' "$@" >> "$FAKE_STATE/launchctl.log"; echo >> "$FAKE_STATE/launchctl.log"
case "$1" in
    print)
        if [ -f "$FAKE_STATE/unloading" ]; then
            left=$(cat "$FAKE_STATE/unloading")
            if [ "$left" -le 1 ]; then rm "$FAKE_STATE/unloading" "$FAKE_STATE/loaded"; else echo $((left - 1)) > "$FAKE_STATE/unloading"; fi
        fi
        [ -f "$FAKE_STATE/loaded" ] ;;
    bootout) [ -f "$FAKE_STATE/loaded" ] && echo 2 > "$FAKE_STATE/unloading" ;;
    bootstrap)
        if [ -n "${FAKE_BOOTSTRAP_FAILS:-}" ] || [ -f "$FAKE_STATE/loaded" ]; then
            echo "Bootstrap failed: 5: Input/output error" >&2; exit 5
        fi
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
# The venv's python: the test's own (it has pyserial and PyObjC); pip is only recorded.
VENV_PYTHON = """#!/bin/sh
if [ "$1" = -I ] && [ "$2" = -m ] && [ "$3" = pip ]; then echo "$*" >> "$FAKE_STATE/pip.log"; exit 0; fi
exec "%s" "$@"
""" % sys.executable
# A Python to make venvs with: `-m venv DIR` gives DIR the venv python above.
BASE_PYTHON = """#!/bin/sh
if [ "$1" = -m ] && [ "$2" = venv ]; then
    echo "$3" >> "$FAKE_STATE/venv.log"
    mkdir -p "$3/bin" && cp "$FAKE_STATE/venv-python" "$3/bin/python" && exit 0
fi
exec "%s" "$@"
""" % sys.executable


def write_script(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    path.chmod(0o755)


class Mac:
    """A home folder for the scripts, with a Python environment already made."""

    def __init__(self, tmp_path):
        self.home = tmp_path / "Some Name"  # a space, as in many user names
        self.state = tmp_path / "state"
        fakes = tmp_path / "bin"
        for d in (self.home, self.state, fakes):
            d.mkdir()
        (self.state / "pids").write_text("")
        write_script(fakes / "launchctl", FAKE_LAUNCHCTL)
        write_script(fakes / "pgrep", FAKE_PGREP)
        write_script(self.state / "venv-python", VENV_PYTHON)
        self.base_python = self.state / "python3"
        write_script(self.base_python, BASE_PYTHON)
        self.app = self.home / "Library" / "Application Support" / "usage-lcd"
        self.python = self.app / "venv" / "bin" / "python"
        write_script(self.python, VENV_PYTHON)
        self.bundle = self.home / "Applications" / "Claude Usage LCD.app"
        self.program = self.bundle / "Contents" / "MacOS" / "usage-lcd"
        self.logs = self.home / "Library" / "Logs" / "usage-lcd"
        self.plist = self.home / "Library" / "LaunchAgents" / (LABEL + ".plist")
        self.domain = "gui/%d" % os.getuid()
        self.env = {"HOME": str(self.home), "PATH": "%s:/usr/bin:/bin:/usr/sbin:/sbin" % fakes,
                    "FAKE_STATE": str(self.state)}
        self.spawned = []

    def options(self):
        with open(self.plist, "rb") as f:
            return plistlib.load(f)["ProgramArguments"][1:]

    def log(self, name):
        path = self.state / name
        return path.read_text().splitlines() if path.exists() else []

    def run(self, script, *args, folder=MACOS, **env):
        # umask 002, as some Macs have: launchd refuses a plist the group may write
        return subprocess.run(["/bin/bash", "-c", 'umask 002; exec /bin/bash "$0" "$@"', os.path.join(folder, script)]
                              + list(args),
                              env=dict(self.env, **env), stdin=subprocess.DEVNULL, capture_output=True,
                              encoding="utf-8", timeout=120)

    def launchctl_calls(self):
        return [line.rstrip("\x1f").split("\x1f") for line in self.log("launchctl.log")]

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
    assert "Claude Usage LCD is running: its icon is in the menu bar" in run.stdout

    with open(mac.plist, "rb") as f:
        agent = plistlib.load(f)
    assert agent == {
        "Label": LABEL,
        "ProgramArguments": [str(mac.program), "--port", "/dev/cu.usbserial-110"],
        "RunAtLoad": True,
        "KeepAlive": {"SuccessfulExit": False},
        "ThrottleInterval": 30,
        "LimitLoadToSessionType": "Aqua",
        "ProcessType": "Interactive",
        "StandardOutPath": str(mac.logs / "launchd.log"),
        "StandardErrorPath": str(mac.logs / "launchd.log"),
    }
    assert stat.S_IMODE(os.stat(mac.plist).st_mode) == 0o644
    lint = subprocess.run(["plutil", "-lint", str(mac.plist)], capture_output=True, encoding="utf-8")
    assert lint.returncode == 0, lint.stdout
    # the app runs this copy, so the project folder may be anywhere
    for source in ("usage_lcd_bridge.py", os.path.join("macos", "usage_lcd_menubar.py")):
        assert filecmp.cmp(os.path.join(BRIDGE_DIR, source), str(mac.app / os.path.basename(source)), shallow=False)
    assert mac.logs.is_dir()  # launchd cannot start a job whose log folder is missing
    assert ["bootstrap", mac.domain, str(mac.plist)] in mac.launchctl_calls()
    assert mac.log("pip.log") == ["-I -m pip install --disable-pip-version-check --quiet -r %s"
                                  % os.path.join(MACOS, "requirements.txt")]


@macos_only
def test_install_makes_the_menu_bar_app(mac):
    assert mac.run("install-autostart.command").returncode == 0
    with open(mac.bundle / "Contents" / "Info.plist", "rb") as f:
        info = plistlib.load(f)
    assert info["CFBundleExecutable"] == "usage-lcd" and info["CFBundleIdentifier"] == LABEL
    assert info["LSUIElement"] is True and info["CFBundleDisplayName"] == "Claude Usage LCD"
    assert (mac.bundle / "Contents" / "Resources" / "AppIcon.icns").stat().st_size > 1000
    assert os.access(str(mac.program), os.X_OK)
    # Opened from Finder, the app runs the copy with the Python environment.
    run = subprocess.run([str(mac.program), "--help"], env=mac.env, capture_output=True, encoding="utf-8",
                         timeout=60)
    assert run.returncode == 0 and "menu bar app" in run.stdout, run.stdout + run.stderr


@macos_only
def test_install_stops_a_bridge_started_by_hand(mac):
    hand = mac.spawn(sys.executable, "-c", "import time; time.sleep(60)", "usage_lcd_bridge.py")
    run = mac.run("install-autostart.command")
    assert run.returncode == 0, run.stdout + run.stderr
    assert gone_within(hand, 5)  # it would hold the lock the app needs


@macos_only
def test_install_again_keeps_the_options_until_uninstalled(mac):
    assert mac.run("install-autostart.command", "--port", "/dev/cu.usbserial-110").returncode == 0
    run = mac.run("install-autostart.command")  # a double-click, e.g. after updating the folder
    assert run.returncode == 0 and "Keeping the options given before: --port /dev/cu.usbserial-110" in run.stdout
    assert mac.options() == ["--port", "/dev/cu.usbserial-110"]
    assert mac.run("install-autostart.command", "--file", "/x y/u.json").returncode == 0
    assert mac.options() == ["--file", "/x y/u.json"]
    write_script(mac.python, VENV_PYTHON)  # uninstall removed it with the rest
    assert mac.run("uninstall-autostart.command").returncode == 0
    write_script(mac.python, VENV_PYTHON)
    assert mac.run("install-autostart.command").returncode == 0
    assert mac.options() == []


@macos_only
@pytest.mark.parametrize("venv", ["missing", "broken"])
def test_makes_the_python_environment(mac, venv):
    if venv == "missing":
        shutil.rmtree(str(mac.app))
    else:
        write_script(mac.python, "#!/bin/sh\nexit 1\n")  # e.g. Homebrew removed its Python
    run = mac.run("start-bridge.command", "--version", USAGE_LCD_PYTHON=str(mac.base_python))
    assert run.returncode == 0 and "usage_lcd_bridge 0.1.0" in run.stdout, run.stdout + run.stderr
    assert "Setting up the bridge's Python environment with %s" % mac.base_python in run.stdout
    assert mac.log("venv.log") == [str(mac.app / "venv")] and len(mac.log("pip.log")) == 1


@macos_only
def test_a_serial_py_in_the_working_folder_is_not_pyserial(mac):
    # Finder starts a .command in the home folder; a serial.py there must not look like a broken venv.
    (mac.home / "serial.py").write_text("raise SystemExit('not pyserial')\n")
    run = subprocess.run(["/bin/bash", os.path.join(MACOS, "start-bridge.command"), "--version"], cwd=str(mac.home),
                         env=dict(mac.env, USAGE_LCD_PYTHON=str(mac.base_python)), stdin=subprocess.DEVNULL,
                         capture_output=True, encoding="utf-8", timeout=120)
    assert run.returncode == 0 and "Setting up" not in run.stdout and mac.log("venv.log") == []


@macos_only
def test_install_again_replaces_the_running_agent(mac):
    assert mac.run("install-autostart.command").returncode == 0
    first = mac.plist.read_bytes()
    run = mac.run("install-autostart.command")
    assert run.returncode == 0, run.stdout + run.stderr
    assert mac.plist.read_bytes() == first
    calls = mac.launchctl_calls()
    bootstraps = [i for i, c in enumerate(calls) if c[0] == "bootstrap"]
    assert len(bootstraps) == 2
    between = calls[bootstraps[0]:bootstraps[1]]
    bootout = between.index(["bootout", "%s/%s" % (mac.domain, LABEL)])
    # bootout returned before the old job was gone: it waited for it, else the bootstrap fails
    assert sum(c[0] == "print" for c in between[bootout:]) >= 2


@macos_only
def test_install_reports_a_bridge_that_stops_at_once(mac, monkeypatch):
    # The bridge's own log is where the scripts say: what it wrote is shown.
    import usage_lcd_bridge
    monkeypatch.setenv("HOME", str(mac.home))
    bridge_log = mac.logs / "bridge.log"
    assert usage_lcd_bridge.log_file({}, "darwin") == str(bridge_log)
    mac.logs.mkdir(parents=True)
    bridge_log.write_text("ERROR why it stopped\n")
    run = mac.run("install-autostart.command", FAKE_NO_PID="1")
    assert run.returncode == 1 and "The app stopped right away" in run.stdout
    assert "--- end of %s\nERROR why it stopped" % bridge_log in run.stdout


@macos_only
def test_install_explains_a_refused_agent(mac):
    run = mac.run("install-autostart.command", FAKE_BOOTSTRAP_FAILS="1")
    assert run.returncode == 1
    assert "Input/output error" in run.stdout and "Allow in the Background" in run.stdout


@macos_only
def test_stop_then_uninstall(mac):
    assert mac.run("install-autostart.command").returncode == 0
    bridge = mac.spawn(sys.executable, "-c", "import time; time.sleep(60)", "usage_lcd_bridge.py")
    app = mac.spawn(sys.executable, "-c", "import time; time.sleep(60)", "usage_lcd_menubar.py")
    other = mac.spawn("/bin/sh", "-c", "sleep 60; :", "usage_lcd_bridge.py")  # not a Python

    started = time.monotonic()
    run = mac.run("stop-bridge.command")
    assert run.returncode == 0 and "The bridge was stopped" in run.stdout, run.stdout
    assert time.monotonic() - started < 4  # SIGTERM did it, not the SIGKILL 5 s later
    assert gone_within(bridge, 5) and gone_within(app, 5) and alive(other)
    assert ["bootout", "%s/%s" % (mac.domain, LABEL)] in mac.launchctl_calls()
    assert "To start it again: open Claude Usage LCD in your Applications folder" in run.stdout
    assert mac.plist.exists()  # autostart stays installed

    run = mac.run("uninstall-autostart.command")
    assert run.returncode == 0 and "Autostart removed: %s" % mac.plist in run.stdout, run.stdout
    assert "App removed: %s" % mac.bundle in run.stdout
    assert not mac.plist.exists() and not mac.app.exists() and not mac.bundle.exists()
    assert "To start it again" not in run.stdout
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
