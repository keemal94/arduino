#!/usr/bin/env python3
"""Claude Usage LCD, the macOS menu bar app: runs the bridge (usage_lcd_bridge.py) on a thread
and shows the 5-hour window's percent left in the menu bar. Its menu tells what the LCD gets,
stops and starts the bridge (a stopped bridge frees the serial port for the Arduino IDE) and
quits. install-autostart.command makes "Claude Usage LCD.app" and starts it at login.
Needs pyserial and PyObjC (pyobjc-framework-Cocoa). Everything above run_app() works without
PyObjC, so the tests run anywhere."""

import argparse
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [HERE, os.path.dirname(HERE)]  # installed side by side, or in the repository
import usage_lcd_bridge as bridge  # noqa: E402

log = bridge.log
APP_NAME = "Claude Usage LCD"
LABELS = (("5h", 1), ("7d", 3))  # window label, index of its l in "U|l5|r5|l7|r7"


def format_reset(s):
    """Seconds until a reset as the LCD's resets page shows them (docs/PROTOCOL.md 3)."""
    if s < 0:
        return "--"
    if s == 0:
        return "now"
    if s < 60:
        return "%ds" % s
    if s < 3600:
        return "%dm%02ds" % (s // 60, s % 60)
    if s < 86400:
        return "%dh%02dm" % (s // 3600, s % 3600 // 60)
    if s >= 100 * 86400:
        return "99d+"
    return "%dd%02dh" % (s // 86400, s % 86400 // 3600)


def window_text(label, left, reset):
    """One menu line for a window, from its l and r values."""
    if left < 0:
        return "%s: no reading" % label
    amount = "LIMIT" if left == 0 else "%d%% left" % (left // 10)
    when = format_reset(reset)
    when = {"--": "reset time unknown", "now": "resets now"}.get(when, "resets in " + when)
    return "%s: %s, %s" % (label, amount, when)


class Service:
    """The bridge on a thread that the menu starts and stops. Starting takes the single-instance
    lock first; while another bridge holds it (start-bridge.command, say), it tries again every
    5 s, so it takes over once that one stops."""

    def __init__(self, path, lock_port, port=None, new_bridge=None, clock=time.monotonic):
        self.lock_port = lock_port
        self.new_bridge = new_bridge or (lambda: bridge.Bridge(bridge.SnapshotReader(path), port=port))
        self.clock = clock
        self.wanted = False  # the person wants it running
        self.busy = False  # another bridge holds the lock
        self.worker = self.thread = self.lock = self.halt = None
        self.next_try = 0.0

    @property
    def running(self):
        return self.thread is not None

    @property
    def link_name(self):
        return self.worker.link_name if self.running else None

    def start(self):
        self.wanted = True
        self._try_start()

    def tick(self):
        """Called about once a second."""
        if self.wanted and not self.running and self.clock() >= self.next_try:
            self._try_start()

    def _try_start(self):
        if self.running:
            return
        try:
            lock = bridge.acquire_lock(self.lock_port)
        except OSError as e:
            if not self.busy:
                log.info("another bridge is already running (127.0.0.1:%d is taken: %s); trying again every %d s",
                         self.lock_port, e, bridge.RETRY_S)
            self.busy, self.next_try = True, self.clock() + bridge.RETRY_S
            return
        self.busy, self.lock = False, lock
        self.worker, self.halt = self.new_bridge(), threading.Event()
        self.thread = threading.Thread(target=self.worker.run, args=(self.halt,), name="bridge", daemon=True)
        self.thread.start()
        log.info("bridge started")

    def stop(self):
        """Stops the bridge and frees its serial port and lock."""
        self.wanted = self.busy = False
        if self.thread is not None:
            self.halt.set()
            self.thread.join(10)
            self.thread = None
            log.info("bridge stopped; the serial port is free")
        if self.lock is not None:
            self.lock.close()
            self.lock = None


def menu_state(service, snapshot, now):
    """What the menu bar and the menu show: {title, dimmed, status, windows: [line, line]}."""
    if service.running:
        name = service.link_name
        status = "LCD: connected on %s" % name if name else "LCD: looking for the Arduino…"
    elif service.busy:
        status = "LCD: another bridge is running (start-bridge.command?)"
    else:
        status = "LCD: stopped, the serial port is free"
    line = bridge.snapshot_to_line(snapshot, now)
    if line == "W":
        return {"title": "--%", "dimmed": not service.running, "status": status,
                "windows": ["No usage data yet: send Claude a message", "in a new local Code session."]}
    values = [int(v) for v in line.split("|")[1:]]
    five = values[0]
    return {"title": "--%" if five < 0 else "%d%%" % (five // 10), "dimmed": not service.running, "status": status,
            "windows": [window_text(label, values[i - 1], values[i]) for label, i in LABELS]}


def single_instance(path):
    """The open lock file while this is the only menu bar app running, else None."""
    import fcntl
    os.makedirs(os.path.dirname(path), exist_ok=True)
    f = open(path, "w")
    try:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        f.close()
        return None
    return f


# --- the menu bar (PyObjC) -------------------------------------------------------------

def make_icon(path):
    """Writes the app's icon (an .icns) to path: a white gauge on a slate rounded square."""
    from AppKit import (NSBezierPath, NSBitmapImageRep, NSColor, NSCompositingOperationSourceOver, NSGraphicsContext,
                        NSImage, NSImageSymbolConfiguration, NSMakeRect, NSPNGFileType, NSZeroRect)
    iconset = tempfile.mkdtemp(suffix=".iconset")
    symbol = NSImage.imageWithSystemSymbolName_accessibilityDescription_("gauge.with.dots.needle.67percent", None)
    for base in (16, 32, 128, 256, 512):
        for scale in (1, 2):
            size = base * scale
            rep = getattr(NSBitmapImageRep.alloc(), "initWithBitmapDataPlanes_pixelsWide_pixelsHigh_bitsPerSample_"
                          "samplesPerPixel_hasAlpha_isPlanar_colorSpaceName_bytesPerRow_bitsPerPixel_")(
                None, size, size, 8, 4, True, False, "NSDeviceRGBColorSpace", 0, 0)
            NSGraphicsContext.saveGraphicsState()
            NSGraphicsContext.setCurrentContext_(NSGraphicsContext.graphicsContextWithBitmapImageRep_(rep))
            NSColor.colorWithSRGBRed_green_blue_alpha_(0.20, 0.25, 0.31, 1.0).setFill()
            inset = size * 0.1
            NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
                NSMakeRect(inset, inset, size - 2 * inset, size - 2 * inset), size * 0.18, size * 0.18).fill()
            if symbol is not None:
                config = NSImageSymbolConfiguration.configurationWithPointSize_weight_(size * 0.42, 0.3)
                config = config.configurationByApplyingConfiguration_(
                    NSImageSymbolConfiguration.configurationWithPaletteColors_([NSColor.whiteColor()]))
                glyph = symbol.imageWithSymbolConfiguration_(config)
                w, h = glyph.size().width, glyph.size().height
                glyph.drawInRect_fromRect_operation_fraction_(
                    NSMakeRect((size - w) / 2, (size - h) / 2, w, h), NSZeroRect, NSCompositingOperationSourceOver, 1.0)
            NSGraphicsContext.restoreGraphicsState()
            name = "icon_%dx%d%s.png" % (base, base, "@2x" if scale == 2 else "")
            rep.representationUsingType_properties_(NSPNGFileType, {}).writeToFile_atomically_(
                os.path.join(iconset, name), True)
    subprocess.run(["iconutil", "-c", "icns", "-o", path, iconset], check=True)


def run_app(argv=None):
    p = argparse.ArgumentParser(description="The %s menu bar app (macOS)." % APP_NAME)
    p.add_argument("--port", help="use this serial port only, e.g. /dev/cu.usbserial-110 (default: find the board)")
    p.add_argument("--file", help="snapshot file to read (default: as in PROTOCOL.md)")
    p.add_argument("--verbose", "-v", action="store_true", help="log every line sent and received")
    p.add_argument("--make-icon", metavar="ICNS", help=argparse.SUPPRESS)  # used by install-autostart.command
    args = p.parse_args(argv)
    if args.make_icon:
        make_icon(args.make_icon)
        return 0

    import objc
    from AppKit import (NSAlert, NSApplication, NSApplicationActivationPolicyAccessory, NSImage, NSImageLeft,
                        NSMenu, NSMenuItem, NSStatusBar, NSVariableStatusItemLength, NSWorkspace)
    from Foundation import NSURL, NSObject, NSTimer
    from PyObjCTools import AppHelper

    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)  # no Dock icon

    lock_dir = os.path.join(os.path.expanduser("~"), "Library", "Application Support", "usage-lcd")
    instance = single_instance(os.path.join(lock_dir, "menubar.lock"))
    if instance is None:
        alert = NSAlert.alloc().init()
        alert.setMessageText_("%s is already running" % APP_NAME)
        alert.setInformativeText_("Its icon is in the menu bar, at the top right of the screen.")
        app.activateIgnoringOtherApps_(True)
        alert.runModal()
        return 0

    log_path = bridge.log_file(os.environ)
    bridge.setup_logging(args.verbose, log_path, console=sys.stderr is not None and sys.stderr.isatty())
    path = bridge.snapshot_path(os.environ, args.file)
    if not path:
        log.error("cannot tell where the snapshot file is: set USAGE_LCD_FILE or use --file")
        return 1
    log.info("%s %s reading %s", APP_NAME, bridge.VERSION, path)
    service = Service(path, int(os.environ.get("USAGE_LCD_LOCK_PORT") or bridge.LOCK_PORT), port=args.port)
    reader = bridge.SnapshotReader(path)  # its own, so the menu bar shows usage while the bridge is stopped
    quit_requested = []
    # launchctl bootout and stop-bridge.command send SIGTERM; the timer below then quits cleanly.
    signal.signal(signal.SIGTERM, lambda signum, frame: quit_requested.append(signum))

    class UsageLCDController(NSObject):
        @objc.python_method
        def build(self):
            self.item = NSStatusBar.systemStatusBar().statusItemWithLength_(NSVariableStatusItemLength)
            button = self.item.button()
            image = None
            for symbol in ("gauge.with.dots.needle.67percent", "speedometer"):
                image = NSImage.imageWithSystemSymbolName_accessibilityDescription_(symbol, "Claude usage")
                if image is not None:
                    break
            if image is not None:
                image.setTemplate_(True)  # follows the menu bar's light or dark look
                button.setImage_(image)
                button.setImagePosition_(NSImageLeft)
            menu = NSMenu.alloc().init()
            menu.setAutoenablesItems_(False)
            self.info = [self.add(menu, "", None) for _ in range(3)]
            for entry in self.info:
                entry.setEnabled_(False)
            menu.addItem_(NSMenuItem.separatorItem())
            self.toggle = self.add(menu, "Stop LCD Bridge", "toggle:")
            self.add(menu, "Open Log", "openLog:")
            menu.addItem_(NSMenuItem.separatorItem())
            self.add(menu, "Quit %s" % APP_NAME, "quit:", "q")
            self.item.setMenu_(menu)
            self.timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
                1.0, self, "tick:", None, True)

        @objc.python_method
        def add(self, menu, title, action, key=""):
            entry = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(title, action, key)
            entry.setTarget_(self)
            menu.addItem_(entry)
            return entry

        @objc.python_method
        def refresh(self):
            reader.poll()
            state = menu_state(service, reader.snapshot, time.time())
            button = self.item.button()
            button.setTitle_(" " + state["title"])
            button.setAppearsDisabled_(state["dimmed"])
            for entry, text in zip(self.info, [state["status"]] + state["windows"]):
                entry.setTitle_(text)
            self.toggle.setTitle_("Stop LCD Bridge" if service.wanted else "Start LCD Bridge")

        def tick_(self, timer):
            if quit_requested:
                self.quit_(None)
                return
            try:
                service.tick()
                self.refresh()
            except Exception:  # keep the menu bar alive
                log.exception("menu bar update failed")

        def toggle_(self, sender):
            if service.wanted:
                service.stop()
            else:
                service.start()
            self.refresh()

        def openLog_(self, sender):
            NSWorkspace.sharedWorkspace().openURL_(NSURL.fileURLWithPath_(log_path))

        def quit_(self, sender):
            service.stop()
            log.info("stopped")
            app.terminate_(None)

    controller = UsageLCDController.alloc().init()
    controller.build()
    service.start()
    controller.refresh()
    AppHelper.runEventLoop(installInterrupt=sys.stdin is not None and sys.stdin.isatty())
    return 0


if __name__ == "__main__":
    sys.exit(run_app())
