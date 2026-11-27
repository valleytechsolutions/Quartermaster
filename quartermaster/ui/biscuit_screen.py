"""Settings > Biscuit node: pair with a Biscuit or Biscuit Pro and run as its 6 GHz node."""

from .. import biscuit
from .gfx import AMBER, CONTENT_BOTTOM, CYAN, FAINT, GOLD, GREEN, MUTED, RED, SURFACE, TEXT, W, clock, font
from .screens import TOP, Screen

WORDS = {
    biscuit.STARTING: ("Starting", "Putting the 6 GHz adapter in node mode.", GOLD),
    biscuit.PAIRING: ("Pairing", "Looking for Biscuits on channel 6.", GOLD),
    biscuit.SEARCHING: ("Searching", "Looking for your Biscuit on channel 6.", AMBER),
    biscuit.JOINED: ("Joined", "Start a wardrive on the Biscuit to scan 6 GHz for it.", GOLD),
    biscuit.SCANNING: ("Scanning", "Scanning 6 GHz for the Biscuit.", GREEN),
    biscuit.SECURE: ("Secure mode", "Set the Biscuit to Extended node mode.", RED),
    biscuit.OLD: ("Old firmware", "This Biscuit takes no 6 GHz nodes. Update it.", AMBER),
    biscuit.STOPPING: ("Stopping", "Putting the adapter back.", GOLD),
    biscuit.FAILED: ("Stopped", "Node mode stopped.", RED),
    biscuit.OFF: ("Off", "", MUTED),
}
BUSY = "Stop the wardrive and Recon first: node mode scans 6 GHz and nothing else."
HELP = ("Set your Biscuit or Biscuit Pro to Extended node mode, then press OK. This reports to it as a Pager 6 GHz "
        "node and scans 6 GHz only, with a 6 GHz USB adapter.")


def summary(app):
    """The word Settings shows for node mode, and its colour."""
    link = app.biscuit
    if link.running:
        word, _, color = WORDS.get(link.state, WORDS[biscuit.FAILED])
        return word, color
    return ("Paired", MUTED) if link.mac else ("Not paired", MUTED)


