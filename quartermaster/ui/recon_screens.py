"""Recon screens: the live list of flagged devices, device details and a signal finder."""

import time

from .. import recon
from . import icons
from .gfx import (AMBER, BG, CONTENT_BOTTOM, CYAN, FAINT, GOLD, GOLD_INK, GREEN, LINE, MUTED, RED, SURFACE, SURFACE_2,
                  TEXT, W, ago, bearing, clock, compass, distance_m, font, human_distance, rssi_color)
from .screens import TOP, Details, ListMixin, Screen, empty_state, fmt_rssi, gps_text, plural

PINK = (255, 120, 200)
CATEGORY_COLOR = {
    "flock": RED, "alpr": RED, "axon": RED, "ring": AMBER, "camera": AMBER, "glasses": AMBER, "drone": CYAN,
    "airtag": PINK, "tracker": PINK, "ibeacon": MUTED, "raven": AMBER, "skimmer": AMBER, "hacker": (180, 140, 255),
}
GRADE_COLOR = {recon.LOW: MUTED, recon.MEDIUM: AMBER, recon.STRONG: RED}
ADDRESS_KIND = {0: "public", 1: "random", 2: "public identity", 3: "random identity"}
OPERATOR_KIND = {0: "take-off point", 1: "live operator", 2: "fixed point"}


def category_pill(c, x, y, category, width=None):
    label = recon.CATEGORY_SHORT.get(category, "?")
    f = font("bold", 7)
    w = width or int(c.text_width(label, f)) + 8
    c.rect((x, y, x + w, y + 11), fill=CATEGORY_COLOR.get(category, MUTED), radius=3)
    c.text_center(x + w / 2, y + 2, label, f, BG)
    return w


def grade_dots(c, x, y, confidence):
    color = GRADE_COLOR.get(confidence, FAINT)
    for i in range(3):
        c.rect((x + i * 5, y, x + i * 5 + 3, y + 7), fill=color if i < confidence else LINE, radius=1)


def away(here, there):
    return "%s %s of you" % (human_distance(distance_m(here, there)), compass(bearing(here, there)))


def device_title(device):
    name = device.get("name") or ""
    if device["transport"] == "wifi":
        return name or "(hidden network)"
    return name or device.get("vendor") or device.get("label") or device["address"]


