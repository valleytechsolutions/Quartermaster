"""Screens. Each draws into the content area and handles logical keys.

Esc goes back everywhere, the arrow keys move and OK opens or confirms.
"""

import calendar
import re
import time

from .. import __version__, accounts, config, qr, recon, uploads, wigle
from ..tracker import BLE, BT, WIFI
from . import icons, splash
from .gfx import (AMBER, BAND_COLOR, BG, CONTENT_BOTTOM, CONTENT_TOP, CYAN, FAINT, GOLD, GOLD_DIM, GOLD_INK, GREEN,
                  LINE, MUTED, RED, SURFACE, SURFACE_2, TEXT, W, clock, font, image, rssi_color)

TOP = CONTENT_TOP + 3
WHITE = (255, 255, 255)
SECURITY_COLOR = {"OPEN": RED, "WEP": RED, "WPA": AMBER, "OWE": CYAN}
STAMP = re.compile(r"quartermaster_(\d{8})-(\d{6})")
SITE_SHORT = {"wigle": "WiGLE", "wdgwars": "WDG", "wardrift": "DRIFT"}
REPO = "github.com/valleytechsolutions/Quartermaster"


class Screen:
    title = ""
    refresh = 1.0
    chrome = True
    text_mode = False

    def hints(self, app):
        return [("Esc", "Back")]

    def render(self, c, app):
        raise NotImplementedError

    def handle(self, key, app):
        if key in ("ESC", "BACKSPACE"):
            app.pop()
            return True
        return False

    def closed(self, app):
        """Called once when the screen leaves the stack."""


class Modal(Screen):
    pass


