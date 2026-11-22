#!/usr/bin/env python3
"""Render every screen to screenshots/ with sample data. Run: python3 tools/render_screens.py"""

from pathlib import Path
import random
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from quartermaster import config  # noqa: E402
from quartermaster.engine import Wardrive  # noqa: E402
from quartermaster.ui import app as ui  # noqa: E402
from quartermaster.ui.keys import ScriptedInput  # noqa: E402

OUT = Path(__file__).resolve().parents[1] / "screenshots"
FIX = {"valid": True, "latitude": 40.03712, "longitude": -75.34218, "satellites": 14, "hdop": 0.8, "age_seconds": 0.3,
       "source": "CP210x USB GPS (/dev/ttyUSB0)", "altMSL": 96.0}
ADAPTERS = [{"interface": "wlan1", "label": "MT7921AU tri-band (ALFA AWUS036AXML)", "external": True,
             "bands": ["2.4", "5", "6"], "band_label": "tri-band", "monitor": True, "phy": "phy1"},
            {"interface": "wlan0", "label": "Broadcom (built-in)", "external": False, "bands": ["2.4"],
             "band_label": "single-band", "monitor": False, "phy": "phy0"}]
NAMES = ["Black Pearl", "Flying Dutchman 5G", "xfinitywifi", "", "Tortuga Guest", "NETGEAR88-6G", "eduroam",
         "Jolly Roger IoT", "DIRECT-roku-471", "Davy Jones", "ATTm9xK2s", "Queen Anne's Revenge", "Kraken_6E"]


class GPS:
    mode = "auto"

    def snapshot(self):
        return dict(FIX)


class Fleet:
    def __init__(self, adapters, sink, interval):
        self.adapters, self.sink = adapters, sink

    def start(self):
        rnd = random.Random(7)
        nets = []
        for i in range(412):
            freq = rnd.choice([2412, 2437, 2462, 5180, 5745, 5500, 6135, 5955, 2437, 5220])
            sec, caps = rnd.choice([("WPA2", "[WPA2-PSK-CCMP][ESS]"), ("WPA2", "[WPA2-PSK-CCMP][ESS]"),
                                    ("WPA3", "[RSN-SAE-CCMP][ESS]"), ("OPEN", "[ESS]"), ("EAP", "[WPA2-EAP-CCMP][ESS]")])
            from quartermaster.wifi import band_of, channel_of
            nets.append({"bssid": "AA:BB:CC:%02X:%02X:%02X" % (i >> 8, i & 255, rnd.randrange(256)),
                         "ssid": NAMES[i % len(NAMES)], "rssi": rnd.randrange(-92, -38), "frequency": freq,
                         "channel": channel_of(freq), "band": band_of(freq), "security": sec, "capabilities": caps})
        self.sink(nets, self.adapters[0])

    def stop(self):
        pass

    def status(self):
        return [{"interface": a["interface"]} for a in self.adapters]


def main():
    OUT.mkdir(exist_ok=True)
    for name, t in (("boot-1", 0.2), ("boot-2", 0.45), ("boot-3", 0.62), ("boot-4", 1.0)):
        ui.splash_frame(t).resize((640, 340), 0).save(OUT / (name + ".png"))
    cfg = dict(config.DEFAULTS, ble=False)
    root = tempfile.mkdtemp()
    ticks = iter(range(1795000000, 1795000000 + 10 ** 6, 1432))
    app = ui.App(None, ScriptedInput(), GPS(), cfg, splash=False,
                 wardrive=lambda c, g: Wardrive(c, g, root=root, adapters=ADAPTERS, fleet=Fleet, clock=lambda: next(ticks)))
    app.adapters, app.gps_ports = ADAPTERS, [{"label": "CP210x USB GPS", "path": "/dev/ttyUSB0"}]

    def shot(name):
        app.render().resize((640, 340), 0).save(OUT / (name + ".png"))
    shot("menu")
    app.start_wardrive()
    app.drive.tracker.counts["BLE"] = 187
    shot("wardrive")
    for screen in ("hardware", "settings", "about"):
        app.screen = screen
        shot(screen)
    app.stop_wardrive()
    app.screen = "summary"
    shot("summary")


if __name__ == "__main__":
    main()
