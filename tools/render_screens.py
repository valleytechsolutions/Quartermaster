#!/usr/bin/env python3
"""Render every screen with sample data: python3 tools/render_screens.py [--out screenshots] [--scale 1] [--gif]"""

import argparse
from pathlib import Path
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from quartermaster import accounts, adsb, config, drive_watch, lora, mesh, tones, uploads, wigle  # noqa: E402
from quartermaster.recon import STRONG  # noqa: E402
from quartermaster.engine import Wardrive  # noqa: E402
from quartermaster.nodes import NodeManager  # noqa: E402
from quartermaster.recon_engine import Recon  # noqa: E402
from quartermaster.ui import screens, splash  # noqa: E402
from quartermaster.ui.app import App, FlashJob  # noqa: E402
from quartermaster.ui.keys import ScriptedInput  # noqa: E402
from tests.fakes import (ADAPTERS, FIX, Ble, Clock, Display, FakeAir, FakeBus, FakeCap, FakeHub, FakeNode,  # noqa: E402
                         Fleet, Gps, Hardware, NoBle, QuietFleet, Silent, usb_serial)

NOW = 1795000000
KEYS = {"wigle_name": "AID0f1e2d3c4b5a69788796a5b4c3d2e1f0", "wigle_token": "5a4b3c2d1e0f9a8b7c6d5e4f3a2b1c0d",
        "wdgwars_key": "c7" * 32, "wardrift_key": ""}
BLUETOOTH = [("C4:7C:8D:11:22:01", "Galaxy Buds2 Pro", -58), ("E4:5F:01:77:88:04", "JBL Flip 6", -80)]
MAST, SPARE = "dc5475c8a1b2", "48ca43e91a2b"
HUB, BOW, STERN = "dc5475c8a3c4", "48ca43e93d01", "48ca43e95e02"


class Site:
    """Stands in for the setup page server so the QR code and PIN are the same in every render."""

    pin, port, visits, saves, locked = "482913", 8642, 0, 0, False

    def __init__(self, *args, **kwargs):
        pass

    def start(self):
        return self

    def stop(self):
        pass


class Aircraft:
    """Stands in for the aircraft side of a wardrive, with the aircraft it has heard."""

    count = 23

    def __init__(self, *args, **kwargs):
        pass

    def start(self, epoch):
        pass

    def stop(self):
        pass

    def finish(self):
        return None

    def status(self):
        return {"state": adsb.RUNNING, "aircraft": self.count, "detail": adsb.WORDS[adsb.RUNNING]}


class MeshNodes(mesh.Listener):
    """Stands in for the mesh side of a wardrive, with the nodes it has heard."""

    HEARD = [({"network": "meshtastic", "node": "1a2b3c4d", "name": "Hilltop Rptr", "short_name": "HTR",
               "role": "ROUTER", "position": (25.0912, -77.3801), "hops": 0}, -92.5, 6.25),
             ({"network": "meshtastic", "node": "0badcafe", "name": "Pocket", "short_name": "PKT", "hops": 1},
              -104.0, -2.5),
             ({"network": "meshcore", "node": "ab" * 8, "public_key": "ab" * 32, "name": "Ridge", "role": "REPEATER",
               "position": (25.1302, -77.4115), "hops": 2}, -111.0, -8.75)]

    def start(self, epoch):
        self.prepare(epoch)
        for i in range(9):
            packet, rssi, snr = self.HEARD[i % 3]
            packet = dict(packet, node=packet["node"][:-1] + str(i))
            self.mesh.hear(packet, rssi, snr, epoch + i * 40, (FIX["latitude"], FIX["longitude"], 4.0))
        self.state, self.packets = mesh.LISTENING, 214

    def stop(self):
        pass