class ListMixin:
    index = 0
    top = 0

    def move(self, key, count, visible):
        if not count:
            return False
        step = {"UP": -1, "DOWN": 1, "PAGEUP": -visible, "PAGEDOWN": visible}.get(key.name)
        if step is None:
            return False
        self.index = max(0, min(count - 1, self.index + step)) if abs(step) > 1 else (self.index + step) % count
        self.clamp(count, visible)
        return True

    def clamp(self, count, visible):
        self.index = max(0, min(self.index, max(0, count - 1)))
        if self.index < self.top:
            self.top = self.index
        if self.index >= self.top + visible:
            self.top = self.index - visible + 1
        self.top = max(0, min(self.top, max(0, count - visible)))

    def scrollbar(self, c, count, visible, x=W - 4, y0=TOP, y1=CONTENT_BOTTOM - 2):
        if count <= visible:
            return
        c.rect((x, y0, x + 1, y1), fill=LINE)
        span = y1 - y0
        height = max(8, span * visible // count)
        y = y0 + (span - height) * self.top // max(1, count - visible)
        c.rect((x - 1, y, x + 2, y + height), fill=MUTED, radius=1)


def empty_state(c, icon, title, detail):
    icons.draw(c, icon, W // 2 - 14, 40, 28, LINE)
    c.text_center(W // 2, 76, title, font("bold", 12), TEXT)
    for i, line in enumerate(c.wrap(detail, 280, font("ui", 10))[:4]):
        c.text_center(W // 2, 94 + i * 13, line, font("ui", 10), MUTED)


def fmt_rssi(value):
    return "—" if value is None else "%d" % round(value)


def log_epoch(name):
    match = STAMP.match(name)
    if not match:
        return None
    try:
        return calendar.timegm(time.strptime(match.group(1) + match.group(2), "%Y%m%d%H%M%S"))
    except ValueError:
        return None


def when(epoch):
    return time.strftime("%b %d  %H:%M", time.localtime(epoch)) if epoch else "Unknown time"


def size_text(size):
    if size >= 1024 * 1024:
        return "%.1f MB" % (size / 1048576)
    return "%d KB" % max(1, size // 1024)


def line_count(path, headers):
    try:
        with open(path, "rb") as source:
            return max(0, sum(1 for _ in source) - headers)
    except OSError:
        return None


def plural(count, word):
    return "%d %s%s" % (count, word, "" if count == 1 else "s")


def gps_text(fix):
    if fix.get("valid"):
        text = "%.5f, %.5f" % (fix["latitude"], fix["longitude"])
        if fix.get("satellites"):
            text += "  %d sats" % fix["satellites"]
        return text
    if fix.get("source", "none") == "none":
        return "No GPS found"
    return "Searching for a fix"


class Splash(Screen):
    chrome = False
    refresh = 0.04

    def __init__(self):
        self.started = None

    def hints(self, app):
        return []

    def render(self, c, app):
        if self.started is None:
            self.started = time.monotonic()
        c.image.paste(splash.frame(min(1.0, (time.monotonic() - self.started) / splash.SECONDS)), (0, 0))

    def done(self):
        return self.started is not None and time.monotonic() - self.started >= splash.SECONDS + splash.HOLD

    def handle(self, key, app):
        app.pop()
        return True


class Home(Screen):
    title = "QUARTERMASTER"
    TILES = [("W", "Wardrive", "wifi"), ("R", "Recon", "eye"), ("L", "Logs", "folder"), ("U", "Uploads", "cloud"),
             ("H", "Hardware", "chip"), ("S", "Settings", "sliders"), ("K", "Keys", "key"), ("A", "About", "anchor")]
    COLUMNS = 4

    def __init__(self):
        self.index = 0

    def hints(self, app):
        return [("OK", "Open"), ("Space", "Stop wardrive" if app.wardriving else "Start wardrive"), ("Letter", "Jump")]

    def subtitle(self, label, app):
        if label == "Wardrive":
            if app.wardriving:
                return "REC " + clock(app.drive.elapsed()), RED
            radios = len(app.hw.adapters)
            return (plural(radios, "radio") if radios else "BLE only"), MUTED
        if label == "Recon":
            if app.recon_running:
                summary = app.recon.summary()
                if summary["flagged"]:
                    return "%d flagged" % summary["flagged"], RED if summary["strong"] else AMBER
                return "Watching", GREEN
            return "Surveillance", MUTED
        if label == "Logs":
            count = len(app.log_files())
            return ("%d saved" % count if count else "None yet"), MUTED
        if label == "Uploads":
            status = app.upload_status()
            if status["uploading"]:
                return "Sending", GOLD
            if not status["keys"]:
                return "Add keys", AMBER if status["files"] else MUTED
            if status["waiting"]:
                return "%d waiting" % status["waiting"], AMBER
            return ("All sent", GREEN) if status["files"] else ("Ready", MUTED)
        if label == "Hardware":
            if app.hw.stalled:
                return "Check adapter", AMBER
            fix = app.gps.snapshot()
            if fix.get("valid"):
                return "GPS fix", GREEN
            return ("No GPS", AMBER) if fix.get("source", "none") == "none" else ("GPS searching", AMBER)
        if label == "Settings":
            return "Scan and log", MUTED
        if label == "Keys":
            return "%d of 3 set" % len(accounts.ready(app.accounts())), MUTED
        return "v" + __version__, MUTED

    def render(self, c, app):
        gap, x0 = 4, 4
        tw = (W - 2 * x0 - gap * (self.COLUMNS - 1)) // self.COLUMNS
        th = 63
        for i, (letter, label, icon) in enumerate(self.TILES):
            x, y = x0 + (i % self.COLUMNS) * (tw + gap), TOP + (i // self.COLUMNS) * (th + gap)
            selected = i == self.index
            text, color = self.subtitle(label, app)
            live = (label == "Wardrive" and app.wardriving) or (label == "Recon" and app.recon_running)
            c.rect((x, y, x + tw, y + th), fill=SURFACE_2 if selected else SURFACE, outline=GOLD if selected else LINE,
                   radius=6)
            icons.draw(c, "record" if live and label == "Wardrive" else icon, x + 7, y + 7, 18,
                       color if live else (GOLD if selected else TEXT))
            c.text((x + tw - 7, y + 6), letter, font("monob", 9), GOLD if selected else FAINT, anchor="ra")
            c.text((x + 7, y + 32), label, font("bold", 11), TEXT, width=tw - 10)
            c.text((x + 7, y + 48), text, font("mono", 8), color, width=tw - 10)

    def handle(self, key, app):
        count = len(self.TILES)
        if key.name in ("LEFT", "RIGHT"):
            self.index = (self.index + (1 if key == "RIGHT" else -1)) % count
        elif key.name in ("UP", "DOWN"):
            self.index = (self.index + self.COLUMNS) % count
        elif key == "ENTER":
            app.open(self.TILES[self.index][1])
        elif key == "SPACE":
            if app.wardriving:
                app.ask_stop_wardrive()
            else:
                app.start_wardrive()
        elif key.name in [tile[0] for tile in self.TILES]:
            self.index = next(i for i, tile in enumerate(self.TILES) if tile[0] == key.name)
            app.open(self.TILES[self.index][1])
        elif key in ("ESC", "BACKSPACE", "Q"):
            busy = app.wardriving or app.recon_running
            app.confirm("Quit Quartermaster?", "The scan stops and its log is saved." if busy
                        else "Finished logs keep uploading next time.", "Quit", app.quit, danger=True)
        else:
            return False
        return True


class Wardrive(Screen):
    title = "WARDRIVE"
    refresh = 0.25
    NEXT = {"all": "wifi", "wifi": "ble", "ble": "all"}
    LABEL = {"all": "All", "wifi": "Wi-Fi", "ble": "Bluetooth"}
    KINDS = {"all": None, "wifi": (WIFI,), "ble": (BLE, BT)}

    def __init__(self, app):
        self.filter = app.cfg.get("live_list", "all")

    def hints(self, app):
        return [("OK", "Stop + save"), ("Tab", self.LABEL[self.filter]), ("Esc", "Menu")]

    @staticmethod
    def warning(app, s):
        fix = s["gps"]
        if s["error"]:
            return s["error"], RED
        if not fix.get("valid"):
            if fix.get("source", "none") == "none":
                return "No GPS found. Nothing is logged until one has a fix.", AMBER
            return "Waiting for a GPS fix. Nothing is logged yet.", AMBER
        if s["gps_weak"]:
            return "GPS fix is too weak. Logging waits for a better one.", AMBER
        errors = ["%s: %s" % (a["interface"], a["error"]) for a in s["adapters"] if a.get("error")]
        if errors:
            return errors[0], AMBER
        if app.hw.stalled:
            return "%s is not starting. See Hardware." % app.hw.stalled[0]["label"], AMBER
        if s["ble_error"]:
            return s["ble_error"], AMBER
        return None

    def render(self, c, app):
        drive = app.drive
        if drive is None:
            return
        s = drive.status()
        y = TOP - 1
        counters = (("WI-FI", s["wifi"], GOLD), ("BLUETOOTH", s["ble"], CYAN), ("LOGGED", s["rows"], GREEN))
        for i, (label, value, color) in enumerate(counters):
            x = 4 + i * 105
            c.rect((x, y, x + 101, y + 43), fill=SURFACE, radius=5)
            c.text((x + 6, y + 2), label, font("ui", 8), MUTED)
            text = "{:,}".format(value)
            size = 30 if len(text) <= 5 else 24 if len(text) <= 6 else 19
            c.put((x + 6, y + 41), text, font("display", size), color, "ls")
        y += 46
        x = 4
        for band in ("2.4", "5", "6"):
            x += c.pill(x, y, "%s GHz %d" % (band, s["bands"].get(band, 0)), font("monob", 8), BG, BAND_COLOR[band],
                        height=11) + 4
        c.text_right(W - 4, y + 1, plural(len(s["adapters"]), "radio"), font("ui", 8), MUTED)
        y += 14
        warning = self.warning(app, s)
        if warning:
            c.text((5, y - 1), warning[0], font("ui", 9), warning[1], width=W - 10)
            y += 12
        devices = drive.tracker.recent((CONTENT_BOTTOM - y) // 13, self.KINDS[self.filter])
        for device in devices:
            wifi = device.kind == WIFI
            c.tag(4, y + 1, (device.band or "?") if wifi else "BT", BAND_COLOR.get(device.band, GOLD) if wifi else CYAN,
                  21)
            name = device.name or ("(hidden)" if wifi else device.mac)
            c.text((29, y), name, font("ui", 10), FAINT if device.ignored else TEXT if device.name else MUTED,
                   width=150)
            if device.ignored:
                c.text((184, y + 1), "IGNORED", font("monob", 8), FAINT)
            elif wifi:
                c.text((184, y + 1), device.security, font("monob", 8), SECURITY_COLOR.get(device.security, MUTED))
                c.text_right(248, y + 1, "ch%s" % (device.channel or "?"), font("mono", 8), MUTED)
            c.text_right(W - 24, y + 1, fmt_rssi(device.rssi) if device.rssi is not None else "", font("mono", 8),
                         rssi_color(device.rssi))
            c.signal_bars(W - 20, y + 1, device.rssi, rssi_color(device.rssi))
            y += 13
        if not devices:
            c.text_center(W // 2, y + 14, "Listening…", font("ui", 11), MUTED)

    def handle(self, key, app):
        if key in ("ENTER", "S"):
            app.ask_stop_wardrive()
        elif key.name in ("TAB", "LEFT", "RIGHT"):
            self.filter = self.NEXT[self.filter]
        else:
            return super().handle(key, app)
        return True


class Summary(Screen):
    def __init__(self, status):
        self.s = status
        self.title = "WARDRIVE SAVED" if status["log"] else "WARDRIVE ENDED"

    def hints(self, app):
        return [("Esc", "Menu"), ("L", "Logs"), ("U", "Uploads"), ("W", "New wardrive")]

    def render(self, c, app):
        s = self.s
        files = s.get("files", 0)
        rows = [("Wi-Fi networks", "{:,}".format(s["wifi"])), ("Bluetooth devices", "{:,}".format(s["ble"])),
                ("2.4 / 5 / 6 GHz", "%d / %d / %d" % tuple(s["bands"].get(b, 0) for b in ("2.4", "5", "6"))),
                ("WiGLE rows", "{:,}".format(s["rows"])), ("Time", clock(s["elapsed"])),
                ("Saved as", (s["log"].rsplit("/", 1)[-1][14:29] + ("  +%d more" % (files - 1) if files > 1 else ""))
                 if s["log"] else "Nothing: GPS never had a fix")]
        for i, (label, value) in enumerate(rows):
            y = TOP + i * 17
            c.text((8, y), label, font("ui", 10), MUTED)
            c.text_right(W - 8, y, value, font("bold", 10), TEXT if i < 5 or s["log"] else AMBER)
            c.line((8, y + 14, W - 8, y + 14))
        status = app.upload_status()
        if not s["log"]:
            text, color = "A GPS fix is needed before rows can be logged.", MUTED
        elif not status["keys"]:
            text, color = "Press K to add your upload keys.", AMBER
        elif not status["auto"]:
            text, color = "Automatic uploads are off. Press U to send it.", MUTED
        else:
            text, color = "Uploading to %s." % ", ".join(
                info["name"] for info in status["services"].values() if info["configured"]), GREEN
        c.text((8, TOP + 106), text, font("ui", 9), color, width=W - 16)

    def handle(self, key, app):
        if key == "L":
            app.open("Logs")
        elif key == "U":
            app.open("Uploads")
        elif key == "K":
            app.open("Keys")
        elif key == "W":
            app.start_wardrive()
        elif key in ("ESC", "BACKSPACE", "ENTER"):
            app.home()
        else:
            return False
        return True


class Hardware(Screen):
    title = "HARDWARE"
    VISIBLE = 5

    def __init__(self):
        self.top = 0

    def hints(self, app):
        return [("F/X", "Scroll"), ("Esc", "Back")]

    def cards(self, app):
        scanning = {}
        if app.wardriving:
            scanning = {a["interface"]: a for a in app.drive.status()["adapters"]}
        cards = []
        for item in app.hw.stalled:
            cards.append({"tag": "!", "color": AMBER, "title": item["label"], "edge": AMBER,
                          "detail": "On USB but not starting. Replug it or use a powered hub.", "detail_color": AMBER})
        for adapter in app.hw.adapters:
            live = scanning.get(adapter["interface"])
            card = {"tag": "USB" if adapter["external"] else "INT", "color": GOLD if adapter["external"] else MUTED,
                    "title": "%s  %s" % (adapter["interface"], adapter["label"]), "bands": adapter["bands"],
                    "right": adapter["band_label"]}
            if live and live.get("error"):
                card.update(bands=(), detail=live["error"], detail_color=AMBER, right="")
            elif live:
                card["right"] = plural(live.get("sweeps") or 0, "sweep")
            elif not adapter["bands"]:
                card.update(detail="No usable bands reported", detail_color=AMBER, right="")
            cards.append(card)
        if not app.hw.adapters:
            cards.append({"tag": "WIFI", "color": AMBER, "title": "No Wi-Fi adapter found",
                          "detail": "Bluetooth still scans. Plug in a USB adapter any time."})
        fix = app.gps.snapshot()
        source = fix.get("source", "none")
        plugged = [port for port in app.hw.gps_ports if port.get("kind") == "usb" and port.get("likely")]
        if app.cfg.get("gps_source") == "off":
            title, detail = "GPS is off in Settings", "Rows cannot be logged without a position."
        elif source != "none":
            title, detail = source, gps_text(fix) if fix.get("valid") else "No fix yet. It needs a view of the sky."
        elif plugged:
            title, detail = plugged[0]["label"] + " found", "Waiting for it to send a position."
        else:
            title, detail = "No GPS found", "Plug in a USB GPS, the LoRa cap or a Grove GPS."
        cards.append({"tag": "GPS", "color": GREEN if fix.get("valid") else AMBER, "title": title, "detail": detail,
                      "right": "HDOP %.1f" % fix["hdop"] if fix.get("valid") and isinstance(
                          fix.get("hdop"), (int, float)) else ""})
        for radio in app.hw.bluetooth or [None]:
            if radio is None:
                cards.append({"tag": "BT", "color": AMBER, "title": "No Bluetooth adapter found",
                              "detail": "Bluetooth devices cannot be logged."})
            else:
                cards.append({"tag": "BT", "color": CYAN, "title": "%s  %s" % (radio["name"], radio["label"]),
                              "detail": "Scans Bluetooth LE and classic" if app.cfg.get("ble", True)
                              else "Bluetooth scan is off in Settings"})
        return cards

    def render(self, c, app):
        cards = self.cards(app)
        self.top = max(0, min(self.top, len(cards) - self.VISIBLE))
        for i, card in enumerate(cards[self.top:self.top + self.VISIBLE]):
            y = TOP - 2 + i * 27
            c.rect((4, y, W - 9, y + 25), fill=SURFACE, outline=card.get("edge"), radius=5)
            c.tag(8, y + 2, card["tag"], card["color"], 25)
            c.text((37, y + 1), card["title"], font("bold", 10), TEXT, width=W - 52)
            right = card.get("right") or ""
            if right:
                c.text_right(W - 14, y + 14, right, font("ui", 8), MUTED)
            x = 37
            for band in card.get("bands", ()):
                x += c.pill(x, y + 15, band + " GHz", font("monob", 8), BG, BAND_COLOR[band], height=8) + 3
            if card.get("detail"):
                room = W - 20 - x - (int(c.text_width(right, font("ui", 8))) + 6 if right else 0)
                c.text((x, y + 14), card["detail"], font("ui", 8), card.get("detail_color", MUTED), width=room)
        if len(cards) > self.VISIBLE:
            span = CONTENT_BOTTOM - 2 - TOP
            height = max(8, span * self.VISIBLE // len(cards))
            c.rect((W - 4, TOP, W - 3, CONTENT_BOTTOM - 2), fill=LINE)
            y = TOP + (span - height) * self.top // (len(cards) - self.VISIBLE)
            c.rect((W - 5, y, W - 2, y + height), fill=MUTED, radius=1)

    def handle(self, key, app):
        if key.name in ("UP", "DOWN"):
            self.top = max(0, self.top + (1 if key == "DOWN" else -1))
            return True
        return super().handle(key, app)


def upload_mark(c, x, y, mark):
    """One dot per site: green sent, amber waiting, red refused, hollow when no key is saved."""
    box = (x - 4, y, x + 4, y + 8)
    if mark == "done":
        c.draw.ellipse(box, fill=GREEN)
    elif mark == "rejected":
        c.draw.ellipse(box, fill=RED)
    elif mark == "waiting":
        c.draw.ellipse(box, outline=AMBER, width=2)
    else:
        c.draw.ellipse(box, outline=LINE, width=1)


class Logs(Screen, ListMixin):
    title = "LOGS"
    VISIBLE = 7
    refresh = 2.0
    COLUMNS = (222, 256, 290)

    def __init__(self):
        self.index = self.top = 0

    def hints(self, app):
        return [("OK", "Details"), ("U", "Upload now"), ("F/X", "Move"), ("Esc", "Back")]

    def render(self, c, app):
        files = app.log_files()
        if not files:
            empty_state(c, "folder", "No wardrives saved yet",
                        "Start a wardrive from the menu. Each one is saved as a WiGLE file in quartermaster/logs.")
            return
        c.text((11, TOP - 1), plural(len(files), "log"), font("bold", 7), FAINT)
        for x, service in zip(self.COLUMNS, uploads.SERVICES):
            c.text_center(x, TOP - 1, SITE_SHORT[service], font("bold", 7), FAINT)
        self.clamp(len(files), self.VISIBLE)
        saved = app.accounts()
        for row, entry in enumerate(files[self.top:self.top + self.VISIBLE]):
            y = TOP + 10 + row * 17
            if self.top + row == self.index:
                c.select(y, 15)
            c.text((11, y + 2), when(entry["epoch"]), font("mono", 10), TEXT)
            if entry["active"]:
                c.pill(116, y + 2, "REC", font("monob", 8), WHITE, (150, 24, 28), height=11)
                continue
            c.text_right(196, y + 3, size_text(entry["size"]), font("mono", 9), MUTED)
            marks = app.uploader.marks(entry["name"], saved)
            for x, service in zip(self.COLUMNS, uploads.SERVICES):
                upload_mark(c, x, y + 4, marks[service])
        self.scrollbar(c, len(files), self.VISIBLE, y0=TOP + 10)

    def handle(self, key, app):
        files = app.log_files()
        if self.move(key, len(files), self.VISIBLE):
            return True
        if key == "ENTER" and files:
            app.push(LogDetail(files[self.index]))
        elif key == "U":
            app.upload_now()
        else:
            return super().handle(key, app)
        return True


class Details(Screen, ListMixin):
    """Scrollable list of labelled values."""

    VISIBLE = 10

    def __init__(self, title):
        self.title = title
        self.index = self.top = 0

    def rows(self, app):
        return []

    def hints(self, app):
        return [("F/X", "Scroll"), ("Esc", "Back")]

    def layout(self, c, app):
        out = []
        for label, value, *rest in self.rows(app):
            color = rest[0] if rest else TEXT
            lines = c.wrap(str(value), W - (96 if label else 22), font("mono", 9)) or [""]
            out.append((label, lines[0], color))
            out.extend((None if label else "", line, color) for line in lines[1:])
        return out

    def render(self, c, app):
        rows = self.layout(c, app)
        self.top = max(0, min(self.top, max(0, len(rows) - self.VISIBLE)))
        for i, (label, value, color) in enumerate(rows[self.top:self.top + self.VISIBLE]):
            y = TOP + i * 13
            if label:
                c.text((8, y), label, font("ui", 9), MUTED)
            c.text((84 if (label or label is None) else 8, y), value, font("mono", 9), color)
        self.index = self.top
        self.scrollbar(c, len(rows), self.VISIBLE)

    def handle(self, key, app):
        step = {"UP": -1, "DOWN": 1, "PAGEUP": -self.VISIBLE, "PAGEDOWN": self.VISIBLE}.get(key.name)
        if step is not None:
            self.top = max(0, self.top + step)
            return True
        return super().handle(key, app)


class LogDetail(Details):
    def __init__(self, entry):
        super().__init__("LOG")
        self.entry = entry
        self.counts = None

    def hints(self, app):
        return [("U", "Upload again"), ("D", "Delete"), ("Esc", "Back")]

    def rows(self, app):
        entry = self.entry
        path = entry["path"]
        if self.counts is None:
            self.counts = (wigle.data_rows(path),
                           line_count(path.with_name(path.name.replace(".wigle.csv", ".summary.csv")), 1))
        rows = [("Recorded", when(entry["epoch"])), ("File", entry["name"]), ("Size", size_text(entry["size"]))]
        if self.counts[0] is not None:
            rows.append(("Rows", "{:,}".format(self.counts[0])))
        if self.counts[1] is not None:
            rows.append(("Devices", "{:,}".format(self.counts[1])))
        if path == app.active_log():
            rows.append(("", "Still recording. It is sent after the wardrive stops.", AMBER))
            return rows
        marks = app.uploader.marks(entry["name"], app.accounts())
        for service in uploads.SERVICES:
            record = app.uploader.ledger.entry(entry["name"], service)
            name = accounts.SERVICE_NAMES[service]
            if marks[service] == "done":
                rows.append((name, record.get("detail") or "Sent", GREEN))
            elif marks[service] == "rejected":
                rows.append((name, record.get("detail") or "Refused", RED))
            elif marks[service] == "waiting":
                rows.append((name, record.get("detail") or "Waiting to be sent", AMBER))
            else:
                rows.append((name, "No key saved", FAINT))
        rows.append(("", "Download it from the phone setup page, under Keys.", FAINT))
        return rows

    def handle(self, key, app):
        if key == "U":
            app.upload_now(self.entry["name"])
        elif key == "D":
            if self.entry["path"] == app.active_log():
                app.toast("Stop the wardrive before deleting its log", AMBER)
            else:
                app.confirm("Delete this log?", "The file is removed from the Cardputer. Sites that already have "
                            "it keep their copy.", "Delete", lambda: app.delete_log(self.entry), danger=True)
        else:
            return super().handle(key, app)
        return True


def test_line(app, service):
    """The latest key check for a site as (text, color), or None."""
    state, detail = app.tests.get(service, ("", ""))
    if not state:
        return None
    color = {"busy": MUTED, uploads.DONE: GREEN, uploads.AUTH: RED}.get(state, AMBER)
    return detail, color


class Uploads(Screen):
    title = "UPLOADS"

    def hints(self, app):
        return [("OK", "Upload now"), ("T", "Test keys"), ("K", "Keys"), ("Esc", "Back")]

    @staticmethod
    def headline(status, now):
        if status["current"]:
            name, service = status["current"]
            return "Sending %s to %s" % (name[14:29], accounts.SERVICE_NAMES[service]), GOLD
        if not status["keys"]:
            return "Add a key to start uploading", AMBER
        if not status["files"]:
            return "No finished logs yet", MUTED
        if status["waiting"]:
            if status["next_try"]:
                wait = clock(max(0, status["next_try"] - now))[3:]
                return "%s waiting. Next try in %s" % (plural(status["waiting"], "upload"), wait), AMBER
            if not status["auto"]:
                return "%s waiting. Press OK to send" % plural(status["waiting"], "upload"), AMBER
            return "%s waiting" % plural(status["waiting"], "upload"), AMBER
        refused = sum(info["refused"] for info in status["services"].values())
        if refused:
            return "%s refused. Open the log to see why" % plural(refused, "upload"), RED
        return "Every log has been sent", GREEN

    def render(self, c, app):
        status = app.upload_status()
        text, color = self.headline(status, app.uploader.clock())
        c.text((8, TOP - 2), text, font("bold", 10), color, width=W - 100)
        c.text_right(W - 8, TOP - 1, "Automatic" if status["auto"] else "Manual", font("ui", 9), MUTED)
        for i, service in enumerate(uploads.SERVICES):
            info = status["services"][service]
            y = TOP + 14 + i * 38
            c.rect((4, y, W - 4, y + 35), fill=SURFACE, radius=5)
            c.text((10, y + 3), info["name"], font("bold", 11), TEXT)
            refused = info["blocked"] or info["last_state"] == uploads.AUTH
            if not info["configured"]:
                label, fg, bg = "NO KEY", MUTED, SURFACE_2
            elif refused:
                label, fg, bg = "KEY REFUSED", WHITE, (150, 24, 28)
            else:
                label, fg, bg = "KEY SAVED", GOLD, GOLD_DIM
            width = int(c.text_width(label, font("monob", 8))) + 8
            c.pill(W - 10 - width, y + 4, label, font("monob", 8), fg, bg, height=11)
            x = 10
            for count, word, tint in ((info["sent"], "sent", GREEN), (info["waiting"], "waiting", AMBER),
                                      (info["refused"], "refused", RED)):
                x += c.text((x, y + 17), "%d %s" % (count, word), font("mono", 9), tint if count else FAINT) + 10
            line = test_line(app, service)
            if line is None:
                line = (info["last"], MUTED) if info["last"] else \
                    (("Ready", FAINT) if info["configured"] else ("Press K to add your key", FAINT))
            c.text_right(W - 10, y + 18, c.fit(line[0], W - 30 - x, font("ui", 8)), font("ui", 8), line[1])

    def handle(self, key, app):
        if key == "ENTER":
            app.upload_now()
        elif key == "T":
            ready = accounts.ready(app.accounts())
            if not ready:
                app.toast("Add a key first: press K", AMBER)
            for service in ready:
                app.test_key(service)
        elif key == "K":
            app.push(Keys())
        else:
            return super().handle(key, app)
        return True


FIELD_SERVICE = {field: service for service, fields in accounts.SERVICES.items() for field in fields}


class Keys(Screen, ListMixin):
    title = "KEYS"
    VISIBLE = 5

    def __init__(self):
        self.index = self.top = 0

    def hints(self, app):
        if self.index == 0:
            return [("OK", "Open"), ("F/X", "Move"), ("Esc", "Back")]
        return [("OK", "Type it"), ("T", "Test"), ("D", "Remove"), ("Esc", "Back")]

    def render(self, c, app):
        saved = app.accounts()
        for row in range(len(accounts.FIELDS) + 1):
            y = TOP + row * 17
            if row == self.index:
                c.select(y, 15)
            if row == 0:
                icons.draw(c, "phone", 10, y + 2, 12, GOLD)
                c.text((26, y + 2), "Set up from a phone or PC", font("bold", 10), GOLD)
                c.text_right(W - 14, y + 2, "Easiest  ›", font("ui", 9), MUTED)
                continue
            field, label, _ = accounts.FIELDS[row - 1]
            value = saved.get(field, "")
            c.text((11, y + 2), label, font("ui", 10), TEXT)
            c.text_right(W - 14, y + 3, accounts.masked(value) if value else "Not set", font("mono", 9),
                         GREEN if value else FAINT)
        y = TOP + 5 * 17 + 4
        c.line((8, y, W - 8, y))
        if self.index == 0:
            lines = [("Scan a QR code, then paste all your keys in a web page.", MUTED),
                     ("Or pick a key below and type it on the Cardputer.", FAINT)]
        else:
            field, _, where = accounts.FIELDS[self.index - 1]
            lines = [("Find it at " + where, MUTED)]
            line = test_line(app, FIELD_SERVICE[field])
            lines.append(line or ("Press T to check that the key works.", FAINT))
        for i, (text, color) in enumerate(lines):
            c.text((10, y + 5 + i * 13), text, font("ui", 9), color, width=W - 20)

    def handle(self, key, app):
        if self.move(key, len(accounts.FIELDS) + 1, self.VISIBLE):
            return True
        if self.index == 0:
            if key == "ENTER":
                app.push(PhoneSetup(app))
                return True
            return super().handle(key, app)
        field, label, where = accounts.FIELDS[self.index - 1]
        service = FIELD_SERVICE[field]
        if key == "ENTER":
            app.push(TextInput(label, lambda value: app.save_key(field, value), hint="Find it at " + where,
                               limit=accounts.MAX_LENGTH))
        elif key == "T":
            if accounts.configured(app.accounts(), service):
                app.test_key(service)
            else:
                app.toast("Save the %s key first" % accounts.SERVICE_NAMES[service], AMBER)
        elif key == "D":
            if app.accounts().get(field):
                app.confirm("Remove this key?", "%s is erased from the Cardputer." % label, "Remove",
                            lambda: app.save_key(field, ""), danger=True)
        else:
            return super().handle(key, app)
        return True


class PhoneSetup(Screen):
    title = "PHONE SETUP"
    refresh = 0.5

    def __init__(self, app):
        self.server = None
        self.error = ""
        self.addresses = []
        self.checked = 0.0
        self.code = (None, None)
        self.check(app)

    def hints(self, app):
        return [("Esc", "Close the page")]

    def check(self, app):
        self.checked = time.monotonic()
        self.addresses = app.hw.addresses()
        if self.addresses and self.server is None and not self.error:
            try:
                self.server = app.setup_server().start()
            except OSError as exc:
                self.error = "The setup page could not start: %s" % (exc.strerror or exc)

    def render(self, c, app):
        if time.monotonic() - self.checked > 3:
            self.check(app)
        if self.error:
            return empty_state(c, "phone", "Setup page unavailable", self.error)
        if self.server is None or not self.addresses:
            return empty_state(c, "phone", "Not on a network",
                               "Join Wi-Fi in the CardputerZero settings first. This screen updates by itself. "
                               "You can also type keys on the Cardputer.")
        url = "http://%s:%d" % (self.addresses[0], self.server.port)
        link = "%s/#%s" % (url, self.server.pin)
        if self.code[0] != link:
            try:
                self.code = (link, qr.image(link, scale=4, border=2))
            except ValueError:
                self.code = (link, None)
        picture = self.code[1]
        x = 8
        if picture is not None:
            c.paste(picture, (4, CONTENT_TOP + (CONTENT_BOTTOM - CONTENT_TOP - picture.height) // 2 + 1))
            x = 4 + picture.width + 8
        c.text((x, TOP), "Scan with your phone", font("bold", 11), GOLD)
        c.text((x, TOP + 16), "or open this in a browser:", font("ui", 9), MUTED)
        c.text((x, TOP + 29), url, font("bold", 10), TEXT, width=W - x - 4)
        c.text((x, TOP + 48), "PIN", font("ui", 9), MUTED)
        c.text((x, TOP + 58), "%s %s" % (self.server.pin[:3], self.server.pin[3:]), font("display", 24), GOLD)
        if self.server.locked:
            status, color = "Too many wrong PINs. Reopen this screen.", RED
        elif self.server.saves:
            status, color = "Saved from the page ✓", GREEN
        elif self.server.visits:
            status, color = "Page opened", GREEN
        else:
            status, color = "Waiting for your phone…", MUTED
        c.text((x, TOP + 91), status, font("ui", 9), color, width=W - x - 4)
        for i, line in enumerate(c.wrap("Use the same Wi-Fi as the Cardputer. The page closes when you leave "
                                        "this screen.", W - x - 6, font("ui", 8))[:3]):
            c.text((x, TOP + 105 + i * 10), line, font("ui", 8), FAINT)

    def closed(self, app):
        if self.server is not None:
            self.server.stop()
            self.server = None


class Settings(Screen, ListMixin):
    title = "SETTINGS"
    VISIBLE = 7
    WHILE_SCANNING = {key for section, entries in config.SECTIONS if section in ("SCANNING", "LOGGING", "GPS")
                      for key, _, _, _ in entries} - {"ignore"}

    def __init__(self):
        self.index, self.top = 1, 0
        self.items = []
        for section, entries in config.SECTIONS:
            self.items.append((None, section, None, ""))
            self.items.extend(entries)

    def hints(self, app):
        return [("OK", "Change"), ("Z/C", "Step"), ("F/X", "Move"), ("Esc", "Back")]

    @staticmethod
    def value(app, key):
        if key == "ignore":
            return plural(len(app.cfg.get("ignore", ())), "name")
        if key == "recon_categories":
            return "%d of %d" % (len(app.cfg.get(key, ())), len(recon.CATEGORIES))
        return config.shown(key, app.cfg.get(key))

    def skip_headers(self, direction):
        for _ in range(len(self.items)):
            if self.items[self.index][0] is not None:
                return
            self.index = (self.index + direction) % len(self.items)

    def locked(self, app, key):
        return app.wardriving and key in self.WHILE_SCANNING

    def render(self, c, app):
        self.skip_headers(1)
        self.clamp(len(self.items), self.VISIBLE)
        for row, (key, label, choices, _) in enumerate(self.items[self.top:self.top + self.VISIBLE]):
            y = TOP + row * 15
            if key is None:
                c.text((11, y + 3), label, font("bold", 7), GOLD)
                c.line((16 + c.text_width(label, font("bold", 7)), y + 7, W - 12, y + 7), fill=LINE)
                continue
            selected = self.top + row == self.index
            if selected:
                c.select(y - 1, 14)
            c.text((11, y), label, font("ui", 10), FAINT if self.locked(app, key) else TEXT)
            c.text_right(W - 22, y + 1, self.value(app, key), font("mono", 9), GOLD if selected else MUTED)
            c.text((W - 18, y - 1), "›", font("bold", 11), FAINT)
        self.scrollbar(c, len(self.items), self.VISIBLE, y1=TOP + self.VISIBLE * 15 - 2)
        key, _, _, help_text = self.items[self.index]
        if self.locked(app, key):
            help_text, color = "Stop the wardrive to change this setting.", AMBER
        else:
            color = MUTED
        for i, line in enumerate(c.wrap(help_text, W - 20, font("ui", 9))[:2]):
            c.text((10, TOP + self.VISIBLE * 15 + 3 + i * 11), line, font("ui", 9), color)

    def handle(self, key, app):
        before = self.index
        if self.move(key, len(self.items), self.VISIBLE):
            self.skip_headers(1 if self.index >= before or key.name == "PAGEDOWN" else -1)
            self.clamp(len(self.items), self.VISIBLE)
            return True
        name, label, choices, _ = self.items[self.index]
        if key.name not in ("ENTER", "LEFT", "RIGHT", "SPACE"):
            return super().handle(key, app)
        if name == "ignore":
            app.push(IgnoreList())
        elif name == "recon_categories":
            from .recon_screens import Categories
            app.push(Categories())
        elif self.locked(app, name):
            app.toast("Stop the wardrive to change this", AMBER)
        elif key == "ENTER" and len(choices) > 2:
            app.push(Picker(label, [(value, text, "") for value, text in choices], app.cfg.get(name),
                            lambda value: app.change(name, value)))
        else:
            values = [value for value, _ in choices]
            index = values.index(app.cfg[name]) if app.cfg.get(name) in values else 0
            app.change(name, values[(index + (-1 if key == "LEFT" else 1)) % len(values)])
        return True


class IgnoreList(Screen, ListMixin):
    title = "IGNORED NETWORKS"
    VISIBLE = 7

    def __init__(self):
        self.index = self.top = 0

    def hints(self, app):
        return [("A", "Add"), ("D", "Remove"), ("Esc", "Back")]

    def render(self, c, app):
        entries = app.cfg.get("ignore", [])
        if not entries:
            empty_state(c, "wifi", "Nothing is ignored",
                        "Press A to add a Wi-Fi name or MAC address, such as your home network or phone hotspot. "
                        "It still shows on screen but is never logged or uploaded.")
            return
        self.clamp(len(entries), self.VISIBLE)
        for row, entry in enumerate(entries[self.top:self.top + self.VISIBLE]):
            y = TOP + row * 16
            if self.top + row == self.index:
                c.select(y, 15)
            c.text((11, y + 2), entry, font("ui", 10), TEXT, width=W - 110)
            c.text_right(W - 14, y + 3, "address" if config.is_mac(entry) else "name", font("mono", 9), MUTED)
        self.scrollbar(c, len(entries), self.VISIBLE)
        c.text((10, CONTENT_BOTTOM - 13), "Never logged or uploaded. Paste a long list from the phone setup page.",
               font("ui", 8), FAINT, width=W - 20)

    def handle(self, key, app):
        entries = list(app.cfg.get("ignore", []))
        if self.move(key, len(entries), self.VISIBLE):
            return True
        if key == "A":
            if len(entries) >= config.MAX_IGNORED:
                app.toast("The list is full (%d entries)" % config.MAX_IGNORED, AMBER)
            else:
                app.push(TextInput("Wi-Fi name or MAC address", lambda value: app.ignore(value),
                                   hint="Exactly as it is shown, capitals included.", limit=64))
        elif key == "D" and entries:
            entry = entries[self.index]
            app.confirm("Stop ignoring it?", "%s will be logged again." % entry, "Remove",
                        lambda: app.change("ignore", [item for item in entries if item != entry]))
        else:
            return super().handle(key, app)
        return True


class About(Screen):
    title = "ABOUT"

    def hints(self, app):
        return [("T", "Tips"), ("Esc", "Back")]

    def render(self, c, app):
        c.paste(image("quartermaster-logo-dark.png", (120, 84)), (8, TOP + 3))
        c.text((126, TOP + 4), "Quartermaster", font("display", 17), GOLD)
        c.text((126, TOP + 26), "Version %s" % __version__, font("ui", 10), TEXT)
        c.text((126, TOP + 41), "Wi-Fi + Bluetooth wardriver", font("ui", 9), MUTED)
        c.text((126, TOP + 53), "for the CardputerZero", font("ui", 9), MUTED)
        c.text((126, TOP + 68), REPO, font("ui", 7), FAINT)
        y = CONTENT_BOTTOM - 37
        c.rect((8, y, W - 8, y + 34), fill=WHITE, radius=6)
        c.paste(image("bwm-logo.png", (28, 28)), (16, y + 3))
        c.paste(image("vts-logo.png", (28, 28)), (W - 42, y + 3))
        c.text_center(W // 2, y + 5, "Valleytech Solutions", font("bold", 11), (0, 0, 0))
        c.text_center(W // 2, y + 19, "MIT open source", font("ui", 9), (90, 90, 90))

    def handle(self, key, app):
        if key == "T":
            app.push(Tips())
            return True
        return super().handle(key, app)


class Tips(Details):
    def __init__(self):
        super().__init__("TIPS")

    def rows(self, app):
        return [
            ("", "Keys", GOLD),
            ("F  X  Z  C", "Up, down, left, right. The arrow keys work too."),
            ("OK", "Open or confirm"),
            ("Esc", "Back"),
            ("Sym", "Then a key: the symbol printed on it"),
            ("Tab", "In a text box: lists every symbol"),
            ("Space", "Start or stop a wardrive from the menu"),
            ("", "Wardrive", GOLD),
            ("", "Every Wi-Fi adapter scans at once, with Bluetooth beside them. Rows are logged only with a GPS "
                 "fix, so WiGLE accepts every one.", MUTED),
            ("", "Each device is listed once. Its strongest and weakest sightings are always written, which is "
                 "the closest and the farthest point you heard it from.", MUTED),
            ("", "Before the battery is empty, a running wardrive is stopped and saved by itself.", MUTED),
            ("", "Uploads", GOLD),
            ("", "Add your keys once under Keys. Finished logs are then sent to WiGLE, WDGWars and Wardrift "
                 "whenever the Cardputer is online.", MUTED),
            ("", "Recon", GOLD),
            ("", "Watches for cameras, trackers and other surveillance gear. It never runs together with a "
                 "wardrive.", MUTED),
            ("", "A match is broadcast evidence, not proof of recording, intent or ownership.", FAINT),
        ]


class Picker(Modal, ListMixin):
    VISIBLE = 6

    def __init__(self, title, options, current=None, on_select=None):
        self.title = title
        self.options = options
        self.on_select = on_select
        self.index = next((i for i, option in enumerate(options) if option[0] == current), 0)
        self.top = 0
        self.clamp(len(options), self.VISIBLE)

    def hints(self, app):
        return [("OK", "Select"), ("Esc", "Cancel")]

    def render(self, c, app):
        x0, y0, x1 = 34, 20, W - 34
        y1 = y0 + 28 + min(self.VISIBLE, len(self.options)) * 17
        c.rect((x0, y0, x1, y1), fill=SURFACE, outline=GOLD_DIM, radius=7)
        c.text((x0 + 10, y0 + 6), self.title, font("bold", 11), GOLD, width=x1 - x0 - 20)
        for row, (_, label, detail) in enumerate(self.options[self.top:self.top + self.VISIBLE]):
            y = y0 + 24 + row * 17
            selected = self.top + row == self.index
            if selected:
                c.rect((x0 + 4, y, x1 - 4, y + 15), fill=GOLD, radius=4)
            c.text((x0 + 10, y + 2), label, font("ui", 10), GOLD_INK if selected else TEXT, width=x1 - x0 - 24)
            if detail:
                c.text_right(x1 - 10, y + 3, detail, font("mono", 8), GOLD_INK if selected else MUTED)
        self.scrollbar(c, len(self.options), self.VISIBLE, x=x1 - 3, y0=y0 + 25, y1=y1 - 4)

    def handle(self, key, app):
        if self.move(key, len(self.options), self.VISIBLE):
            return True
        if key == "ENTER":
            app.pop()
            if self.on_select:
                self.on_select(self.options[self.index][0])
            return True
        return super().handle(key, app)


class Confirm(Modal):
    def __init__(self, title, detail, action, callback, danger=False, default=0):
        self.title, self.detail, self.action, self.callback = title, detail, action, callback
        self.danger = danger
        self.choice = default

    def hints(self, app):
        return [("Z/C", "Choose"), ("OK", "Confirm"), ("Esc", "Cancel")]

    def render(self, c, app):
        x0, y0, x1, y1 = 36, 32, W - 36, 136
        c.rect((x0, y0, x1, y1), fill=SURFACE, outline=LINE, radius=8)
        c.text((x0 + 12, y0 + 9), self.title, font("bold", 12), TEXT, width=x1 - x0 - 24)
        for i, line in enumerate(c.wrap(self.detail, x1 - x0 - 24, font("ui", 9))[:4]):
            c.text((x0 + 12, y0 + 28 + i * 12), line, font("ui", 9), MUTED)
        width = (x1 - x0 - 30) // 2
        for i, label in enumerate(("Cancel", self.action)):
            x = x0 + 12 + i * (width + 6)
            selected = i == self.choice
            fill = (RED if self.danger and i == 1 else GOLD) if selected else SURFACE_2
            c.rect((x, y1 - 25, x + width, y1 - 8), fill=fill, radius=5)
            c.text_center(x + width // 2, y1 - 22, label, font("bold", 10),
                          (WHITE if self.danger and i == 1 else GOLD_INK) if selected else TEXT)

    def handle(self, key, app):
        if key.name in ("LEFT", "RIGHT", "TAB", "UP", "DOWN"):
            self.choice = 1 - self.choice
        elif key == "ENTER":
            app.pop()
            if self.choice == 1:
                self.callback()
        elif key == "Y":
            app.pop()
            self.callback()
        elif key in ("N", "ESC", "BACKSPACE"):
            app.pop()
        else:
            return False
        return True


class TextInput(Modal):
    refresh = 0.5
    SYMBOLS = "-_.@:/!#$%&*+=?~()[]{}<>'\"`^|\\,;"
    COLUMNS = 16

    def __init__(self, title, on_submit, initial="", hint="", limit=64):
        self.title, self.on_submit, self.hint, self.limit = title, on_submit, hint, limit
        self.value = initial
        self.picking = False
        self.cell = 0

    @property
    def text_mode(self):
        return not self.picking

    def hints(self, app):
        if self.picking:
            return [("F/X/Z/C", "Move"), ("OK", "Add"), ("Tab", "Close")]
        return [("OK", "Save"), ("Tab", "Symbols"), ("Del", "Erase"), ("Esc", "Cancel")]

    def render(self, c, app):
        x0, y0, x1, y1 = 14, 38, W - 14, 122
        c.rect((x0, y0, x1, y1), fill=SURFACE, outline=GOLD_DIM, radius=8)
        c.text((x0 + 12, y0 + 8), self.title, font("bold", 11), GOLD, width=x1 - x0 - 24)
        c.rect((x0 + 10, y0 + 28, x1 - 10, y0 + 48), fill=BG, outline=LINE, radius=4)
        shown = self.value[-41:]
        width = c.text((x0 + 16, y0 + 32), shown, font("mono", 10), TEXT)
        if int(time.monotonic() * 2) % 2 == 0:
            c.rect((x0 + 17 + width, y0 + 32, x0 + 18 + width, y0 + 44), fill=GOLD)
        if self.picking:
            for i, symbol in enumerate(self.SYMBOLS):
                x, y = x0 + 18 + i % self.COLUMNS * 16, y0 + 52 + i // self.COLUMNS * 15
                if i == self.cell:
                    c.rect((x, y, x + 15, y + 13), fill=GOLD, radius=3)
                c.put((x + 8, y + 7), symbol, font("monob", 12), GOLD_INK if i == self.cell else TEXT, "mm")
            return
        c.text_right(x1 - 12, y0 + 53, "%d typed" % len(self.value), font("mono", 8), FAINT)
        for i, line in enumerate(c.wrap(self.hint, x1 - x0 - 90, font("ui", 8))[:3]):
            c.text((x0 + 12, y0 + 53 + i * 10), line, font("ui", 8), MUTED)

    def insert(self, text):
        if len(self.value) < self.limit:
            self.value += text

    def handle(self, key, app):
        if self.picking:
            return self.pick(key)
        if key == "ENTER":
            app.pop()
            if self.value.strip():
                self.on_submit(self.value.strip())
        elif key == "ESC":
            app.pop()
        elif key == "TAB":
            self.picking = True
        elif key.name in ("BACKSPACE", "DELETE"):
            self.value = self.value[:-1]
        elif key.char and key.char.isprintable():
            self.insert(key.char)
        else:
            return False
        return True

    def pick(self, key):
        step = {"LEFT": -1, "RIGHT": 1, "UP": -self.COLUMNS, "DOWN": self.COLUMNS}.get(key.name)
        if step:
            self.cell = (self.cell + step) % len(self.SYMBOLS)
        elif key == "ENTER":
            self.insert(self.SYMBOLS[self.cell])
            self.picking = False
        elif key in ("TAB", "ESC"):
            self.picking = False
        elif key.name in ("BACKSPACE", "DELETE"):
            self.value = self.value[:-1]
        else:
            return False
        return True
