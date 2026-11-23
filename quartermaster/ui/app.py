"""Navigation, the status bar and key hints, and the main loop."""

from collections import deque
import os
from pathlib import Path
import threading
import time

from PIL import Image

from .. import accounts, adsb, config, faults, flasher, hardware, power, updates_check, uploads, wigle
from .. import engine
from ..engine import DEFAULT_ROOT, Wardrive
from ..nodes import NodeManager
from ..recon_engine import Recon
from ..setup_server import SetupServer
from . import icons, recon_screens, screens
from .gfx import (AMBER, FOOTER_H, GOLD, GREEN, H, LINE, MUTED, RED, STATUS_H, SURFACE, TEXT, W, Canvas, clock,
                  font, image)
from .keys import navigation

STALL_SECONDS = 10
FAULT_LIMIT, FAULT_WINDOW = 3, 10.0
LOW, VERY_LOW, EMPTY = 20, 10, 3
EMPTY_SECONDS, CLOSING_SECONDS = 15, 10
BATTERY_SILENCE = 60
REPEATING = ("UP", "DOWN", "LEFT", "RIGHT", "PAGEUP", "PAGEDOWN", "BACKSPACE", "DELETE")
REC_FILL = (70, 12, 14)
NODES_SECONDS = 1.0
PORT_WAIT, PORT_STEP = 20.0, 0.1
FLASH_WAIT = 60.0
FINISH_WAIT = 30.0
UPDATE_SECONDS = 15.0
GONE = "The node is not plugged in any more. Plug it in again and retry."
FLASH_LAST = "Battery almost empty. Closing once the board is flashed."
BUSY = "Something else is using this board's port. Plug the board in again and retry."


def serial_ports(sys_tty=hardware.SYS_TTY, dev=Path("/dev"), by_id=hardware.BY_ID):
    """USB serial ports an ESP32-C5 board may be on: its own USB port first, then the bridges such boards use."""
    links = hardware.by_id_links(by_id)
    found = []
    for name in hardware.usb_serial_names(sys_tty):
        usb_id = hardware.usb_id_of(name, sys_tty)
        if usb_id != hardware.NODE_NATIVE and usb_id not in hardware.NODE_BRIDGES:
            continue
        real = os.path.realpath(str(Path(dev) / name))
        found.append({"name": name, "path": links.get(real, str(Path(dev) / name)), "real": real,
                      "native": usb_id == hardware.NODE_NATIVE})
    return sorted(found, key=lambda port: not port["native"])


class HardwareWatch:
    """Keeps the battery level and the lists of adapters, Bluetooth radios and GPS ports fresh off the screen thread."""

    def __init__(self, interval=5.0):
        self.interval = interval
        self.adapters, self.bluetooth, self.gps_ports, self.stalled = [], [], [], []
        self.serial_ports = []
        self.sdr, self.readsb, self.sky_use = [], "", ""
        self.battery, self._battery_read = None, 0.0
        self._since = {}
        self._stop = threading.Event()
        self._thread = None

    def refresh(self):
        level, now = power.battery(), time.monotonic()
        # The fuel gauge misses a reading now and then, and the battery is still there.
        if level:
            self.battery, self._battery_read = level, now
        elif now - self._battery_read > BATTERY_SILENCE:
            self.battery = None
        try:
            self.adapters = hardware.wifi_adapters()
            self.bluetooth = hardware.bluetooth_adapters()
            self.gps_ports = hardware.gps_candidates()
            self.serial_ports = serial_ports()
            self.sdr, self.readsb = hardware.rtl_sdr_dongles(), adsb.find_readsb()
            self.sky_use = adsb.in_use() if self.sdr else ""
            found = hardware.stalled_usb_adapters()
        except OSError:
            return
        # A driver needs a few seconds after plugging in; only a lasting stall is worth a warning.
        self._since = {item["port"]: self._since.get(item["port"], now) for item in found}
        self.stalled = [item for item in found if now - self._since[item["port"]] >= STALL_SECONDS]

    def addresses(self):
        return hardware.local_addresses()

    def start(self):
        self._check()
        if self._thread is None:
            self._thread = threading.Thread(target=self._loop, daemon=True, name="hardware")
            self._thread.start()

    def _loop(self):
        while not self._stop.wait(self.interval):
            self._check()

    def _check(self):
        try:
            self.refresh()
        except Exception:
            faults.note("Hardware check")

    def stop(self):
        self._stop.set()


