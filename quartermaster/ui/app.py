"""Navigation, the status bar and key hints, and the main loop."""

from collections import deque
import threading
import time

from PIL import Image

from .. import accounts, config, faults, hardware, uploads, wigle
from ..engine import DEFAULT_ROOT, Wardrive
from ..recon_engine import Recon
from ..setup_server import SetupServer
from . import icons, recon_screens, screens
from .gfx import (AMBER, FOOTER_H, GOLD, GREEN, H, LINE, MUTED, RED, STATUS_H, SURFACE, W, Canvas, clock, font,
                  image)

STALL_SECONDS = 10
FAULT_LIMIT, FAULT_WINDOW = 3, 10.0
REC_FILL = (70, 12, 14)


class HardwareWatch:
    """Keeps a fresh list of adapters, Bluetooth radios and GPS ports without stalling the screen."""

    def __init__(self, interval=5.0):
        self.interval = interval
        self.adapters, self.bluetooth, self.gps_ports, self.stalled = [], [], [], []
        self._since = {}
        self._stop = threading.Event()
        self._thread = None

    def refresh(self):
        try:
            self.adapters = hardware.wifi_adapters()
            self.bluetooth = hardware.bluetooth_adapters()
            self.gps_ports = hardware.gps_candidates()
            found = hardware.stalled_usb_adapters()
        except OSError:
            return
        now = time.monotonic()
        # A driver needs a few seconds after plugging in; only a lasting stall is worth a warning.
        self._since = {item["port"]: self._since.get(item["port"], now) for item in found}
        self.stalled = [item for item in found if now - self._since[item["port"]] >= STALL_SECONDS]

    def addresses(self):
        return hardware.local_addresses()

    def start(self):
        self.refresh()
        if self._thread is None:
            self._thread = threading.Thread(target=self._loop, daemon=True, name="hardware")
            self._thread.start()

    def _loop(self):
        while not self._stop.wait(self.interval):
            try:
                self.refresh()
            except Exception:
                faults.note("Hardware check")

    def stop(self):
        self._stop.set()