def build(root):
    cfg = config.load(root / "config.json")
    cfg.update(ble_interval=0.5, country_set=True, ignore=["Black Pearl Guest", "My iPhone", "a4:91:b1:20:7c:5e"])
    accounts.save(KEYS, root / "accounts.json")
    gps = Gps(FIX)
    ticks = iter(range(NOW, NOW + 10 ** 7, 3))

    def sender(service, path, saved, endpoints, **extra):
        if service == "wigle":
            return uploads.Outcome(uploads.DONE, "WiGLE has it (20261118-00412)", "20261118-00412")
        return uploads.Outcome(uploads.DONE, "WDGWars has it (job 5731)", "5731")
    uploader = uploads.Uploader(root, cfg, accounts_path=root / "accounts.json", sender=sender, clock=lambda: NOW)
    recon = Recon(cfg, gps, root=root, adapters=ADAPTERS, ble=NoBle(), fleet=QuietFleet, sound=Silent(),
                  clock=lambda: NOW)
    clock = Clock()
    bus = FakeBus(clock)
    sys_tty, dev, by_id = usb_serial(root / "usb", {})
    nodes = NodeManager(cfg, bus.factory, sys_tty, dev, by_id, clock=clock, threads=False)
    app = App(Display(), ScriptedInput(), gps, cfg, root=root, splash=False, nodes=nodes,
              wardrive=lambda c, g: Wardrive(c, g, root=root, adapters=ADAPTERS, ble=Ble(BLUETOOTH), fleet=Fleet,
                                             clock=lambda: next(ticks)),
              recon=recon, uploader=uploader, hw=Hardware(), accounts_path=root / "accounts.json",
              config_path=root / "config.json", setup=Site, cap=FakeCap(lora.ABSENT),
              tester=lambda service, saved: uploads.Outcome(uploads.DONE, "Signed in to %s as blackbeard"
                                                            % accounts.SERVICE_NAMES[service]))
    uploader.active = app.active_log
    app.bus = bus
    return app


def plug_in_nodes(app, root):
    """One node in the settings that scans, and one just plugged in."""
    bus, nodes = app.bus, app.nodes
    ports = {"ttyACM0": ("303a:1001", "usb-Espressif_USB_JTAG_serial_debug_unit_DC:54:75:C8:A1:B2-if00"),
             "ttyACM1": ("303a:1001", "usb-Espressif_USB_JTAG_serial_debug_unit_48:CA:43:E9:1A:2B-if00")}
    _, dev, _ = usb_serial(root / "usb", ports)
    bus.add(dev / "ttyACM0", FakeNode(MAST, bus.clock, firmware="1.1.0"))
    bus.add(dev / "ttyACM1", FakeNode(SPARE, bus.clock, caps="wifi24+wifi5", board="stamp-c5", flash_mb=4,
                                      psram_mb=0))
    app.change("nodes", [{"id": MAST, "name": "Crow's nest", "ble": False, "led": "gpio,27,1"}])
    nodes.configure(app.cfg)
    nodes.scan()
    nodes.attach(app, lambda found, adapter: None)
    for _ in range(40):
        nodes.pump()
    mast = bus.devices[str(dev / "ttyACM0")]
    mast.networks, mast.sweeps = 1204, 37
    for _ in range(12):
        nodes.pump()


def pair_wireless_nodes(app, root):
    """A hub plugged in, one wireless node just added through it and one more asking to join."""
    bus, nodes = app.bus, app.nodes
    _, dev, _ = usb_serial(root / "usb", {"ttyACM2": ("303a:1001", "")})
    airs = [FakeAir(BOW, bus.clock, board="stamp-c5", flash_mb=4, psram_mb=0), FakeAir(STERN, bus.clock)]
    airs[0].rssi, airs[1].rssi = -48, -63
    bus.add(dev / "ttyACM2", FakeHub(HUB, bus.clock, airs=airs))
    app.change("cluster_key", "3d6f0a9c8e7b5d4c2a1f0e9d8c7b6a59")
    app.change("nodes", app.cfg["nodes"] + [{"id": HUB, "name": "Hub", "ble": False, "hub": True}])
    nodes.configure(app.cfg)
    nodes.scan()
    for _ in range(20):
        nodes.pump()
    nodes.pair()
    app.push(screens.Pairing())
    for _ in range(30):
        nodes.pump()
    press(app, "ENTER")
    for _ in range(30):
        nodes.pump()
    press(app)


