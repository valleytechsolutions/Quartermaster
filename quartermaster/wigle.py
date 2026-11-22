"""WigleWifi-1.6 CSV writer.

The format follows WiGLE's published CSV spec exactly: a pre-header line that
names the app and device, the fixed column header, then one observation per
row. Rows are only written with a fresh GPS fix and a sane UTC time, because
WiGLE rejects or discards rows without them. The pre-header is also what marks
a log as made by Quartermaster: WiGLE, WDGWars and Wardrift all read it.
"""

import csv
import os
from pathlib import Path
import time

from . import APP_NAME, __version__

COLUMNS = ("MAC", "SSID", "AuthMode", "FirstSeen", "Channel", "Frequency", "RSSI", "CurrentLatitude",
           "CurrentLongitude", "AltitudeMeters", "AccuracyMeters", "RCOIs", "MfgrId", "Type")
FIX_MAX_AGE = 5.0
MIN_EPOCH = 1704067200  # 2024-01-01: an unset clock must never reach a log


def pre_header(os_version=""):
    return ["WigleWifi-1.6", "appRelease=%s %s" % (APP_NAME, __version__), "model=CardputerZero",
            "release=%s" % (os_version or "unknown"), "device=%s" % APP_NAME, "display=320x170",
            "board=M5Stack CardputerZero", "brand=Valleytech Solutions", "star=Sol", "body=3", "subBody=0"]


def _text(value):
    """SSIDs and names may hold anything; keep the row on one line and free of control bytes."""
    return "".join(ch if ch >= " " and ch != "\x7f" else " " for ch in str(value or ""))


def accuracy_m(fix):
    errors = [fix[k] for k in ("epx", "epy") if isinstance(fix.get(k), (int, float))]
    if errors:
        return round(max(errors), 1)
    hdop = fix.get("hdop")
    if isinstance(hdop, (int, float)) and hdop > 0:
        return round(max(2.5, hdop * 5.0), 1)
    return ""


def log_filename(epoch):
    return "quartermaster_%s.wigle.csv" % time.strftime("%Y%m%d-%H%M%S", time.gmtime(epoch))


class WigleWriter:
    def __init__(self, path, os_version="", sync_every=5.0):
        self.path = Path(path)
        new = not self.path.exists() or self.path.stat().st_size == 0
        fd = os.open(str(self.path), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        self.file = os.fdopen(fd, "a", encoding="utf-8", newline="")
        self.writer = csv.writer(self.file, lineterminator="\n", quoting=csv.QUOTE_MINIMAL)
        self.rows = 0
        self.sync_every = sync_every
        self._synced = time.monotonic()
        if new:
            self.writer.writerow(pre_header(os_version))
            self.writer.writerow(COLUMNS)
            self.sync(force=True)

    @staticmethod
    def row(kind, device, rssi, fix, epoch):
        """Build one row, or None when it would not be a valid WiGLE observation."""
        if not fix or not fix.get("valid") or (fix.get("age_seconds") or 0) > FIX_MAX_AGE:
            return None
        lat, lon = fix.get("latitude"), fix.get("longitude")
        if lat is None or lon is None or (abs(lat) < 1e-6 and abs(lon) < 1e-6):
            return None
        if not isinstance(rssi, (int, float)) or epoch < MIN_EPOCH:
            return None
        stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(epoch))
        altitude = fix.get("altMSL", fix.get("altHAE", ""))
        if isinstance(altitude, float):
            altitude = round(altitude, 1)
        position = ["%.7f" % lat, "%.7f" % lon, altitude, accuracy_m(fix)]
        rssi = max(-127, min(0, int(round(rssi))))
        if kind == "WIFI":
            return [device["mac"].lower(), _text(device.get("name")), device.get("capabilities") or "[ESS]", stamp,
                    device.get("channel") or 0, device.get("frequency") or "", rssi, *position, "", "", "WIFI"]
        # Bluetooth: AuthMode carries the device class, MfgrId the Bluetooth SIG company id.
        mfgr = device.get("mfgr")
        return [device["mac"].lower(), _text(device.get("name")), device.get("capabilities") or "Misc [LE]", stamp,
                0, "", rssi, *position, "", "" if mfgr is None else mfgr, kind]

    def write(self, kind, device, rssi, fix, epoch):
        row = self.row(kind, device, rssi, fix, epoch)
        if row is None:
            return False
        self.writer.writerow(row)
        self.rows += 1
        self.sync()
        return True

    def sync(self, force=False):
        """Flush to the card often, so pulling power loses a few seconds at most."""
        now = time.monotonic()
        if force or now - self._synced >= self.sync_every:
            self.file.flush()
            try:
                os.fsync(self.file.fileno())
            except OSError:
                pass
            self._synced = now

    def close(self):
        try:
            self.sync(force=True)
            self.file.close()
        except (OSError, ValueError):
            pass