class FlashJob:
    """Flashes one board on a worker thread, so that the screen thread only draws how far it got.

    state is "starting", "writing", "done" or "failed", with error in words when it failed."""

    def __init__(self, nodes, target, mode, flasher_class=flasher.Flasher, manifest=flasher.MANIFEST):
        self.nodes, self.target, self.mode = nodes, target, mode
        self.flasher_class, self.manifest = flasher_class, manifest
        self.state, self.error, self.waiting = "starting", "", False
        self.done = self.total = 0
        self.path = target["path"]
        self._thread = None

    @property
    def fraction(self):
        return self.done / self.total if self.total else 0.0

    @property
    def needs_boot(self):
        return self.error in (flasher.NO_ANSWER, flasher.UNREADY)

    def start(self):
        self._thread = threading.Thread(target=self.run, daemon=True, name="flash")
        self._thread.start()

    def wait(self, seconds):
        if self._thread is not None:
            self._thread.join(seconds)

    def native(self, path):
        """Whether the board is on the ESP32-C5's own USB port, from the port it is on now."""
        name = os.path.basename(os.path.realpath(path))
        usb_id = hardware.usb_id_of(name, getattr(self.nodes, "sys_tty", hardware.SYS_TTY))
        return usb_id == hardware.NODE_NATIVE if usb_id else self.target["native"]

    def node_on(self, path):
        real = os.path.realpath(path)
        return next((node["id"] for node in self.nodes.known() + self.nodes.found()
                     if node["connected"] and os.path.realpath(node["port"]) == real), None)

    def hold(self, path):
        """Take the port of a board without node firmware. The GPS search or a look for a node may have it for a
        few seconds; the node itself when it turns out to be one."""
        deadline = time.monotonic() + PORT_WAIT
        while not hardware.hold_port(path, "flash"):
            node_id = self.node_on(path)
            if node_id is not None:
                return node_id
            if time.monotonic() >= deadline:
                raise flasher.FlashError(BUSY)
            self.waiting = True
            time.sleep(PORT_STEP)
        self.waiting = False
        return None

    def progress(self, done, total):
        self.done, self.total = done, total

    def run(self):
        error, held, path = "", False, None
        try:
            node_id = self.target["node"]
            if node_id is None:
                node_id = self.hold(self.target["path"])
                held = node_id is None
                path = self.target["path"] if held else None
            if node_id is not None:
                self.waiting = False
                path = self.nodes.release(node_id)
                if path is None:
                    raise flasher.FlashError(GONE)
            self.path = path
            with self.flasher_class(path) as board:
                board.connect(self.native(path))
                self.state = "writing"
                board.install(self.manifest, self.mode, progress=self.progress)
        except flasher.FlashError as exc:
            error = str(exc) or flasher.FAILED
        except Exception:
            faults.note("Flashing a node")
            error = flasher.FAILED
        finally:
            if held:
                hardware.let_go_port(path, "flash")
            if path is not None:
                self.nodes.take_back(path)
        self.error = error
        self.state = "failed" if error else "done"


