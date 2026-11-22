"""The Quartermaster interface: boot animation, menu, live wardrive, hardware, settings."""

from functools import lru_cache
import math
import time

from PIL import Image, ImageDraw

from .. import __version__, config, hardware
from ..engine import Wardrive
from ..tracker import WIFI
from .gfx import (AMBER, BAND_COLOR, BG, CONTENT_BOTTOM, CONTENT_TOP, CYAN, FAINT, GOLD, GOLD_DIM, GOLD_INK, GREEN, H,
                  LINE, MUTED, RED, SURFACE, SURFACE_2, TEXT, W, Canvas, clock, font, image, rssi_color)

WHITE = (255, 255, 255)
INK = (0, 0, 0)
SEC_COLOR = {"OPEN": RED, "WEP": RED, "WPA": AMBER, "OWE": CYAN}


def ease(t):
    t = max(0.0, min(1.0, t))
    return 1 - (1 - t) ** 3


SAIL_GOLD = (245, 179, 1)
SAIL_CREAM = (252, 240, 204)
HULL = (196, 138, 0)
NIGHT = (6, 6, 8)
STARS = ((18, 14), (47, 30), (71, 9), (96, 22), (222, 12), (251, 27), (283, 8), (305, 33), (14, 58), (309, 70),
         (140, 6), (190, 9))


