"""Settings: defaults, the on-disk file, and the list the Settings screen shows."""

import json
import os
from pathlib import Path

PATH = Path(os.environ.get("QUARTERMASTER_CONFIG", str(Path.home() / ".config" / "quartermaster" / "config.json")))

# (key, label, [(value, shown text), ...], help)
SETTINGS = [
    ("wifi_adapters", "Wi-Fi adapters", [("all", "All"), ("external", "USB only"), ("internal", "Built-in only")],
     "Which radios scan. All uses every adapter at once."),
    ("ble", "Bluetooth scan", [(True, "On"), (False, "Off")], "Log Bluetooth LE and classic devices."),
    ("ble_random", "Random BLE addresses", [(True, "Log"), (False, "Skip")],
     "Phones rotate random addresses. Skip keeps logs smaller."),
    ("min_rssi", "Weakest signal kept", [(-100, "Everything"), (-90, "-90 dBm"), (-85, "-85 dBm"), (-80, "-80 dBm"),
                                         (-70, "-70 dBm")], "Ignore sightings weaker than this."),
    ("rssi_step", "Log when signal changes", [(2, "2 dB"), (4, "4 dB"), (6, "6 dB"), (10, "10 dB")],
     "New strongest or weakest sighting by this much adds a row."),
    ("relog_meters", "Log again after moving", [(10, "10 m"), (25, "25 m"), (50, "50 m"), (100, "100 m")],
     "Extra rows as you move help WiGLE place the device."),
    ("relog_seconds", "Shortest time between rows", [(5, "5 s"), (10, "10 s"), (30, "30 s"), (60, "60 s")],
     "Per device. Lower means bigger logs."),
    ("wifi_pause", "Pause between sweeps", [(0.0, "None"), (1.0, "1 s"), (3.0, "3 s"), (10.0, "10 s")],
     "None is fastest. A pause saves battery."),
    ("ble_interval", "Bluetooth check", [(0.5, "0.5 s"), (1.0, "1 s"), (2.0, "2 s"), (5.0, "5 s")],
     "How often Bluetooth results are read."),
    ("gps_source", "GPS source", [("auto", "Auto"), ("gpsd", "gpsd"), ("/dev/ttyUSB0", "USB (ttyUSB0)"),
                                  ("/dev/ttyACM0", "USB (ttyACM0)"), ("/dev/ttyS0", "Cap / Grove"), ("off", "Off")],
     "Auto finds a USB, Cap LoRa or Grove GPS by itself."),
    ("grove_power", "Grove port power", [(False, "Leave"), (True, "On while running")],
     "Turn on 5 V for a Grove GPS unit."),
    ("live_list", "Live list shows", [("all", "Wi-Fi + BLE"), ("wifi", "Wi-Fi"), ("ble", "Bluetooth")],
     "What scrolls on the wardrive screen."),
    ("auto_start", "Start wardrive on launch", [(False, "No"), (True, "Yes")], "Skip the menu and start scanning."),
    ("splash", "Boot animation", [(True, "On"), (False, "Off")], "Show the logos at launch."),
]
DEFAULTS = {key: choices[0][0] for key, _, choices, _ in SETTINGS}
DEFAULTS.update(min_rssi=-100, rssi_step=4, relog_meters=25, relog_seconds=30, ble_interval=1.0)


def load(path=PATH):
    cfg = dict(DEFAULTS)
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return cfg
    if isinstance(data, dict):
        for key, _, choices, _ in SETTINGS:
            if key in data and any(data[key] == value and type(data[key]) is type(value) for value, _ in choices):
                cfg[key] = data[key]
    return cfg


def save(cfg, path=PATH):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = path.with_name("." + path.name)
    tmp.write_text(json.dumps({k: cfg[k] for k in DEFAULTS if k in cfg}, indent=1) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def shown(key, value):
    for name, _, choices, _ in SETTINGS:
        if name == key:
            return next((text for v, text in choices if v == value), str(value))
    return str(value)


def cycle(cfg, key, step=1):
    for name, _, choices, _ in SETTINGS:
        if name == key:
            values = [v for v, _ in choices]
            index = values.index(cfg[key]) if cfg.get(key) in values else 0
            cfg[key] = values[(index + step) % len(values)]
            return cfg[key]
    raise KeyError(key)