def old_logs(root):
    """A few earlier wardrives in different upload states."""
    logs = root / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    ledger = uploads.Ledger(root / "uploads.json")
    for days, rows, states in ((1, 1380, ("done", "done")), (2, 5210, ("done", "rejected")),
                               (4, 2903, ("done", "done")), (6, 860, ("done", "done")), (9, 4127, ("done", "done"))):
        epoch = NOW - days * 86400 - days * 1237
        writer = wigle.WigleWriter(wigle.new_log_path(logs, epoch), "13")
        for i in range(rows // 20):
            writer.write("WIFI", {"mac": "AA:BB:CC:%02X:%02X:01" % (i >> 8, i & 255), "name": "net %d" % i}, -60,
                         FIX, epoch + i)
        writer.close()
        for service, state in zip(("wigle", "wdgwars"), states):
            detail = "WDGWars refused the file (400): bad header" if state == "rejected" else "Sent"
            ledger.record(writer.path.name, service, uploads.Outcome(state, detail), epoch + 900)


def press(app, *names):
    app.keyboard.push(*names)
    while app.keyboard.queue:
        app.step(0)
    app.step(0)


def wait(app, root, shot):
    """A wardrive started before the GPS has a fix, and stopped before it gets one."""
    factory = app.wardrive_factory
    app.wardrive_factory = lambda c, g: Wardrive(c, g, root=root, adapters=ADAPTERS, ble=Ble(BLUETOOTH), fleet=Fleet,
                                                 clock=lambda: NOW)
    app.gps.fix = {"valid": False, "source": "CP210x USB GPS (/dev/ttyUSB0)", "satellites": 3, "status": "no_fix",
                   "age_seconds": 0.4}
    press(app, "W")
    shot("wardrive-waiting")
    app.stop_wardrive()
    shot("summary-no-fix")
    app.gps.fix = FIX
    app.wardrive_factory = factory
    app.home()


def fly(app, root, shot):
    """A wardrive with aircraft on and an RTL-SDR stick plugged in."""
    ticks = iter(range(NOW + 7200, NOW + 10 ** 7, 3))
    app.change("nodes", [])
    app.nodes.configure(app.cfg)
    app.cfg["adsb"] = True
    app.hw.sdr = [{"usb_id": "0bda:2838", "label": "RTL2838UHIDIR", "port": "1-1.3"}]
    app.hw.decoder = "/usr/bin/rtl_adsb"
    app.wardrive_factory = lambda c, g: Wardrive(c, g, root=root, adapters=ADAPTERS, ble=Ble(BLUETOOTH), fleet=Fleet,
                                                 clock=lambda: next(ticks), receiver=Aircraft)
    app.home()
    press(app, "W")
    app.drive.tracker.counts["BLE"] = 187
    app.drive.rows = 1263
    shot("wardrive-aircraft")
    app.open("Hardware")
    cards = app.screen.cards(app)
    app.screen.top = max(0, next(i for i, card in enumerate(cards) if card["tag"] == "SDR") - 2)
    shot("hardware-aircraft")
    app.stop_wardrive()
    app.screen.s.update(ble=187, elapsed=1574)
    shot("summary-aircraft")


def listen(app, root, shot):
    """A wardrive with a Cap LoRa-1262 fitted, aircraft on as well."""
    ticks = iter(range(NOW + 14400, NOW + 10 ** 7, 3))
    app.cap = FakeCap()
    app.wardrive_factory = lambda c, g: Wardrive(c, g, root=root, adapters=ADAPTERS, ble=Ble(BLUETOOTH), fleet=Fleet,
                                                 clock=lambda: next(ticks), receiver=Aircraft, cap=app.cap,
                                                 listener=MeshNodes)
    app.home()
    press(app, "W")
    app.drive.tracker.counts["BLE"] = 187
    app.drive.rows = 1263
    shot("wardrive-mesh")
    app.open("Hardware")
    cards = app.screen.cards(app)
    app.screen.top = max(0, next(i for i, card in enumerate(cards) if card["tag"] == "LORA") - 2)
    shot("hardware-mesh")
    app.stop_wardrive()
    app.screen.s.update(ble=187, elapsed=1574)
    shot("summary-mesh")
    app.uploader.run_pass()
    app.stale()
    press(app, "ESC", "L")
    files = app.log_files()
    app.screen.index = next(i for i, entry in enumerate(files) if entry.get("mesh"))
    press(app)
    shot("logs-mesh")
    press(app, "ENTER")
    shot("log-detail-mesh")


def main():
    tones.find_player = lambda: None
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=str(ROOT / "screenshots"))
    parser.add_argument("--scale", type=int, default=1)
    parser.add_argument("--gif", action="store_true", help="also write the boot animation as boot.gif")
    args = parser.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix="quartermaster-shots-"))
    scale = args.scale

    def save(image, name):
        image.resize((image.width * scale, image.height * scale), 0).save(out / (name + ".png"))

    for name, t in (("boot-1", 0.2), ("boot-2", 0.45), ("boot-3", 0.62), ("boot-4", 1.0)):
        save(splash.frame(t), name)
    if args.gif:
        count = int(splash.SECONDS * 15)
        frames = [splash.frame(i / count).resize((320 * scale, 170 * scale), 0) for i in range(count + 1)]
        frames[0].save(out / "boot.gif", save_all=True, append_images=frames[1:], loop=0, optimize=True,
                       duration=[66] * count + [int(splash.HOLD * 1000) + 1200])

    old_logs(root)
    app = build(root)

    def lit(at):
        return not (int(at * 2) % 2 or int(at * 3) % 2)

    def shot(name):
        app.toast_until = 0
        while not (lit(time.monotonic()) and lit(time.monotonic() + 0.1)):    # every blinking mark is lit
            time.sleep(0.005)
        if app.wardriving and not app.drive.waiting:
            app.drive._started = time.monotonic() - 1574
        save(app.compose(), name)

    shot("home")
    wait(app, root, shot)
    press(app, "W")
    deadline = time.monotonic() + 5
    while app.drive.tracker.summary()["ble"] < len(BLUETOOTH) and time.monotonic() < deadline:
        time.sleep(0.02)
    app.drive.tracker.counts["BLE"] = 187
    app.drive.rows = 1263
    shot("wardrive")
    app.drive.watch._show({"time": NOW, "category": "flock", "label": drive_watch.LABELS["flock"],
                           "name": "Flock-3A1007", "address": "B4:1E:52:3A:10:07", "rssi": -71, "confidence": STRONG,
                           "evidence": "Flock Safety OUI", "transport": "wifi"}, False)
    shot("wardrive-alert")
    app.cfg["alert_place"] = "top"
    shot("wardrive-alert-top")
    app.cfg["alert_place"] = "bottom"
    press(app, "A")
    shot("drive-alerts")
    press(app, "ESC")
    press(app, "ENTER")
    shot("confirm")
    press(app, "ENTER")
    app.screen.s.update(ble=187, elapsed=1574)
    shot("summary")
    app.uploader.run_pass()
    app.stale()
    press(app, "ESC", "L")
    shot("logs")
    press(app, "ENTER")
    shot("log-detail")
    press(app, "ESC", "ESC", "U")
    shot("uploads")
    press(app, "ESC", "K", "DOWN", "T")
    deadline = time.monotonic() + 5
    while app.tests.get("wigle", ("busy",))[0] == "busy" and time.monotonic() < deadline:
        time.sleep(0.01)
    press(app)
    shot("keys")
    press(app, "DOWN", "DOWN", "DOWN", "ENTER")
    app.keyboard.type("wdk_live_8f3a61c0")
    press(app)
    shot("key-entry")
    press(app, "TAB", "RIGHT")
    shot("symbols")
    press(app, "ESC", "ESC", "UP", "UP", "UP", "UP", "ENTER")
    shot("phone-setup")
    press(app, "ESC", "ESC", "H")
    shot("hardware")
    press(app, "ESC", "S")
    shot("settings")
    press(app, "ENTER")
    shot("picker")
    press(app, "ESC")
    app.screen.index = next(i for i, item in enumerate(app.screen.items) if item[0] == "ignore")
    press(app, "ENTER")
    shot("ignore-list")
    press(app, "ESC")
    app.screen.index = next(i for i, item in enumerate(app.screen.items) if item[0] == "drive_alerts")
    press(app)
    shot("settings-alerts")
    app.screen.index = next(i for i, item in enumerate(app.screen.items) if item[0] == "drive_alert_types")
    press(app, "ENTER")
    shot("alert-types")
    press(app, "ESC")
    plug_in_nodes(app, root)
    app.screen.index = next(i for i, item in enumerate(app.screen.items) if item[0] == "nodes")
    press(app, "ENTER")
    shot("nodes")
    press(app, "DOWN", "ENTER")
    shot("node-detail")
    job = FlashJob(app.nodes, {"node": SPARE, "path": "/dev/ttyACM1", "native": True, "title": "Stamp-C5 1A2B",
                               "detail": "", "mode": "update", "configured": False}, "update")
    job.state = "writing"
    job.progress(611 * 1024, 1005216 + 8192)
    app.push(screens.Flashing(job))
    shot("flashing")
    app.pop()
    pair_wireless_nodes(app, root)
    shot("pairing")
    app.pop()
    app.nodes.detach(app)
    for device in app.bus.devices.values():
        device.unplug()
    app.nodes.pump()
    press(app, "ESC", "ESC", "ESC", "A")
    shot("about")
    press(app, "T")
    shot("tips")
    press(app, "ESC", "ESC", "R")
    shot("recon-ready")
    press(app, "T")
    shot("watch-list")
    press(app, "ESC", "SPACE")
    r = app.recon
    r.started = time.monotonic() - 754

    def wifi(bssid, ssid, rssi, channel=6):
        return {"bssid": bssid, "ssid": ssid, "rssi": rssi, "channel": channel, "frequency": 2407 + channel * 5,
                "security": "WPA2", "band": "2.4"}
    for step in range(12):
        r.observe("wifi", wifi("B4:1E:52:3A:10:07", "Flock-3A1007", -74 + step * 2), now=NOW - 24 + step * 2)
    r.observe("wifi", wifi("00:25:DF:71:0C:2E", "", -63, 11), now=NOW - 4)
    r.observe("wifi", wifi("00:B4:63:19:AA:40", "Ring Setup 40", -81, 1), now=NOW - 9)
    r.observe("ble", {"address": "D1:22:33:44:55:66", "address_type": 1, "rssi": -69, "name": "", "uuids": [],
                      "manufacturer": {0x004C: bytes([0x12, 0x19]) + bytes(25)}, "service_data": {}}, now=NOW - 2)
    for i in range(31):
        r.observe("wifi", wifi("AA:BB:CC:00:10:%02X" % i, "net %d" % i, -70), now=NOW - 3)
    for i in range(18):
        r.observe("ble", {"address": "11:22:33:00:20:%02X" % i, "address_type": 1, "rssi": -80, "name": "",
                          "uuids": [], "manufacturer": {}, "service_data": {}}, now=NOW - 3)
    r.unseen_alerts = 1
    press(app)
    shot("recon")
    press(app, "ENTER")
    shot("recon-detail")
    press(app, "ENTER")
    shot("recon-finder")
    r.alerts[0]["time"] = NOW
    r.unseen_alerts = 1
    press(app, "ESC", "ESC", "ESC")
    shot("recon-alert")
    app.stop_recon()
    fly(app, root, shot)
    listen(app, root, shot)
    app.shutdown()
    print("%d screenshots in %s" % (len(list(out.glob("*.png"))), out))


if __name__ == "__main__":
    main()