class Recon(Screen, ListMixin):
    title = "RECON"
    refresh = 0.5
    VISIBLE = 6
    SORTS = ("threat", "signal", "recent")

    def __init__(self):
        self.index = self.top = 0
        self.show_all = False
        self.sort = 0

    def hints(self, app):
        if app.recon_running:
            return [("Space", "Stop"), ("OK", "Details"), ("A", "Flagged" if self.show_all else "All"),
                    ("S", self.SORTS[self.sort].title()), ("Esc", "Menu")]
        return [("Space", "Start"), ("T", "Watch list"), ("Esc", "Menu")]

    def render(self, c, app):
        app.recon.unseen_alerts = 0
        if app.recon_running:
            self.live(c, app)
        else:
            self.ready(c, app)

    def ready(self, c, app):
        cfg, r = app.cfg, app.recon
        y = TOP
        c.rect((4, y, W - 4, CONTENT_BOTTOM - 30), fill=SURFACE, radius=6)
        icons.draw(c, "eye", W - 46, y + 8, 32, LINE)
        watching = [k for k in recon.CATEGORIES if k in cfg.get("recon_categories", recon.CATEGORIES)]
        fix = app.gps.snapshot()
        radios = len(app.hw.adapters)
        rows = [("Radios", "%s + Bluetooth" % plural(radios, "Wi-Fi adapter") if radios else "Bluetooth only", TEXT),
                ("Watching", "%d of %d kinds of device" % (len(watching), len(recon.CATEGORIES)), TEXT),
                ("Show", "%s and stronger" % recon.CONFIDENCE_NAMES[r.display_min()].lower(), TEXT),
                ("Alert", "%s and stronger%s" % (recon.CONFIDENCE_NAMES[r.alert_min()].lower(),
                                                 ", with sound" if cfg.get("recon_sound", True) else ""), TEXT),
                ("GPS", gps_text(fix), GREEN if fix.get("valid") else AMBER),
                ("Log", "Flagged devices, in quartermaster/recon", MUTED)]
        for i, (label, value, color) in enumerate(rows):
            c.text((12, y + 6 + i * 13), label, font("ui", 9), MUTED)
            c.text((70, y + 6 + i * 13), value, font("mono", 9), color, width=W - 126 if i < 3 else W - 84)
        by = CONTENT_BOTTOM - 25
        c.rect((4, by, W - 4, CONTENT_BOTTOM - 3), fill=GOLD, radius=6)
        c.text_center(W // 2, by + 5, "SPACE  ·  START RECON", font("display", 11), GOLD_INK)

    def live(self, c, app):
        r = app.recon
        summary = r.summary()
        y = TOP - 1
        tiles = [("FLAGGED", summary["flagged"], AMBER if summary["flagged"] else TEXT),
                 ("STRONG", summary["strong"], RED if summary["strong"] else TEXT),
                 ("WI-FI", summary["wifi"], TEXT), ("BLUETOOTH", summary["ble"], TEXT)]
        width = (W - 8 - 9) / 4
        for i, (label, value, color) in enumerate(tiles):
            x = 4 + i * (width + 3)
            c.rect((x, y, x + width, y + 27), fill=SURFACE, radius=5)
            c.text((x + 7, y + 3), label, font("bold", 7), MUTED)
            c.text((x + 7, y + 11), str(value), font("display", 13), color)
        c.text_right(W - 10, y + 15, clock(r.elapsed()), font("mono", 8), FAINT)
        y += 31
        errors = sorted(set(r.errors.values()))
        if errors:
            c.text((8, y), errors[0], font("ui", 8), AMBER, width=W - 16)
            y += 11
        devices = r.device_list(self.show_all, self.SORTS[self.sort])
        if not devices:
            c.text_center(W // 2, y + 16, "Nothing flagged nearby" if not self.show_all else "No devices heard yet",
                          font("bold", 11), TEXT)
            c.text_center(W // 2, y + 32, "Listening on Wi-Fi and Bluetooth. Flagged devices appear here.",
                          font("ui", 8), MUTED)
            if summary["wifi"] or summary["ble"]:
                heard = summary["wifi"] + summary["ble"]
                c.text_center(W // 2, y + 45, "Press A to list all %d devices heard." % heard, font("ui", 8), FAINT)
            return
        visible = self.VISIBLE = max(1, (CONTENT_BOTTOM - y) // 16)
        self.clamp(len(devices), visible)
        now = r.clock()
        for row, device in enumerate(devices[self.top:self.top + visible]):
            yy = y + row * 16
            stale = now - device["last_seen"] > 60
            if self.top + row == self.index:
                c.select(yy, 15)
            x = 9
            if device.get("category"):
                x += category_pill(c, x, yy + 2, device["category"], width=46) + 4
            else:
                c.text((x, yy + 3), "—", font("mono", 8), FAINT)
                x += 50
            wifi = device["transport"] == "wifi"
            c.text((x, yy + 3), "W" if wifi else "B", font("monob", 8), GOLD if wifi else CYAN)
            x += 10
            title = ("FOLLOWING  " if device.get("follow") else "") + device_title(device)
            c.text((x, yy + 2), title, font("ui", 10), RED if device.get("follow") else FAINT if stale else TEXT,
                   width=W - x - 98)
            if device.get("category"):
                grade_dots(c, W - 96, yy + 4, device.get("confidence", 0))
            c.signal_bars(W - 76, yy + 3, None if stale else device.get("rssi"))
            c.text_right(W - 34, yy + 3, fmt_rssi(device.get("rssi")), font("mono", 9),
                         FAINT if stale else rssi_color(device.get("rssi")))
            c.text_right(W - 12, yy + 3, ago(now - device["last_seen"]), font("mono", 8), MUTED)
        self.scrollbar(c, len(devices), visible, y0=y)

    def handle(self, key, app):
        r = app.recon
        running = app.recon_running
        devices = r.device_list(self.show_all, self.SORTS[self.sort]) if running else []
        if key == "SPACE":
            app.toggle_recon()
        elif running and self.move(key, len(devices), self.VISIBLE):
            pass
        elif key == "ENTER" and devices:
            device = devices[min(self.index, len(devices) - 1)]
            app.push(ReconDetail(device["transport"], device["address"]))
        elif key == "A" and running:
            self.show_all = not self.show_all
            self.index = self.top = 0
        elif key == "S" and running:
            self.sort = (self.sort + 1) % len(self.SORTS)
            self.index = self.top = 0
        elif key == "T" and not running:
            app.push(Categories())
        else:
            return super().handle(key, app)
        return True


class ReconDetail(Details):
    def __init__(self, transport, address):
        super().__init__("DEVICE")
        self.key = (transport, address)

    def hints(self, app):
        return [("OK", "Find it"), ("F/X", "Scroll"), ("Esc", "Back")]

    def rows(self, app):
        device = app.recon.device(*self.key)
        if device is None:
            return [("", "This device is no longer in the live list.", MUTED)]
        now = app.recon.clock()
        rows = []
        if device.get("category"):
            rows.append(("Kind", recon.CATEGORY_LABELS[device["category"]],
                         CATEGORY_COLOR.get(device["category"], TEXT)))
            rows.append(("Evidence", recon.CONFIDENCE_NAMES[device["confidence"]],
                         GRADE_COLOR.get(device["confidence"], TEXT)))
            if device.get("evidence"):
                rows.append(("", device["evidence"], MUTED))
            if device.get("vendor"):
                rows.append(("Vendor", device["vendor"]))
        else:
            rows.append(("Kind", "Not flagged", MUTED))
        if device.get("follow"):
            rows.append(("Following", "Heard at several places you have been, over ten minutes or more", RED))
        if device["transport"] == "wifi":
            radio = (device.get("channel") or "?", device.get("frequency") or "?", device.get("security") or "?")
            rows += [("Name", device.get("name") or "(hidden)"), ("Address", device["address"]),
                     ("Channel", "%s  ·  %s MHz  ·  %s" % radio)]
        else:
            rows += [("Name", device.get("name") or "—"),
                     ("Address", "%s (%s)" % (device["address"], ADDRESS_KIND.get(device.get("address_type"), "?")))]
            if device.get("address_type") in (1, 3):
                rows.append(("", "Random addresses change, so this device may appear again as a new row.", FAINT))
        signal = (fmt_rssi(device.get("rssi")), fmt_rssi(device.get("best_rssi")))
        rows.append(("Signal", "%s dBm now, best %s dBm" % signal, rssi_color(device.get("rssi"))))
        heard = (device["count"], ago(now - device["last_seen"]), ago(now - device["first_seen"]))
        rows.append(("Heard", "%d times, last %s ago, first %s ago" % heard))
        if device.get("best_fix"):
            rows.append(("Strongest at", "%.5f, %.5f" % device["best_fix"]))
        beacon = device.get("beacon")
        if beacon:
            u = beacon["uuid"]
            ids = (beacon["major"], beacon["minor"], beacon["measured_power"])
            rows += [("UUID", "%s-%s-%s-%s-%s" % (u[:8], u[8:12], u[12:16], u[16:20], u[20:])),
                     ("Major/minor", "%d / %d  ·  %d dBm at 1 m" % ids)]
        rid = device.get("remote_id")
        if rid:
            rows += self.drone_rows(app, rid)
        rows.append(("", "A match is broadcast evidence, not proof of recording, intent or ownership.", FAINT))
        return rows

    @staticmethod
    def drone_rows(app, rid):
        rows = []
        fix = app.gps.snapshot()
        here = (fix["latitude"], fix["longitude"]) if fix.get("valid") else None
        if rid.get("has_id"):
            rows.append(("Drone ID", rid["id"], CYAN))
        if rid.get("has_location"):
            there = (rid["latitude"], rid["longitude"])
            rows.append(("Aircraft", "%.5f, %.5f (self-reported)" % there, CYAN))
            if here:
                rows.append(("", away(here, there), CYAN))
            extra = []
            if rid.get("has_altitude"):
                extra.append("altitude %.0f m" % rid["altitude_m"])
            if rid.get("has_speed"):
                extra.append("%.1f m/s" % rid["speed_mps"])
            if extra:
                rows.append(("", ", ".join(extra), MUTED))
        if rid.get("has_operator"):
            there = (rid["operator_latitude"], rid["operator_longitude"])
            kind = OPERATOR_KIND.get(rid.get("operator_location_type"), "operator")
            rows.append(("Operator", "%.5f, %.5f (%s)" % (there[0], there[1], kind), AMBER))
            if here:
                rows.append(("", away(here, there), AMBER))
        return rows

    def handle(self, key, app):
        if key in ("ENTER", "T"):
            app.push(Finder(*self.key))
            return True
        return super().handle(key, app)


class Finder(Screen):
    """A large signal meter for walking towards a device."""

    title = "FIND"
    refresh = 0.25

    def __init__(self, transport, address):
        self.key = (transport, address)
        self.peak = None

    def hints(self, app):
        return [("R", "Reset peak"), ("Esc", "Back")]

    def render(self, c, app):
        device = app.recon.device(*self.key)
        if device is None:
            empty_state(c, "eye", "Device lost", "It has not been heard for a while.")
            return
        now = app.recon.clock()
        history = [(t, v) for t, v in device["rssi_history"] if v is not None and now - t < 30]
        rssi = device.get("rssi") if now - device["last_seen"] < 15 else None
        if rssi is not None and (self.peak is None or rssi > self.peak):
            self.peak = rssi
        y = TOP
        x = 8
        if device.get("category"):
            x += category_pill(c, 8, y + 1, device["category"]) + 6
        c.text((x, y), device_title(device), font("bold", 11), TEXT, width=W - x - 8)
        y += 18
        big = font("display", 40)
        color = rssi_color(rssi) if rssi is not None else FAINT
        c.text((10, y), fmt_rssi(rssi), big, color)
        c.text((14 + c.text_width(fmt_rssi(rssi), big), y + 26), "dBm", font("bold", 10), MUTED)
        trend, tint = "—", MUTED
        if len(history) >= 4:
            half = len(history) // 2
            old = sum(v for _, v in history[:half]) / half
            new = sum(v for _, v in history[half:]) / (len(history) - half)
            if new - old > 2:
                trend, tint = "WARMER", GREEN
            elif old - new > 2:
                trend, tint = "COLDER", AMBER
            else:
                trend = "STEADY"
        c.text_right(W - 10, y + 4, trend, font("display", 14), tint)
        c.text_right(W - 10, y + 24, "peak %s dBm" % fmt_rssi(self.peak), font("mono", 9), MUTED)
        beacon = device.get("beacon")
        estimate = recon.distance_from_rssi(rssi, beacon["measured_power"]) if beacon else None
        if estimate is not None:
            c.text_right(W - 10, y + 36, "≈ %s (rough)" % human_distance(estimate), font("mono", 9), FAINT)
        y += 54
        level = 0 if rssi is None else max(0.0, min(1.0, (rssi + 110) / 70))
        c.progress((10, y, W - 10, y + 10), level, fg=color if rssi is not None else LINE)
        y += 16
        height = CONTENT_BOTTOM - y - 2
        c.rect((10, y, W - 10, y + height), fill=SURFACE, radius=4)
        for (t0, v0), (t1, v1) in zip(history, history[1:]):
            x0 = 12 + (W - 24) * (1 - (now - t0) / 30.0)
            x1 = 12 + (W - 24) * (1 - (now - t1) / 30.0)
            y0 = y + height - 2 - (height - 4) * max(0, min(1, (v0 + 110) / 70))
            y1 = y + height - 2 - (height - 4) * max(0, min(1, (v1 + 110) / 70))
            c.line((x0, y0, x1, y1), fill=GOLD, width=2)
        c.text((14, y + 2), "LAST 30 s", font("bold", 7), FAINT)

    def handle(self, key, app):
        if key == "R":
            self.peak = None
            return True
        return super().handle(key, app)


class Categories(Screen, ListMixin):
    title = "WATCH LIST"
    VISIBLE = 8

    def __init__(self):
        self.index = self.top = 0

    def hints(self, app):
        return [("OK", "On / off"), ("A", "All on"), ("Esc", "Back")]

    def render(self, c, app):
        enabled = set(app.cfg.get("recon_categories", recon.CATEGORIES))
        names = list(recon.CATEGORIES)
        self.clamp(len(names), self.VISIBLE)
        for row, name in enumerate(names[self.top:self.top + self.VISIBLE]):
            y = TOP + row * 16
            if self.top + row == self.index:
                c.select(y, 15)
            on = name in enabled
            category_pill(c, 10, y + 2, name, width=56)
            c.text((72, y + 2), recon.CATEGORY_LABELS[name], font("ui", 10), TEXT if on else FAINT, width=180)
            c.rect((W - 44, y + 3, W - 18, y + 12), fill=GOLD if on else SURFACE_2, radius=5)
            c.draw.ellipse((W - 30 if on else W - 43, y + 4, W - 21 if on else W - 34, y + 11),
                           fill=GOLD_INK if on else MUTED)
        self.scrollbar(c, len(names), self.VISIBLE)

    def handle(self, key, app):
        names = list(recon.CATEGORIES)
        if self.move(key, len(names), self.VISIBLE):
            return True
        enabled = app.cfg.get("recon_categories", recon.CATEGORIES)
        if key in ("ENTER", "SPACE"):
            chosen = names[self.index]
            app.change("recon_categories", [k for k in names if (k in enabled) != (k == chosen)])
        elif key == "A":
            app.change("recon_categories", names)
        else:
            return super().handle(key, app)
        return True


def fresh_alert(app):
    """The newest Recon alert while it is still worth interrupting for."""
    r = app.recon
    if r.state != "running" or not r.alerts or not r.unseen_alerts:
        return None
    alert = r.alerts[0]
    return alert if r.clock() - alert["time"] <= 10 else None


def alert_banner(c, app):
    """A fresh Recon alert, shown over every screen except Recon itself."""
    r = app.recon
    alert = fresh_alert(app)
    if alert is None:
        return
    strong = alert["confidence"] >= recon.STRONG or alert.get("follow")
    fill, edge = ((70, 12, 14), RED) if strong else ((60, 44, 8), AMBER)
    y = TOP - 2
    c.rect((4, y, W - 4, y + 28), fill=fill, outline=edge, radius=6)
    blink = int(time.monotonic() * 3) % 2 == 0
    icons.draw(c, "alert", 10, y + 5, 18, edge if blink else tuple(v * 2 for v in fill))
    c.text((34, y + 3), ("FOLLOWING?  " if alert.get("follow") else "") + alert["label"], font("bold", 10), TEXT,
           width=W - 110)
    detail = "%s  ·  %s" % (alert.get("name") or alert["address"], recon.CONFIDENCE_NAMES[alert["confidence"]])
    c.text((34, y + 15), detail, font("ui", 8), (230, 220, 210), width=W - 110)
    c.text_right(W - 12, y + 3, "%s dBm" % fmt_rssi(alert.get("rssi")), font("monob", 9), edge)
    more = r.unseen_alerts - 1
    c.text_right(W - 12, y + 15, "R to view" if more < 1 else "+%d  ·  R" % more, font("mono", 8), MUTED)
