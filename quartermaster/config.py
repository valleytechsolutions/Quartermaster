"""Settings: defaults, the on-disk file, and the list the Settings screen shows."""

import json
import os
from pathlib import Path
import re

PATH = Path(os.environ.get("QUARTERMASTER_CONFIG", str(Path.home() / ".config" / "quartermaster" / "config.json")))
ON_OFF = [(True, "On"), (False, "Off")]

# (section, [(key, label, choices, help)]). choices is None for a setting that opens its own screen.
SECTIONS = [
    ("SCANNING", [
        ("wifi_adapters", "Wi-Fi adapters", [("all", "All"), ("external", "USB only"), ("internal", "Built-in only")],
         "Which radios scan. All uses every adapter at once."),
        ("wifi_bands", "Wi-Fi bands kept", [("all", "All"), ("2.4", "2.4 GHz"), ("5", "5 GHz"), ("6", "6 GHz"),
                                            ("5+6", "5 + 6 GHz")], "Networks on other bands are left out."),
        ("wifi_pause", "Pause between sweeps", [(0.0, "None"), (1.0, "1 s"), (3.0, "3 s"), (10.0, "10 s")],
         "None is fastest. A pause saves battery."),
        ("min_rssi", "Weakest signal kept", [(-100, "Everything"), (-90, "-90 dBm"), (-85, "-85 dBm"),
                                             (-80, "-80 dBm"), (-70, "-70 dBm")],
         "Ignore sightings weaker than this."),
        ("ble", "Bluetooth scan", ON_OFF, "Log Bluetooth LE and classic devices."),
        ("ble_random", "Random BLE addresses", [(True, "Log"), (False, "Skip")],
         "Phones rotate random addresses. Skip keeps logs smaller."),
        ("ble_interval", "Bluetooth check", [(0.5, "0.5 s"), (1.0, "1 s"), (2.0, "2 s"), (5.0, "5 s")],
         "How often Bluetooth results are read."),
    ]),
    ("LOGGING", [
        ("rssi_step", "Log when signal changes", [(2, "2 dB"), (4, "4 dB"), (6, "6 dB"), (10, "10 dB")],
         "A stronger or weaker sighting by this much adds a row."),
        ("relog_meters", "Log again after moving", [(10, "10 m"), (25, "25 m"), (50, "50 m"), (100, "100 m")],
         "Extra rows as you move help WiGLE place the device."),
        ("relog_seconds", "Shortest time between rows", [(5, "5 s"), (10, "10 s"), (30, "30 s"), (60, "60 s")],
         "Per device. Lower means bigger logs."),
        ("log_hidden", "Hidden networks", [(True, "Log"), (False, "Skip")], "Networks that do not send a name."),
        ("ignore", "Ignored networks", None, "Names or addresses that are never written to a log."),
    ]),
    ("GPS", [
        ("gps_source", "GPS source", [("auto", "Auto"), ("gpsd", "gpsd"), ("/dev/ttyUSB0", "USB (ttyUSB0)"),
                                      ("/dev/ttyACM0", "USB (ttyACM0)"), ("/dev/ttyS0", "Cap / Grove"),
                                      ("off", "Off")], "Auto finds a USB, Cap LoRa or Grove GPS by itself."),
        ("gps_max_hdop", "GPS accuracy needed", [(0, "Any fix"), (10, "Fair"), (5, "Good"), (2, "Best")],
         "Rows wait until the fix is at least this good."),
        ("gps_power", "Cap and Grove power", [(True, "When needed"), (False, "Leave off")],
         "Switches on 5 V for a GPS on the Cap or the Grove port."),
    ]),
    ("UPLOADS", [
        ("auto_upload", "Send logs", [(True, "Automatic"), (False, "Only when asked")],
         "Automatic sends finished logs whenever the Cardputer is online."),
    ]),
    ("RECON", [
        ("recon_display_min", "Show devices from", [("low", "Weak hints"), ("medium", "Medium evidence"),
                                                    ("high", "Strong only")], "Weakest evidence listed in Recon."),
        ("recon_alert_min", "Alert from", [("medium", "Medium evidence"), ("high", "Strong only"),
                                           ("low", "Weak hints")], "Evidence needed before Recon alerts you."),
        ("recon_sound", "Alert sound", ON_OFF, "Beep when Recon raises an alert."),
        ("recon_categories", "Watch list", None, "Kinds of devices Recon looks for."),
    ]),
    ("SCREEN", [
        ("live_list", "Live list shows", [("all", "Wi-Fi + BLE"), ("wifi", "Wi-Fi"), ("ble", "Bluetooth")],
         "What scrolls on the wardrive screen."),
        ("auto_start", "Start wardrive on launch", [(False, "No"), (True, "Yes")],
         "Skip the menu and start scanning."),
        ("splash", "Boot animation", ON_OFF, "Show the ship at launch."),
    ]),
]
SETTINGS = [item for _, items in SECTIONS for item in items]
DEFAULTS = {key: choices[0][0] for key, _, choices, _ in SETTINGS if choices}
DEFAULTS.update(min_rssi=-100, rssi_step=4, relog_meters=25, relog_seconds=30, ble_interval=1.0)
LISTS = ("ignore", "recon_categories")
MAX_IGNORED = 64
_MAC = re.compile(r"(?:[0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}")


def is_mac(text):
    return bool(_MAC.fullmatch(str(text).strip()))


def default_lists():
    from .recon import CATEGORIES
    return {"ignore": [], "recon_categories": list(CATEGORIES)}


def load(path=PATH):
    cfg = dict(DEFAULTS)
    cfg.update(default_lists())
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return cfg
    if not isinstance(data, dict):
        return cfg
    for key, _, choices, _ in SETTINGS:
        if choices and key in data and any(data[key] == v and type(data[key]) is type(v) for v, _ in choices):
            cfg[key] = data[key]
    ignore = data.get("ignore")
    if isinstance(ignore, list):
        cfg["ignore"] = [str(item)[:64] for item in ignore if isinstance(item, str) and item.strip()][:MAX_IGNORED]
    categories = data.get("recon_categories")
    if isinstance(categories, list):
        cfg["recon_categories"] = [c for c in default_lists()["recon_categories"] if c in categories]
    return cfg


def save(cfg, path=PATH):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = path.with_name("." + path.name)
    keys = list(DEFAULTS) + list(LISTS)
    tmp.write_text(json.dumps({k: cfg[k] for k in keys if k in cfg}, indent=1) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def shown(key, value):
    for name, _, choices, _ in SETTINGS:
        if name == key and choices:
            return next((text for v, text in choices if v == value), str(value))
    return str(value)