class App:
    def __init__(self, display, keyboard, gps, cfg, root=DEFAULT_ROOT, splash=True, wardrive=None, recon=None,
                 uploader=None, hw=None, accounts_path=None, config_path=None, tester=uploads.test,
                 setup=SetupServer):
        self.display, self.keyboard, self.gps, self.cfg = display, keyboard, gps, cfg
        self.root = root
        self.logs_dir = root / "logs"
        self.accounts_path = accounts_path
        self.config_path = config_path
        self.wardrive_factory = wardrive or (lambda c, g: Wardrive(c, g, root=root))
        self.recon = recon or Recon(cfg, gps, root=root)
        self.uploader = uploader or uploads.Uploader(root, cfg, active=self.active_log, accounts_path=accounts_path)
        self.hw = hw or HardwareWatch()
        self.tester = tester
        self.setup_class = setup
        self.drive = None
        self.tests = {}
        self.running = True
        self.dirty = True
        self.stack = [screens.Home()]
        if splash and cfg.get("splash", True):
            self.stack.append(screens.Splash())
        self.toast_text, self.toast_color, self.toast_until = "", GOLD, 0.0
        self._booted = False
        self._calls = deque()
        self._files, self._files_at = [], None
        self._upload, self._upload_at = None, None
        self._accounts, self._accounts_stamp = None, None
        self._shade = Image.new("RGBA", (W, H), (0, 0, 0, 170))
        self._faults = []

    @property
    def screen(self):
        return self.stack[-1]

    @property
    def wardriving(self):
        return self.drive is not None and self.drive.state == "running"

    @property
    def recon_running(self):
        return self.recon.state == "running"

    def active_log(self):
        return self.drive.active_log() if self.drive is not None else None

    def accounts(self):
        stamp = accounts.stamp(self.accounts_path)
        if self._accounts is None or stamp != self._accounts_stamp:
            self._accounts, self._accounts_stamp = accounts.load(self.accounts_path), stamp
        return self._accounts

    def upload_status(self):
        now = time.monotonic()
        if self._upload_at is None or now - self._upload_at > 1.5:
            self._upload, self._upload_at = self.uploader.status(), now
        return self._upload

    def log_files(self, refresh=False):
        now = time.monotonic()
        if refresh or self._files_at is None or now - self._files_at > 3:
            active, files = self.active_log(), []
            try:
                paths = sorted(self.logs_dir.glob(wigle.LOG_GLOB), reverse=True)
            except OSError:
                paths = []
            for path in paths:
                try:
                    size = path.stat().st_size
                except OSError:
                    continue
                files.append({"path": path, "name": path.name, "size": size, "active": path == active,
                              "epoch": screens.log_epoch(path.name)})
            self._files, self._files_at = files, now
        return self._files

    def stale(self):
        """Forget cached lists after something changed them."""
        self._files_at = self._upload_at = None
        self.dirty = True

    def push(self, screen):
        self.stack.append(screen)
        self.dirty = True

    def pop(self):
        if len(self.stack) > 1:
            self.stack.pop().closed(self)
        self.dirty = True

    def home(self):
        while len(self.stack) > 1:
            self.stack.pop().closed(self)
        self.dirty = True

    def open(self, name):
        if name == "Wardrive":
            return self.start_wardrive()
        factory = {"Recon": recon_screens.Recon, "Logs": screens.Logs, "Uploads": screens.Uploads,
                   "Hardware": screens.Hardware, "Settings": screens.Settings, "Keys": screens.Keys,
                   "About": screens.About}
        self.home()
        self.push(factory[name]())

    def quit(self):
        self.running = False

    def toast(self, text, color=GOLD, seconds=3.0):
        self.toast_text, self.toast_color, self.toast_until = text, color, time.monotonic() + seconds
        self.dirty = True

    def confirm(self, title, detail, action, callback, danger=False, default=0):
        self.push(screens.Confirm(title, detail, action, callback, danger=danger, default=default))

    def call_soon(self, function):
        """Run a function on the screen thread. Safe to call from any thread."""
        self._calls.append(function)

    def busy(self, text):
        """Show a notice right away, before a call that takes a few seconds."""
        picture = self.compose()
        c = Canvas()
        c.image.paste(picture, (0, 0))
        c.image.paste(self._shade, (0, 0), self._shade)
        c.rect((50, 68, W - 50, 102), fill=SURFACE, outline=GOLD, radius=8)
        c.text_center(W // 2, 78, text, font("bold", 11), GOLD)
        self.display.show(c.image)

    def save(self):
        try:
            config.save(self.cfg, self.config_path or config.PATH)
            return True
        except OSError:
            self.toast("The settings could not be saved", RED)
            return False

    def change(self, key, value):
        self.cfg[key] = value
        self.save()
        if key == "gps_source":
            self.gps.mode = value
        if key == "gps_power":
            self.gps.power = value
        if key == "ignore" and self.drive is not None:
            self.drive.set_ignore(value)
        if key == "auto_upload" and value:
            self.uploader.kick()
        self.recon.cfg = dict(self.cfg)
        self.stale()

    def ignore(self, entry):
        entry = entry.strip()[:64]
        entries = list(self.cfg.get("ignore", []))
        if entry and entry not in entries:
            self.change("ignore", (entries + [entry])[:config.MAX_IGNORED])
            self.toast("%s will not be logged" % entry)

    def save_key(self, field, value):
        try:
            saved = accounts.update({field: value}, self.accounts_path)
        except OSError:
            return self.toast("The key could not be saved", RED)
        self.keys_changed()
        service = screens.FIELD_SERVICE[field]
        self.tests.pop(service, None)
        if not value:
            self.toast("Key removed")
        elif accounts.configured(saved, service):
            self.toast("Saved. Checking it now")
            self.test_key(service)
        else:
            self.toast("Saved. Now add the WiGLE API %s" % ("token" if field == "wigle_name" else "name"))

    def keys_changed(self):
        self._accounts = None
        self.uploader.kick()
        self.stale()

    def test_key(self, service):
        if self.tests.get(service, ("",))[0] == "busy":
            return

        def work():
            try:
                outcome = self.tester(service, accounts.load(self.accounts_path))
                self.tests[service] = (outcome.state, outcome.detail)
            except Exception:
                self.tests[service] = (uploads.RETRY, "The key could not be checked")
            self.call_soon(self.keys_checked)
        self.tests[service] = ("busy", "Checking with %s…" % accounts.SERVICE_NAMES[service])
        self.dirty = True
        threading.Thread(target=work, daemon=True, name="key-test").start()

    def keys_checked(self):
        self.uploader.kick()
        self.stale()

    def setup_server(self):
        def changed():
            self.call_soon(self.keys_changed)
            self.call_soon(lambda: self.toast("Keys saved from the setup page", GREEN))

        def ignored(items):
            self.call_soon(lambda: self.change("ignore", list(items)))
        return self.setup_class(self.logs_dir, self.cfg, self.accounts_path, on_keys=changed, on_ignore=ignored,
                                on_upload=lambda: self.call_soon(self.upload_now), marks=self.uploader.marks)

    def start_wardrive(self):
        if self.wardriving:
            if not isinstance(self.screen, screens.Wardrive):
                self.home()
                self.push(screens.Wardrive(self))
            return True
        if self.recon_running:
            self.confirm("Stop Recon first?", "Recon and a wardrive never run together. Recon stops and the "
                         "wardrive starts.", "Start wardrive", self._swap_to_wardrive, default=1)
            return False
        drive = self.wardrive_factory(self.cfg, self.gps)
        try:
            drive.start()
        except (RuntimeError, OSError) as exc:
            self.toast(str(exc) or "The wardrive could not start", RED, 5)
            return False
        self.drive = drive
        self.stale()
        self.home()
        self.push(screens.Wardrive(self))
        if self.hw.stalled:
            self.toast("%s is not starting. See Hardware" % self.hw.stalled[0]["label"], AMBER, 5)
        return True

    def _swap_to_wardrive(self):
        self.stop_recon()
        self.start_wardrive()

    def ask_stop_wardrive(self):
        self.confirm("Stop the wardrive?", "The log is saved and sent to your upload sites.", "Stop + save",
                     self.stop_wardrive, default=1)

    def stop_wardrive(self, show=True):
        if not self.wardriving:
            return
        self.busy("Saving the wardrive…")
        self.drive.stop()
        status = self.drive.status()
        self.stale()
        self.uploader.kick()
        if show:
            self.home()
            self.push(screens.Summary(status))

    def toggle_recon(self):
        if self.recon_running:
            flagged = self.recon.summary()["flagged_total"]
            self.stop_recon()
            return self.toast("Recon stopped. %s flagged" % screens.plural(flagged, "device"))
        if self.wardriving:
            return self.confirm("Stop the wardrive first?", "Recon and a wardrive never run together. The wardrive "
                                "is saved and Recon starts.", "Start Recon", self._swap_to_recon, default=1)
        self.start_recon()

    def _swap_to_recon(self):
        self.stop_wardrive(show=False)
        self.start_recon()

    def start_recon(self):
        self.recon.cfg = dict(self.cfg)
        try:
            self.recon.start()
        except (RuntimeError, OSError) as exc:
            return self.toast(str(exc) or "Recon could not start", RED, 5)
        if not isinstance(self.screen, recon_screens.Recon):
            self.home()
            self.push(recon_screens.Recon())
        self.toast("Recon started", GREEN)

    def stop_recon(self):
        if self.recon_running:
            self.busy("Stopping Recon…")
            self.recon.stop()
            self.dirty = True

    def upload_now(self, name=None):
        if not accounts.ready(self.accounts()):
            return self.toast("Add a key first: press K on the menu", AMBER, 4)
        if not self.upload_status()["files"]:
            return self.toast("There are no finished logs to send yet", AMBER)
        self.uploader.send_again(name)
        self.stale()
        self.toast("Uploading now")

    def delete_log(self, entry):
        path = entry["path"]
        try:
            path.unlink()
        except OSError:
            return self.toast("The log could not be deleted", RED)
        summary = path.with_name(path.name.replace(".wigle.csv", ".summary.csv"))
        try:
            summary.unlink()
        except OSError:
            pass
        self.uploader.ledger.forget(path.name)
        self.stale()
        self.pop()
        self.toast("Log deleted")

    def compose(self):
        c = Canvas()
        top = self.stack[-1]
        modal = isinstance(top, screens.Modal)
        under = next(s for s in reversed(self.stack) if not isinstance(s, screens.Modal))
        if under.chrome:
            self.status_bar(c, under)
        under.render(c, self)
        if under.chrome and not modal and not isinstance(under, recon_screens.Recon):
            recon_screens.alert_banner(c, self)
        if modal:
            c.image.paste(self._shade, (0, 0), self._shade)
            top.render(c, self)
        if under.chrome:
            self.footer(c, top)
        if self.toast_text and time.monotonic() < self.toast_until:
            f = font("bold", 10)
            width = min(W - 16, int(c.text_width(self.toast_text, f)) + 24)
            x, y = (W - width) // 2, H - FOOTER_H - 24
            c.rect((x, y, x + width, y + 18), fill=SURFACE, outline=self.toast_color, radius=9)
            c.text_center(W // 2, y + 3, c.fit(self.toast_text, width - 16, f), f, self.toast_color)
        return c.image

    def status_bar(self, c, screen):
        c.rect((0, 0, W, STATUS_H - 1), fill=SURFACE)
        c.line((0, STATUS_H, W, STATUS_H), fill=LINE)
        c.paste(image("quartermaster-logo-dark.png", (22, 16)), (3, 1))
        c.text((28, 3), screen.title, font("bold", 11), GOLD)
        small = font("monob", 9)
        right = W - 5
        fix = self.gps.snapshot()
        label = "GPS %s" % (fix.get("satellites") or "OK") if fix.get("valid") else \
            ("NO GPS" if fix.get("source", "none") == "none" else "GPS --")
        c.text_right(right, 4, label, small, GREEN if fix.get("valid") else AMBER)
        right -= int(c.text_width(label, small)) + 8
        status = self.upload_status()
        if status["uploading"] or (status["waiting"] and status["keys"]):
            count = str(status["waiting"])
            color = GOLD if status["uploading"] else MUTED
            c.text_right(right, 4, count, small, color)
            right -= int(c.text_width(count, small)) + 2
            icons.draw(c, "cloud", right - 13, 2, 13, color)
            right -= 20
        if self.recon_running:
            summary = self.recon.summary()
            color = RED if summary["strong"] or summary["following"] else AMBER if summary["flagged"] else GREEN
            if summary["flagged"]:
                c.text_right(right, 4, str(summary["flagged"]), small, color)
                right -= int(c.text_width(str(summary["flagged"]), small)) + 2
            icons.draw(c, "eye", right - 13, 2, 13, color)
            right -= 20
        if self.wardriving:
            text = clock(self.drive.elapsed())
            width = int(c.text_width(text, font("monob", 8))) + 18
            c.rect((right - width, 3, right, 15), fill=REC_FILL, radius=6)
            blink = int(time.monotonic() * 2) % 2 == 0
            c.draw.ellipse((right - width + 5, 6, right - width + 11, 12), fill=RED if blink else (120, 30, 30))
            c.text((right - width + 14, 4), text, font("monob", 8), RED)

    def footer(self, c, screen):
        y = H - FOOTER_H
        c.rect((0, y, W, H), fill=SURFACE)
        c.line((0, y, W, y), fill=LINE)
        x = 4
        for key, label in screen.hints(self):
            if x > W - 36:
                break
            x += c.keycap(x, y + 3, key, label)

    def booted(self):
        if self.cfg.get("auto_start"):
            self.start_wardrive()
        elif not accounts.ready(self.accounts()):
            self.toast("Press K to add your upload keys", GOLD, 5)

    def step(self, timeout=0.05):
        while self._calls:
            self._calls.popleft()()
            self.dirty = True
        if isinstance(self.screen, screens.Splash) and self.screen.done():
            self.pop()
        if not self._booted and not isinstance(self.screen, screens.Splash):
            self._booted = True
            self.booted()
        self.keyboard.text_mode = self.screen.text_mode
        for key in self.keyboard.poll(timeout):
            if key == "R" and not isinstance(self.screen, (recon_screens.Recon, screens.Modal)) \
                    and recon_screens.fresh_alert(self):
                self.open("Recon")
            else:
                self.screen.handle(key, self)
            self.dirty = True

    def run(self):
        self.hw.start()
        self.uploader.start()
        drawn = 0.0
        try:
            while self.running:
                try:
                    self.step(0.02 if isinstance(self.screen, screens.Splash) else 0.05)
                    now = time.monotonic()
                    if self.dirty or now - drawn >= self.screen.refresh:
                        self.display.show(self.compose())
                        drawn, self.dirty = now, False
                except Exception:
                    self.recover()
        finally:
            self.shutdown()

    def recover(self):
        """A screen failed: go back to the menu. Scanning runs in its own threads and carries on.

        A screen that keeps failing ends the app instead, so that it can be started again.
        """
        faults.note("Screen %s" % type(self.screen).__name__)
        now = time.monotonic()
        self._faults = [at for at in self._faults if now - at < FAULT_WINDOW] + [now]
        if len(self._faults) >= FAULT_LIMIT:
            raise
        self.home()
        self.toast("That screen hit a problem. Back at the menu.", AMBER, 5)

    def shutdown(self):
        if self.wardriving:
            self.drive.stop()
        if self.recon_running:
            self.recon.stop()
        while self.stack:
            self.stack.pop().closed(self)
        self.uploader.stop()
        self.hw.stop()