class App:
    def __init__(self, display, keyboard, gps, cfg, root=DEFAULT_ROOT, splash=True, wardrive=None, recon=None,
                 uploader=None, hw=None, accounts_path=None, config_path=None, tester=uploads.test,
                 setup=SetupServer, nodes=None, flasher_class=flasher.Flasher, updates=updates_check.check):
        self.display, self.keyboard, self.gps, self.cfg = display, keyboard, gps, cfg
        self.root = root
        self.logs_dir = root / "logs"
        self.accounts_path = accounts_path
        self.config_path = config_path
        self.nodes = nodes if nodes is not None else NodeManager(cfg)
        self._thread = threading.current_thread()
        self.nodes.on_change = lambda entries: self.on_screen_thread(lambda: self.change("nodes", self.nodes.entries()))
        self.nodes.on_key = lambda key: self.call_soon(lambda: self.change("cluster_key", key))
        self.wardrive_factory = wardrive or (lambda c, g: Wardrive(c, g, root=root, nodes=self.nodes))
        self.recon = recon or Recon(cfg, gps, root=root, nodes=self.nodes)
        self.flasher_class = flasher_class
        self.flash_job = None
        self.updates = updates
        self.update, self._update_ask, self._update_at = None, None, 0.0
        self._announced, self._nodes_at = set(), None
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
        self._battery_step, self._empty_since, self._charger_since, self._closing = None, None, None, None
        self._pressed = None
        self._deaf = False
        self._finisher = None

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
                paths = sorted(list(self.logs_dir.glob(wigle.LOG_GLOB)) + list(self.logs_dir.glob(adsb.FILE_GLOB)),
                               reverse=True)
            except OSError:
                paths = []
            for path in paths:
                try:
                    size = path.stat().st_size
                except OSError:
                    continue
                files.append({"path": path, "name": path.name, "size": size, "active": path == active,
                              "epoch": screens.log_epoch(path.name), "aircraft": adsb.is_aircraft(path)})
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

    def ask_quit(self):
        if self.flashing():
            return self.toast("Wait until the board is flashed", AMBER)
        said = self.drive.failed if self.wardriving else ""
        detail = "Finished logs keep uploading next time."
        if self.wardriving or self.recon_running:
            detail = self.ending("The scan stops and its log is saved.", "The scan stops.")
        self.confirm("Quit Quartermaster?", detail, "Quit", lambda: self.leave(said), danger=True)

    def leave(self, said=""):
        """Quit as asked. A wardrive that turns out not to be saved, when nothing has said so yet, shows why first."""
        drive = self.drive if self.wardriving else None
        self.stop_wardrive(show=False, upload=False)
        if drive is None or drive.failed in ("", said):
            return self.quit()
        self.push(screens.Summary(drive.status()))

    def ending(self, saved, stops):
        """What a dialog says of the wardrive it would end: once rows were lost, why, and never that it is saved."""
        failed = self.drive.failed if self.wardriving else ""
        if failed:
            return "%s %s." % (stops, failed)
        if self.wardriving and self.drive.log_path is None:
            return "%s Nothing has been logged yet." % stops
        return saved

    def toast(self, text, color=GOLD, seconds=3.0):
        self.toast_text, self.toast_color, self.toast_until = text, color, time.monotonic() + seconds
        self.dirty = True

    def confirm(self, title, detail, action, callback, danger=False, default=0):
        self.push(screens.Confirm(title, detail, action, callback, danger=danger, default=default))

    def call_soon(self, function):
        """Run a function on the screen thread. Safe to call from any thread."""
        self._calls.append(function)

    def on_screen_thread(self, function):
        """Run a function now on the screen thread, else as soon as the screen thread can."""
        if threading.current_thread() is self._thread:
            function()
        else:
            self.call_soon(function)

    def busy(self, text):
        """Show a notice right away, before a call that takes a few seconds."""
        picture = self.compose()
        c = Canvas()
        c.image.paste(picture, (0, 0))
        c.image.paste(self._shade, (0, 0), self._shade)
        c.rect((50, 68, W - 50, 102), fill=SURFACE, outline=GOLD, radius=8)
        c.text_center(W // 2, 78, text, font("bold", 11), GOLD)
        self.display.show(c.image)
        self._deaf = True

    def save(self):
        try:
            config.save(self.cfg, self.config_path or config.PATH)
            return True
        except OSError:
            self.toast("The settings could not be saved", RED)
            return False

    def change(self, key, value):
        self.cfg[key] = value
        if key == "country":
            self.cfg["country_set"] = True
        self.save()
        if key == "gps_source":
            self.gps.mode = value
        if key == "gps_power":
            self.gps.power = value
        if key == "ignore" and self.drive is not None:
            self.drive.set_ignore(value)
        if key == "auto_upload" and value:
            self.uploader.kick()
        if key == "country":
            self.nodes.set_country(value)
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

    def watch_nodes(self):
        """Say once per node and session that a node not in the settings was plugged in."""
        now = time.monotonic()
        if self._nodes_at is not None and now - self._nodes_at < NODES_SECONDS:
            return
        self._nodes_at = now
        fresh = [node["id"] for node in self.nodes.found() if node["id"] not in self._announced]
        if fresh:
            self._announced.update(fresh)
            if not isinstance(self.screen, screens.Nodes):
                self.toast("New node found. Settings > Nodes to add it.", GOLD, 5)

    def remove_node(self, node_id):
        node = self.nodes.node(node_id)
        self.nodes.remove(node_id)
        self.pop()
        self.toast("%s removed" % node["name"])

    def flash_blocked(self):
        if self.wardriving:
            return "Stop the wardrive first"
        if self.recon_running:
            return "Stop Recon first"
        return ""

    def flashing(self):
        return self.flash_job is not None and self.flash_job.state in ("starting", "writing")

    def ask_flash(self, target):
        blocked = self.flash_blocked()
        if blocked:
            return self.toast("%s. Flashing restarts the board." % blocked, AMBER, 4)
        if target["node"] is None:
            return self.confirm("Install the node firmware?", "This erases the board and makes it a Quartermaster "
                                "Node. It takes about a minute.", "Install", lambda: self.flash(target, "first"),
                                default=1)
        options = [("update", "Update", "keeps its settings"), ("first", "First install", "erases the board")]
        self.push(screens.Picker("How to flash it?", options, target["mode"], lambda mode: self.flash(target, mode)))

    def flash(self, target, mode):
        """Flash a board on a worker thread, and show how far it got."""
        blocked = self.flash_blocked()
        if blocked:
            return self.toast("%s. Flashing restarts the board." % blocked, AMBER, 4)
        if self.flashing():
            return self.toast("Another board is being flashed", AMBER)
        self.flash_job = FlashJob(self.nodes, target, mode, self.flasher_class)
        self.flash_job.start()
        self.push(screens.Flashing(self.flash_job))

    def retry_flash(self, job, mode=None):
        """Try again on the same board: as a node if it came back as one, else on the port it was on."""
        target = dict(job.target)
        node = self.nodes.node(target["node"]) if target["node"] else None
        if node is None or not node["connected"]:
            target.update(node=None, path=job.path)
        while isinstance(self.screen, (screens.Flashing, screens.Confirm)):
            self.pop()
        self.flash(target, mode or job.mode)

    def check_updates(self):
        """Ask in the background whether a newer Quartermaster is out. Nothing is downloaded."""
        if self.update_state()[0] == "busy":
            return
        ask = object()

        def work():
            try:
                state, _, text = self.updates()
            except Exception:
                faults.note("Update check")
                state, text = updates_check.UNKNOWN, updates_check.UNKNOWN_TEXT
            if self._update_ask is ask:
                self.update = (state, text)
            self.call_soon(lambda: None)
        self.update, self._update_ask, self._update_at = ("busy", ""), ask, time.monotonic()
        self.dirty = True
        threading.Thread(target=work, daemon=True, name="update-check").start()

    def update_state(self):
        """(state, words) of the last check. One with no answer after UPDATE_SECONDS counts as offline, because the
        timeout of each request does not bound the name lookup."""
        state, text = self.update or ("", "")
        if state == "busy" and time.monotonic() - self._update_at >= UPDATE_SECONDS:
            return updates_check.OFFLINE, updates_check.OFFLINE_TEXT
        return state, text

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
        if drive.leftovers():
            self.busy("Finishing the last wardrive…")
        try:
            drive.start()
        except RuntimeError as exc:
            self.toast(str(exc) or "The wardrive could not start", RED, 5)
            return False
        except OSError as exc:
            self.toast("The logs folder cannot be used: %s" % (exc.strerror or exc), RED, 5)
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
        self.confirm("Stop the wardrive?", self.ending("The log is saved and sent to your upload sites.",
                                                       "The wardrive stops."), "Stop + save",
                     self.stop_wardrive, default=1)

    def stop_wardrive(self, show=True, upload=True):
        if not self.wardriving:
            return
        self.busy("Saving the wardrive…")
        self.drive.stop()
        status = self.drive.status()
        self.stale()
        if upload:
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
            return self.confirm("Stop the wardrive first?", self.ending(
                "Recon and a wardrive never run together. The wardrive is saved and Recon starts.",
                "The wardrive stops and Recon starts."), "Start Recon", self._swap_to_recon, default=1)
        self.start_recon()

    def _swap_to_recon(self):
        drive = self.drive
        self.stop_wardrive(show=False)
        self.start_recon()
        if drive.failed:
            self.toast(drive.failed, RED, 5)

    def start_recon(self):
        self.recon.cfg = dict(self.cfg)
        try:
            self.recon.start()
        except RuntimeError as exc:
            return self.toast(str(exc) or "Recon could not start", RED, 5)
        except OSError as exc:
            return self.toast("Recon could not start: %s" % (exc.strerror or exc), RED, 5)
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
        summary = adsb.summary_path(path) if entry.get("aircraft") else \
            path.with_name(path.name.replace(".wigle.csv", ".summary.csv"))
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
        f, battery = font("bold", 11), self.hw.battery
        parts = self.indicators(c)
        if battery:
            room = W - 29 - sum(width + 8 for width, _ in parts) - c.text_width(screen.title, f)
            parts.insert(0, self.gauge(c, battery, room))
        right = W - 5
        for width, paint in parts:
            paint(right)
            right -= width + 8
        c.text((28, 3), screen.title, f, GOLD, width=max(0, right - 24))

    def indicators(self, c):
        """What the status bar shows left of the battery, from the right inwards: (width, paint at that edge)."""
        parts = []
        fix = self.gps.snapshot()
        label = "GPS %s" % (fix.get("satellites") or "OK") if fix.get("valid") else \
            ("NO GPS" if fix.get("source", "none") == "none" else "GPS --")
        parts.append(self.counted(c, None, label, GREEN if fix.get("valid") else AMBER))
        status = self.upload_status()
        if status["uploading"] or (status["waiting"] and status["keys"]):
            parts.append(self.counted(c, "cloud", str(status["waiting"]), GOLD if status["uploading"] else MUTED))
        if self.recon_running:
            summary = self.recon.summary()
            color = RED if summary["strong"] or summary["following"] else AMBER if summary["flagged"] else GREEN
            parts.append(self.counted(c, "eye", str(summary["flagged"] or ""), color))
        if self.wardriving:
            parts.append(self.recording(c, clock(self.drive.elapsed())))
        return parts

    def gauge(self, c, battery, room):
        """The battery: green with a bolt while it charges, amber from 20 % down and red from 10 %.

        `room` is what the title and the other indicators leave. Where that is too little for both, the number
        stays and the bolt goes. Where the number does not fit either, the bolt comes back alone for as long as
        it fits. The number is judged as "100%", so that it does not come and go with the level.
        """
        level, charging = battery["percent"], battery["charging"]
        color = GREEN if charging else RED if level <= 10 else AMBER if level <= 20 else TEXT
        small = font("monob", 9)
        widest = int(c.text_width("100%", small)) + 3
        reading = "%d%%" % level if room >= 30 + widest else ""
        bolt = charging and room >= 41 + (widest if reading else 0)
        text = int(c.text_width(reading, small)) + 3 if reading else 0

        def paint(right):
            c.battery(right - 22, 4, level, color, MUTED if color == TEXT else color)
            if reading:
                c.text_right(right - 25, 4, reading, small, color)
            if bolt:
                icons.draw(c, "bolt", right - 33 - text, 3, 11, color)
        return 22 + text + (11 if bolt else 0), paint

    def counted(self, c, icon, text, color):
        """A number or a word, with an icon before it."""
        small = font("monob", 9)
        width = int(c.text_width(text, small))

        def paint(right):
            c.text_right(right, 4, text, small, color)
            if icon:
                icons.draw(c, icon, right - width - (15 if text else 13), 2, 13, color)
        return width + ((15 if text else 13) if icon else 0), paint

    def recording(self, c, text):
        small = font("monob", 8)
        width = int(c.text_width(text, small)) + 18

        def paint(right):
            c.rect((right - width, 3, right, 15), fill=REC_FILL, radius=6)
            blink = int(time.monotonic() * 2) % 2 == 0
            c.draw.ellipse((right - width + 5, 6, right - width + 11, 12), fill=RED if blink else (120, 30, 30))
            c.text((right - width + 14, 4), text, small, RED)
        return width, paint

    def footer(self, c, screen):
        y = H - FOOTER_H
        c.rect((0, y, W, H), fill=SURFACE)
        c.line((0, y, W, y), fill=LINE)
        x = 4
        for key, label in screen.hints(self):
            if x > W - 36:
                break
            x += c.keycap(x, y + 3, key, label)

    def watch_battery(self):
        """Say so as the battery runs down. Almost empty, save what is running and close.

        The system's own low battery warning and safe power-off wait for as long as an app is open.
        A weak charger comes and goes with the load, so before the save one reading of it settles nothing:
        that wait ends only when the charger has held for as long as the wait itself. After the save one
        reading calls the close off, as it does for the system's own countdown.
        """
        battery, now = self.hw.battery, time.monotonic()
        charging = bool(battery) and battery["charging"]
        if self._closing is not None:
            if charging:
                self._empty_since = self._closing = None
                self.toast("Charging. Quartermaster stays open.", GREEN, 5)
            elif now >= self._closing:
                if not self.flashing():
                    return self.quit()
                self._closing = now + 1
                if self.toast_text != FLASH_LAST:
                    self.toast(FLASH_LAST, RED, CLOSING_SECONDS)
            return
        if not battery:
            return
        level = battery["percent"]
        if level > EMPTY:
            self._empty_since = None
        elif not charging:
            self._charger_since = None
            if self._empty_since is None:
                self._empty_since = now
            elif now - self._empty_since >= EMPTY_SECONDS:
                return self.close_soon()
        elif self._charger_since is None:
            self._charger_since = now
        elif now - self._charger_since >= EMPTY_SECONDS:
            self._empty_since = None
        step = VERY_LOW if level <= VERY_LOW else LOW if level <= LOW else 101
        if self._battery_step is None or level >= LOW + 5 and not charging:
            self._battery_step = step
        elif step < self._battery_step and not charging:
            self._battery_step = step
            self.toast("Battery at %d%%. Plug in %s." % (level, "soon" if step == VERY_LOW else "to keep going"),
                       RED if step == VERY_LOW else AMBER, 5)

    def close_soon(self):
        """Save, say so, and after a moment leave the screen to the system."""
        self._closing = time.monotonic() + CLOSING_SECONDS
        drive = self.drive if self.wardriving else None
        self.stop_wardrive()
        self.stop_recon()
        saved = drive is not None and drive.log_path is not None and not drive.failed
        self._closing = time.monotonic() + CLOSING_SECONDS
        self.toast("Battery almost empty. %s" % ("Wardrive saved. Closing now." if saved else "Closing now."), RED,
                   CLOSING_SECONDS)

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
        if self._booted:
            self.watch_battery()
            self.watch_nodes()
        self._listen()
        for key in self.keyboard.poll(timeout):
            if not self.screen.text_mode:
                key = navigation(key)
            if not key.repeat:
                self._pressed = (self.screen, key.name)
            elif (self.screen, key.name) != self._pressed or \
                    (key.name not in REPEATING and not (self.screen.text_mode and key.char)):
                continue
            if key == "R" and not isinstance(self.screen, (recon_screens.Recon, screens.Modal)) \
                    and recon_screens.fresh_alert(self):
                self.open("Recon")
            else:
                self.screen.handle(key, self)
            self.dirty = True
            if self._deaf or not self.running:
                break
        self._listen()

    def _listen(self):
        """Keys pressed while a notice held the screen were meant for nothing on it."""
        if self._deaf:
            self._deaf = False
            getattr(self.keyboard, "flush", lambda: None)()

    def finish_last(self):
        """Finish the log of a wardrive that a crash or a power cut ended, then let the uploader look at the logs."""
        try:
            if engine.leftovers(self.logs_dir) and \
                    engine.recover(self.logs_dir, engine.ignored_names(self.cfg.get("ignore", ()))):
                self.call_soon(lambda: self.toast("Finished the log of your last wardrive.", GREEN, 5))
        except Exception:
            faults.note("Finishing the last wardrive")
        if self.running:
            self.uploader.start()

    def run(self):
        self.hw.start()
        self.nodes.start()
        self._finisher = threading.Thread(target=self.finish_last, daemon=True, name="finish-last")
        self._finisher.start()
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
        if self.flashing():
            self.flash_job.wait(FLASH_WAIT)
        if self._finisher is not None:
            self._finisher.join(FINISH_WAIT)
        self.uploader.stop()
        self.nodes.stop()
        self.hw.stop()