class BiscuitNode(Screen):
    title = "BISCUIT NODE"
    refresh = 0.5

    def __init__(self):
        self.index = 0
        self.start_after = False

    def hints(self, app):
        link = app.biscuit
        if link.running and link.pairing_mode:
            return [("OK", "Pair"), ("Esc", "Cancel")]
        if link.running:
            return [("OK", "Stop node"), ("Esc", "Back")]
        if not link.mac:
            return [("OK", "Search"), ("Esc", "Back")]
        return [("OK", "Start node"), ("P", "Pair again"), ("D", "Forget"), ("Esc", "Back")]

    def ready(self, app):
        link = app.biscuit
        link.device, link.diy = ("pi" if app.square else "cardputer"), bool(app.square)
        if app.wardriving or app.recon_running:
            app.toast(BUSY, AMBER, 4)
            return False
        return True

    def pair(self, app):
        if self.ready(app):
            self.index = 0
            app.biscuit.start(pair=True)

    def tick(self, app):
        link = app.biscuit
        if self.start_after and link.state in (biscuit.OFF, biscuit.FAILED):
            self.start_after = False
            if self.ready(app):
                link.start()

    def pick(self, app):
        found = app.biscuit.status()["found"]
        if not found:
            return
        chosen = found[min(self.index, len(found) - 1)]
        if chosen["secure"]:
            app.toast("That Biscuit is in Secure node mode. Set it to Extended node mode first.", AMBER, 5)
            return
        app.biscuit.pair_with(chosen["mac"], chosen["name"])
        app.biscuit.stop()
        self.start_after = True
        app.toast("Paired with %s" % chosen["name"], GREEN)

    def handle(self, key, app):
        link = app.biscuit
        if key.name in ("UP", "DOWN") and link.running and link.pairing_mode:
            count = len(link.found)
            if count:
                self.index = (self.index + (1 if key == "DOWN" else -1)) % count
            return True
        if key == "ENTER" and not key.repeat:
            if link.running and link.pairing_mode:
                self.pick(app)
            elif link.running:
                link.stop()
            elif not link.mac:
                self.pair(app)
            elif self.ready(app):
                link.start()
            return True
        if key == "P" and not link.running:
            self.pair(app)
            return True
        if key == "D" and not link.running and link.mac:
            app.confirm("Forget %s?" % link.name, "Pair again to feed a Biscuit.", "Forget", link.forget,
                        danger=True)
            return True
        if key in ("ESC", "BACKSPACE") and link.running and link.pairing_mode:
            link.stop()
            return True
        return super().handle(key, app)

    def paragraph(self, c, text, y, color, size=10, x=10, width=W - 20, lines=4):
        face = font("ui", size)
        for line in c.wrap(text, width, face)[:lines]:
            c.text((x, y), line, face, color)
            y += size + 3
        return y

    def render(self, c, app):
        self.tick(app)
        link = app.biscuit
        s = link.status()
        if link.running and link.pairing_mode:
            self.render_pairing(c, s)
        elif link.running:
            self.render_node(c, app, s)
        else:
            self.render_off(c, app, s)

    def square(self, ui, c, app):
        """The layout for a square HAT: one column, the words wrapped to the screen."""
        self.tick(app)
        link = app.biscuit
        s = link.status()
        y = ui.top + 3
        if link.running and link.pairing_mode:
            found = s["found"]
            y = ui.paragraph(c, "Looking for Biscuits…", y, GOLD, face=ui.small)
            if not found:
                ui.paragraph(c, "Set the Biscuit to Extended node mode.", y + 2, MUTED, face=ui.small)
                return "K2 cancel"
            self.index = min(self.index, len(found) - 1)
            ui.rows(c, ["%s%s" % (entry["name"], " (Secure)" if entry["secure"] else "") for entry in found],
                    self.index, start=y + 2)
            return "OK pair · K2 cancel"
        if link.running:
            word, detail, color = WORDS.get(s["state"], WORDS[biscuit.FAILED])
            y = ui.paragraph(c, word, y, color, face=ui.bold)
            for label, key in (("6 GHz heard", "heard"), ("Sent", "sent"), ("Waiting", "queued")):
                y = ui.paragraph(c, "%s  %s" % (label, format(s.get(key) or 0, ",")), y + 1, TEXT)
            if s.get("channel"):
                y = ui.paragraph(c, "6 GHz channel %d" % s["channel"], y + 1, MUTED, face=ui.small)
            ui.paragraph(c, detail, y + 2, MUTED, face=ui.small)
            return "OK stop · K2 back"
        if link.mac:
            y = ui.paragraph(c, link.name, y, GOLD, face=ui.bold)
            text = "OK starts node mode: it reports as a Pager 6 GHz node and scans 6 GHz only."
            foot = "OK start · K3 actions"
        else:
            y = ui.paragraph(c, "Pair with a Biscuit", y, GOLD, face=ui.bold)
            text = "Set your Biscuit or Biscuit Pro to Extended node mode, then press OK. Needs a 6 GHz USB adapter."
            foot = "OK search · K2 back"
        if s["error"] and link.state == biscuit.FAILED:
            y = ui.paragraph(c, s["error"], y + 2, RED, face=ui.small)
        ui.paragraph(c, text, y + 3, TEXT, face=ui.small)
        return foot

    def render_off(self, c, app, s):
        link = app.biscuit
        y = TOP + 2
        if link.mac:
            c.text((10, y), link.name, font("display", 17), GOLD, width=W - 20)
            c.text((10, y + 24), "Paired · " + link.mac, font("mono", 8), MUTED)
            y += 40
            text = ("OK starts node mode. The 6 GHz USB adapter reports to the Biscuit as a Pager 6 GHz node and "
                    "scans 6 GHz only until you stop it.")
        else:
            c.text((10, y), "Pair with a Biscuit", font("display", 17), GOLD)
            y += 26
            text = HELP
        if s["error"] and link.state == biscuit.FAILED:
            y = self.paragraph(c, s["error"], y, RED, 9, lines=3) + 3
        self.paragraph(c, text, y, TEXT, 10, lines=max(1, (CONTENT_BOTTOM - y) // 13))

    def render_pairing(self, c, s):
        found = s["found"]
        c.text((10, TOP + 2), "Looking for Biscuits on channel 6…", font("bold", 10), GOLD)
        if not found:
            self.paragraph(c, "Set the Biscuit or Biscuit Pro to Extended node mode. It shows up here when it "
                           "answers.", TOP + 22, MUTED, 10)
            return
        self.index = min(self.index, len(found) - 1)
        for i, entry in enumerate(found[:7]):
            y = TOP + 20 + i * 16
            if i == self.index:
                c.select(y - 1, 15)
            word = "Secure mode" if entry["secure"] else "6 GHz ready" if entry["ap6"] else "Old firmware"
            color = RED if entry["secure"] else GREEN if entry["ap6"] else AMBER
            c.text((12, y), "%s  %s" % (entry["name"], entry["mac"][-8:]), font("ui", 10), TEXT, width=W - 110)
            c.text_right(W - 12, y + 1, word, font("ui", 9), color)

    def render_node(self, c, app, s):
        word, detail, color = WORDS.get(s["state"], WORDS[biscuit.FAILED])
        tiles = [("6 GHz heard", s.get("heard", 0), GOLD), ("Sent", s.get("sent", 0), GREEN),
                 ("Updates", s.get("updates", 0), CYAN), ("Waiting", s.get("queued", 0), MUTED)]
        tw, th = 76, (CONTENT_BOTTOM - TOP - 8) // 2
        for i, (label, value, tint) in enumerate(tiles):
            x, y = 6 + i % 2 * (tw + 4), TOP + 2 + i // 2 * (th + 4)
            c.rect((x, y, x + tw, y + th), fill=SURFACE, radius=4)
            c.rect((x, y, x + 2, y + th), fill=tint, radius=1)
            c.text((x + 7, y + 3), label, font("ui", 8), MUTED)
            c.text((x + 7, y + 16), format(value or 0, ","), font("display", 17), tint, width=tw - 10)
        x0 = 170
        c.text((x0, TOP + 2), word, font("display", 15), color, width=W - x0 - 6)
        y = self.paragraph(c, detail, TOP + 24, TEXT, 9, x=x0, width=W - x0 - 6, lines=3)
        lines = [app.biscuit.name or "Biscuit"]
        if s.get("adapter"):
            lines.append("Adapter " + s["adapter"])
        if s.get("channel"):
            lines.append("6 GHz channel %d" % s["channel"])
        lines.append("Reports as " + biscuit.FIRMWARE)
        lines.append("On for " + clock(s.get("seconds", 0)))
        for line in lines:
            if y + 10 > CONTENT_BOTTOM:
                break
            c.text((x0, y + 1), line, font("ui", 8), FAINT if line.startswith("Reports") else MUTED,
                   width=W - x0 - 6)
            y += 11