@lru_cache(maxsize=None)
def _sail(width, height, color, logo, logo_box):
    """A sail with a logo on it, drawn large and scaled down so its edges are smooth."""
    k = 4
    img = Image.new("RGBA", (width * k, height * k), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    w, h, inset, belly = width * k, height * k, width * k * 0.09, height * k * 0.07
    d.polygon([(inset, 0), (w - inset, 0), (w, h - belly), (w * 0.5, h), (0, h - belly)], fill=color)
    d.line([(inset, k), (w - inset, k)], fill=(0, 0, 0), width=k * 2)
    art = image(logo, (logo_box[0] * k, logo_box[1] * k))
    img.paste(art, ((w - art.width) // 2, int((h - belly - art.height) / 2) + k), art)
    return img.resize((width, height), Image.Resampling.LANCZOS)


@lru_cache(maxsize=None)
def _hull():
    """Raised stern on the left, upswept bow and bowsprit on the right."""
    k = 4
    img = Image.new("RGBA", (300 * k, 46 * k), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    body = [(4, 2), (44, 2), (44, 12), (252, 12), (284, 3), (262, 44), (30, 44)]
    d.polygon([(x * k, y * k) for x, y in body], fill=HULL)
    rail = [(4, 2), (44, 2), (44, 12), (252, 12), (284, 3)]
    d.line([(x * k, y * k) for x, y in rail], fill=SAIL_GOLD, width=3 * k, joint="curve")
    d.line([(276 * k, 5 * k), (299 * k, 0)], fill=SAIL_GOLD, width=2 * k)
    for x in (16, 30):
        d.rectangle((x * k, 7 * k, (x + 6) * k, 12 * k), fill=NIGHT)
    return img.resize((300, 46), Image.Resampling.LANCZOS)


def splash_frame(t):
    """Boot animation at time t (0..1): a black and gold ship sails in, its three sails drop one
    by one carrying the BWM, Quartermaster and VTS marks, and the name appears on the hull."""
    c = Canvas()
    c.rect((0, 0, W, H), fill=NIGHT)
    for i, (x, y) in enumerate(STARS):
        twinkle = 0.55 + 0.45 * math.sin(t * 9 + i * 1.7)
        c.rect((x, y, x + 1, y + 1), fill=tuple(int(v * twinkle) for v in SAIL_GOLD))
    glide = ease(t / 0.3)
    dx = int(-300 * (1 - glide))
    bob = int(round(1.5 * math.sin(t * 10)))
    # masts
    for mx, top in ((67, 30), (160, 8), (253, 30)):
        c.rect((mx - 1 + dx, top + bob, mx + 1 + dx, 108 + bob), fill=HULL)
    flag = [(161 + dx, 8 + bob), (179 + dx, 12 + bob), (161 + dx, 17 + bob)]
    c.draw.polygon(flag, fill=SAIL_GOLD)
    sails = ((33, 36, 68, 66, SAIL_GOLD, "bwm-logo.png", (44, 44), 0.28),
             (109, 14, 102, 90, SAIL_CREAM, "quartermaster-logo.png", (92, 70), 0.40),
             (219, 36, 68, 66, SAIL_GOLD, "vts-logo.png", (44, 44), 0.52))
    for x, y, w, h, color, logo, box, start in sails:
        drop = ease((t - start) / 0.22)
        if drop <= 0:
            continue
        sail = _sail(w, h, color, logo, box)
        shown = sail.crop((0, 0, w, max(1, int(h * drop))))
        c.paste(shown, (x + dx, y + bob))
    c.paste(_hull(), (10 + dx, 96 + bob))
    name = ease((t - 0.72) / 0.2)
    if name > 0:
        f = font("display", 18)
        text, gap = "QUARTERMASTER", 3.0
        width = sum(c.text_width(ch, f) for ch in text) + gap * (len(text) - 1)
        x = (W - width) / 2
        ink = tuple(int(h + (0 - h) * name) for h in HULL)
        for ch in text:
            c.text((x + 2, 113 + bob), ch, f, ink)
            x += c.text_width(ch, f) + gap
    # sea: gold swells in front of the hull
    for row, (base, amp, shade) in enumerate(((141, 2.5, 1.0), (152, 2.0, 0.7), (162, 1.6, 0.45))):
        phase = t * (14 - row * 3) + row * 1.3
        points = [(x, base + amp * math.sin(x / 9.0 + phase)) for x in range(-2, W + 4, 3)]
        if row == 0:
            c.draw.polygon(points + [(W + 4, H), (-2, H)], fill=(20, 14, 0))
        c.draw.line(points, fill=tuple(int(v * shade) for v in SAIL_GOLD), width=2)
    return c.image


class App:
    MENU = [("wardrive", "Wardrive", "Scan Wi-Fi and Bluetooth, log for WiGLE"),
            ("hardware", "Hardware", "Adapters, bands and GPS found"),
            ("logs", "Logs", "Saved wardrives"),
            ("settings", "Settings", "Tune scanning, logging and GPS"),
            ("about", "About", "Version and credits")]

    def __init__(self, display, keyboard, gps, cfg, splash=True, wardrive=Wardrive, clock_fn=time.time):
        self.display, self.keyboard, self.gps, self.cfg = display, keyboard, gps, cfg
        self.wardrive_class = wardrive
        self.now = clock_fn
        self.running = True
        self.screen = "splash" if splash and cfg.get("splash", True) else "menu"
        self.cursor = {"menu": 0, "settings": 0, "logs": 0, "hardware": 0}
        self.drive = None
        self.message = ""
        self.message_until = 0
        self.filter = cfg.get("live_list", "all")
        self.adapters = None
        self.gps_ports = None
        self.splash_started = None

    # -- loop ------------------------------------------------------------
    def run(self):
        if self.cfg.get("auto_start") and self.screen != "splash":
            self.start_wardrive()
        while self.running:
            self.display.show(self.render())
            for key in self.keyboard.poll(0.04 if self.screen == "splash" else 0.25):
                self.handle(key)
        self.stop_wardrive()

    def toast(self, text, seconds=3):
        self.message, self.message_until = text, time.monotonic() + seconds

    def start_wardrive(self):
        self.drive = self.wardrive_class(self.cfg, self.gps)
        try:
            self.drive.start()
            self.screen = "wardrive"
        except (RuntimeError, OSError) as exc:
            self.drive = None
            self.screen = "menu"
            self.toast(str(exc))

    def stop_wardrive(self):
        if self.drive is not None and self.drive.state == "running":
            self.drive.stop()

    # -- input -----------------------------------------------------------
    def handle(self, key):
        name = key.name
        if self.screen == "splash":
            self.screen = "menu"
            if self.cfg.get("auto_start"):
                self.start_wardrive()
            return
        if self.screen == "menu":
            self._move("menu", name, len(self.MENU))
            if name == "ENTER":
                target = self.MENU[self.cursor["menu"]][0]
                if target == "wardrive":
                    if self.drive is not None and self.drive.state == "running":
                        self.screen = "wardrive"
                    else:
                        self.start_wardrive()
                else:
                    if target == "hardware":
                        self.adapters, self.gps_ports = hardware.wifi_adapters(), hardware.gps_candidates()
                    self.screen = target
            elif name == "Q":
                self.running = False
            return
        if self.screen == "wardrive":
            if name in ("ENTER", "S"):
                self.stop_wardrive()
                self.toast("Wardrive saved" if self.drive.log_path else "Stopped. No GPS fix, so nothing was logged", 4)
                self.screen = "summary"
            elif name in ("TAB", "F"):
                self.filter = {"all": "wifi", "wifi": "ble", "ble": "all"}[self.filter]
            elif name == "ESC":
                self.screen = "menu"   # keeps scanning in the background
            return
        if self.screen == "settings":
            self._move("settings", name, len(config.SETTINGS))
            key_name = config.SETTINGS[self.cursor["settings"]][0]
            if name in ("ENTER", "RIGHT", "LEFT", "SPACE"):
                if self.drive is not None and self.drive.state == "running":
                    return self.toast("Stop the wardrive to change settings")
                config.cycle(self.cfg, key_name, -1 if name == "LEFT" else 1)
                try:
                    config.save(self.cfg)
                except OSError:
                    self.toast("Could not save settings")
                if key_name == "gps_source":
                    self.gps.mode = self.cfg["gps_source"]
                if key_name == "live_list":
                    self.filter = self.cfg["live_list"]
        if name == "ESC":
            self.screen = "menu"

    def _move(self, which, name, count):
        if name == "UP":
            self.cursor[which] = (self.cursor[which] - 1) % count
        elif name == "DOWN":
            self.cursor[which] = (self.cursor[which] + 1) % count

    # -- drawing ---------------------------------------------------------
    def render(self):
        if self.screen == "splash":
            if self.splash_started is None:
                self.splash_started = time.monotonic()
            t = (time.monotonic() - self.splash_started) / 3.0
            if t >= 1.6:
                self.handle(type("K", (), {"name": "ENTER"})())
            else:
                return splash_frame(min(1.0, t))
        c = Canvas()
        getattr(self, "draw_" + self.screen)(c)
        if self.message and time.monotonic() < self.message_until:
            f = font("ui", 10)
            w = min(W - 12, int(c.text_width(self.message, f)) + 16)
            c.rect(((W - w) // 2, 132, (W + w) // 2, 150), fill=GOLD, radius=6)
            c.text_center(W // 2, 135, c.fit(self.message, w - 10, f), f, GOLD_INK)
        return c.image

    def header(self, c, title, right=""):
        c.rect((0, 0, W, 18), fill=SURFACE)
        c.paste(image("quartermaster-logo-dark.png", (22, 16)), (3, 1))
        c.text((27, 3), title, font("bold", 11), GOLD)
        fix = self.gps.snapshot()
        gps_text = ("GPS %d sat" % fix.get("satellites", 0)) if fix.get("valid") else (
            "GPS searching" if fix.get("source", "none") != "none" else "NO GPS")
        c.text_right(W - 4, 4, gps_text, font("ui", 9), GREEN if fix.get("valid") else AMBER)
        if right:
            c.text_right(W - 78, 4, right, font("mono", 9), MUTED)

    def footer(self, c, keys):
        c.rect((0, H - 15, W, H), fill=SURFACE)
        x = 4
        for key, label in keys:
            x += c.keycap(x, H - 12, key, label)

    def draw_menu(self, c):
        running = self.drive is not None and self.drive.state == "running"
        self.header(c, "QUARTERMASTER", "SCANNING" if running else "")
        for i, (_, label, hint) in enumerate(self.MENU):
            y = CONTENT_TOP + 3 + i * 25
            selected = i == self.cursor["menu"]
            c.rect((4, y, W - 4, y + 22), fill=GOLD if selected else SURFACE, radius=5)
            c.text((12, y + 2), label, font("bold", 12), GOLD_INK if selected else TEXT)
            c.text_right(W - 12, y + 6, hint, font("ui", 8), GOLD_INK if selected else MUTED)
        self.footer(c, [(";", "up"), (".", "down"), ("OK", "open"), ("Q", "quit")])

    def draw_wardrive(self, c):
        s = self.drive.status()
        self.header(c, "WARDRIVE", clock(s["elapsed"]))
        # counters
        y = CONTENT_TOP + 2
        for i, (label, value, color) in enumerate((("WI-FI", s["wifi"], GOLD), ("BLUETOOTH", s["ble"], CYAN),
                                                    ("LOGGED", s["rows"], GREEN))):
            x = 4 + i * 105
            c.rect((x, y, x + 101, y + 30), fill=SURFACE, radius=5)
            c.text((x + 6, y + 2), label, font("ui", 8), MUTED)
            c.text((x + 6, y + 10), "{:,}".format(value), font("display", 17), color)
        x = 4
        y += 33
        for band in ("2.4", "5", "6"):
            x += c.pill(x, y, "%s GHz %d" % (band, s["bands"].get(band, 0)), font("monob", 8), BG, BAND_COLOR[band],
                        height=11) + 4
        radios = len(s["adapters"])
        note = "%d radio%s" % (radios, "" if radios == 1 else "s")
        if not s["gps"].get("valid"):
            note = "waiting for GPS: not logging yet"
        elif s["ble_error"]:
            note = s["ble_error"]
        c.text_right(W - 4, y + 1, note, font("ui", 8), AMBER if "GPS" in note or s["ble_error"] else MUTED, )
        # live list
        y += 14
        kind = {"wifi": WIFI, "all": None}.get(self.filter, "BLE")
        devices = [d for d in self.drive.tracker.recent(40) if self.filter == "all" or (d.kind == WIFI) == (kind == WIFI)]
        rows = (CONTENT_BOTTOM - y) // 13
        for device in devices[:rows]:
            wifi = device.kind == WIFI
            c.tag(4, y + 1, device.band if wifi and device.band else ("BT" if not wifi else "?"),
                  BAND_COLOR.get(device.band, GOLD) if wifi else CYAN, 21)
            name = device.name or ("(hidden)" if wifi else device.mac)
            c.text((29, y), name, font("ui", 10), TEXT if device.name else MUTED, width=150)
            if wifi:
                c.text((184, y + 1), device.security, font("monob", 8), SEC_COLOR.get(device.security, MUTED))
                c.text_right(248, y + 1, "ch%s" % (device.channel or "?"), font("mono", 8), MUTED)
            c.text_right(W - 24, y + 1, "%d" % device.rssi if device.rssi is not None else "", font("mono", 8),
                         rssi_color(device.rssi))
            c.signal_bars(W - 20, y + 1, device.rssi, rssi_color(device.rssi))
            y += 13
        if not devices:
            c.text_center(W // 2, y + 18, "Listening…", font("ui", 11), MUTED)
        self.footer(c, [("OK", "stop + save"), ("TAB", {"all": "all", "wifi": "wi-fi", "ble": "bluetooth"}[self.filter]),
                        ("`", "menu")])

    def draw_summary(self, c):
        s = self.drive.status()
        self.header(c, "WARDRIVE SAVED" if s["log"] else "WARDRIVE ENDED")
        rows = [("Wi-Fi networks", "{:,}".format(s["wifi"])), ("Bluetooth devices", "{:,}".format(s["ble"])),
                ("2.4 / 5 / 6 GHz", "%d / %d / %d" % tuple(s["bands"].get(b, 0) for b in ("2.4", "5", "6"))),
                ("WiGLE rows", "{:,}".format(s["rows"])), ("Time", clock(s["elapsed"])),
                ("File", s["log"].rsplit("/", 1)[-1] if s["log"] else "none (no GPS fix)")]
        self._table(c, rows)
        self.footer(c, [("`", "menu")])

    def _table(self, c, rows, top=CONTENT_TOP + 4):
        for i, (label, value) in enumerate(rows):
            y = top + i * 18
            c.text((8, y), label, font("ui", 10), MUTED)
            c.text_right(W - 8, y, c.fit(value, 230, font("mono", 9) if len(value) > 24 else font("bold", 10)), font("bold", 10), TEXT)
            c.line((8, y + 15, W - 8, y + 15))

    def draw_hardware(self, c):
        self.header(c, "HARDWARE")
        y = CONTENT_TOP + 3
        for adapter in (self.adapters or [])[:3]:
            c.rect((4, y, W - 4, y + 28), fill=SURFACE, radius=5)
            c.tag(8, y + 3, "USB" if adapter["external"] else "INT", GOLD if adapter["external"] else MUTED, 25)
            c.text((37, y + 2), "%s  %s" % (adapter["interface"], adapter["label"]), font("bold", 10), TEXT, width=W - 46)
            x = 8
            for band in adapter["bands"]:
                x += c.pill(x, y + 15, band + " GHz", font("monob", 8), BG, BAND_COLOR[band], height=10) + 3
            c.text_right(W - 9, y + 15, adapter["band_label"] + ("  monitor" if adapter["monitor"] else ""),
                         font("ui", 8), MUTED)
            y += 31
        if not self.adapters:
            c.text((8, y), "No Wi-Fi adapter found", font("ui", 10), AMBER)
            y += 16
        fix = self.gps.snapshot()
        c.rect((4, y, W - 4, y + 28), fill=SURFACE, radius=5)
        c.tag(8, y + 3, "GPS", GREEN if fix.get("valid") else AMBER, 25)
        ports = self.gps_ports or []
        source = fix.get("source", "none")
        c.text((37, y + 2), source if source != "none" else (ports[0]["label"] + " found" if ports else "No GPS found"),
               font("bold", 10), TEXT, width=W - 46)
        detail = ("%.5f, %.5f  %d sats" % (fix["latitude"], fix["longitude"], fix.get("satellites", 0))
                  if fix.get("valid") else "No fix yet. Needs a view of the sky.")
        c.text((8, y + 15), detail, font("ui", 8), MUTED)
        self.footer(c, [("`", "back")])

    def draw_settings(self, c):
        self.header(c, "SETTINGS")
        cursor = self.cursor["settings"]
        first = max(0, min(cursor - 3, len(config.SETTINGS) - 7))
        for i, (key, label, _, _) in enumerate(config.SETTINGS[first:first + 7]):
            y = CONTENT_TOP + 2 + i * 16
            selected = first + i == cursor
            if selected:
                c.rect((4, y, W - 4, y + 15), fill=GOLD_DIM, radius=4)
            c.text((9, y + 1), label, font("ui", 10), TEXT)
            c.text_right(W - 9, y + 1, config.shown(key, self.cfg.get(key)), font("bold", 10), GOLD if selected else MUTED)
        c.rect((0, H - 28, W, H - 15), fill=BG)
        c.text((6, H - 27), config.SETTINGS[cursor][3], font("ui", 8), FAINT, width=W - 12)
        self.footer(c, [(";", "up"), (".", "down"), ("OK", "change"), ("`", "back")])

    def draw_logs(self, c):
        from ..engine import DEFAULT_ROOT
        self.header(c, "LOGS")
        files = sorted((DEFAULT_ROOT / "logs").glob("quartermaster_*.wigle.csv"), reverse=True)
        y = CONTENT_TOP + 4
        for path in files[:7]:
            c.text((8, y), path.name.replace("quartermaster_", "").replace(".wigle.csv", ""), font("mono", 10), TEXT)
            c.text_right(W - 8, y, "%d KB" % max(1, path.stat().st_size // 1024), font("mono", 10), MUTED)
            y += 16
        if not files:
            c.text_center(W // 2, 70, "No wardrives saved yet", font("ui", 11), MUTED)
        c.text((6, H - 27), "Saved in %s" % (DEFAULT_ROOT / "logs"), font("ui", 8), FAINT, width=W - 12)
        self.footer(c, [("`", "back")])

    def draw_about(self, c):
        self.header(c, "ABOUT")
        c.paste(image("quartermaster-logo-dark.png", (120, 84)), (8, 28))
        c.text((122, 34), "Quartermaster", font("display", 17), GOLD)
        c.text((122, 56), "Version %s" % __version__, font("ui", 10), TEXT)
        c.text((122, 72), "Wi-Fi + Bluetooth wardriver", font("ui", 9), MUTED)
        c.text((122, 86), "for the CardputerZero", font("ui", 9), MUTED)
        c.rect((8, 116, W - 8, 150), fill=WHITE, radius=6)
        c.paste(image("bwm-logo.png", (28, 28)), (16, 119))
        c.paste(image("vts-logo.png", (28, 28)), (W - 42, 119))
        c.text_center(W // 2, 121, "Valleytech Solutions", font("bold", 11), INK)
        c.text_center(W // 2, 135, "MIT open source", font("ui", 9), (90, 90, 90))
        self.footer(c, [("`", "back")])
