"""Settings > Biscuit node: run as a Biscuit or Biscuit Pro's 6 GHz node, added from a node search on the Biscuit."""

from .. import biscuit
from .gfx import AMBER, CONTENT_BOTTOM, CYAN, FAINT, GOLD, GREEN, MUTED, RED, SURFACE, TEXT, W, clock, font, image
from .screens import TOP, Screen

WORDS = {
    biscuit.STARTING: ("Starting", "Putting the 6 GHz adapter in node mode.", GOLD),
    biscuit.SEARCHING: ("Waiting", "Search for nodes in the Biscuit manager app and add this one. It shows as a "
                        "Pager.", AMBER),
    biscuit.JOINED: ("Joined", "Start a wardrive on the Biscuit to scan 6 GHz for it.", GOLD),
    biscuit.SCANNING: ("Scanning", "Scanning 6 GHz for the Biscuit.", GREEN),
    biscuit.SECURE: ("Secure mode", "Set the Biscuit to Extended node mode.", RED),
    biscuit.OLD: ("Old firmware", "This Biscuit takes no 6 GHz nodes. Update it to v1.7.0 or newer.", AMBER),
    biscuit.STOPPING: ("Stopping", "Putting the adapter back.", GOLD),
    biscuit.FAILED: ("Stopped", "Node mode stopped.", RED),
    biscuit.OFF: ("Off", "", MUTED),
}
LOGO = "biscuitshop-logo.png"
BUSY = "Stop the wardrive and Recon first: node mode scans 6 GHz and nothing else."
HELP = ("Set your Biscuit or Biscuit Pro to Extended node mode and press OK here. Then search for nodes in the "
        "Biscuit manager app and add this one. It shows on the Biscuit as a Pager and scans 6 GHz only, with a 6 GHz "
        "USB adapter.")


def summary(app):
    """The word Settings shows for node mode, and its colour."""
    link = app.biscuit
    if link.running:
        word, _, color = WORDS.get(link.state, WORDS[biscuit.FAILED])
        return word, color
    return ("Stopped", AMBER) if link.state == biscuit.FAILED else ("Off", MUTED)


class BiscuitNode(Screen):
    title = "BISCUIT NODE"
    refresh = 0.5

    def hints(self, app):
        return [("OK", "Stop node"), ("Esc", "Back")] if app.biscuit.running else [("OK", "Start node"),
                                                                                  ("Esc", "Back")]

    def ready(self, app):
        link = app.biscuit
        link.device, link.diy = ("pi" if app.square else "cardputer"), bool(app.square)
        if app.wardriving or app.recon_running:
            app.toast(BUSY, AMBER, 4)
            return False
        return True

    def handle(self, key, app):
        link = app.biscuit
        if key == "ENTER" and not key.repeat:
            if link.running:
                link.stop()
            elif self.ready(app):
                link.start()
            return True
        return super().handle(key, app)

    def paragraph(self, c, text, y, color, size=10, x=10, width=W - 20, lines=4):
        face = font("ui", size)
        for line in c.wrap(text, width, face)[:lines]:
            c.text((x, y), line, face, color)
            y += size + 3
        return y

    def render(self, c, app):
        link = app.biscuit
        s = link.status()
        if link.running:
            self.render_node(c, app, s)
        else:
            self.render_off(c, app, s)

    def square(self, ui, c, app):
        """The layout for a square HAT: one column, the words wrapped to the screen."""
        link = app.biscuit
        s = link.status()
        y = ui.top + 3
        side = max(16, ui.bold.size + 6)
        c.paste(image(LOGO, (side, side)), (ui.size - side - 3, y - 1))
        if link.running:
            word, detail, color = WORDS.get(s["state"], WORDS[biscuit.FAILED])
            y = ui.paragraph(c, word, y, color, face=ui.bold)
            for label, key in (("6 GHz heard", "heard"), ("Sent", "sent"), ("Waiting", "queued")):
                y = ui.paragraph(c, "%s  %s" % (label, format(s.get(key) or 0, ",")), y + 1, TEXT)
            if s.get("channel"):
                y = ui.paragraph(c, "6 GHz channel %d" % s["channel"], y + 1, MUTED, face=ui.small)
            ui.paragraph(c, detail, y + 2, MUTED, face=ui.small)
            return "OK stop · K2 back"
        y = ui.paragraph(c, "Biscuit node", y, GOLD, face=ui.bold)
        if s["error"] and link.state == biscuit.FAILED:
            y = ui.paragraph(c, s["error"], y + 2, RED, face=ui.small)
        ui.paragraph(c, "Set the Biscuit to Extended node mode, press OK, then add this node from a search on the "
                     "Biscuit. Needs a 6 GHz USB adapter.", y + 3, TEXT, face=ui.small)
        return "OK start · K2 back"

    def render_off(self, c, app, s):
        link = app.biscuit
        y = TOP + 2
        c.paste(image(LOGO, (48, 48)), (W - 56, y))
        c.text((10, y), "Biscuit node", font("display", 17), GOLD)
        y += 26
        if s["error"] and link.state == biscuit.FAILED:
            y = self.paragraph(c, s["error"], y, RED, 9, width=W - 76, lines=3) + 3
        self.paragraph(c, HELP, y, TEXT, 10, width=W - 76, lines=max(1, (CONTENT_BOTTOM - y) // 13))

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
        c.paste(image(LOGO, (24, 24)), (W - 30, TOP + 2))
        c.text((x0, TOP + 2), word, font("display", 15), color, width=W - x0 - 36)
        y = self.paragraph(c, detail, TOP + 24, TEXT, 9, x=x0, width=W - x0 - 6, lines=3)
        lines = [app.biscuit.name or "No Biscuit yet"]
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
