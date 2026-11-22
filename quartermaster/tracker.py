"""Deduplication: one record per device, with the strongest and weakest place it was heard.

A wardrive passes the same access point many times. The tracker keeps a single
entry per MAC and remembers where the signal was strongest (closest pass) and
weakest (farthest edge of its range). It also decides which sightings are worth
a WiGLE row, so the log stays small but gives WiGLE good trilateration points.
"""

import math
import threading

WIFI, BLE, BT = "WIFI", "BLE", "BT"


def distance_m(a, b):
    lat1, lon1, lat2, lon2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 2 * 6371000 * math.asin(math.sqrt(min(1.0, h)))


class Device:
    __slots__ = ("kind", "mac", "name", "security", "capabilities", "channel", "frequency", "band", "first_seen",
                 "last_seen", "sightings", "rssi", "best", "worst", "logged_at", "logged_pos", "rows", "extra")

    def __init__(self, kind, mac, now):
        self.kind, self.mac = kind, mac
        self.name = self.security = self.capabilities = self.band = ""
        self.channel = self.frequency = None
        self.first_seen = self.last_seen = now
        self.sightings = 0
        self.rssi = None
        self.best = self.worst = None      # (rssi, lat, lon, epoch) or None before a GPS fix
        self.logged_at = None
        self.logged_pos = None
        self.rows = 0
        self.extra = None

    def as_dict(self):
        def point(p):
            return None if p is None else {"rssi": p[0], "latitude": p[1], "longitude": p[2], "time": p[3]}
        return {"kind": self.kind, "mac": self.mac, "name": self.name, "security": self.security,
                "capabilities": self.capabilities, "channel": self.channel, "frequency": self.frequency,
                "band": self.band, "first_seen": self.first_seen, "last_seen": self.last_seen,
                "sightings": self.sightings, "rssi": self.rssi, "strongest": point(self.best),
                "weakest": point(self.worst), "rows": self.rows}


class Tracker:
    def __init__(self, relog_seconds=30.0, relog_meters=25.0, rssi_step=4):
        self.relog_seconds = relog_seconds
        self.relog_meters = relog_meters
        self.rssi_step = rssi_step
        self.devices = {}
        self.lock = threading.Lock()
        self.counts = {WIFI: 0, BLE: 0, BT: 0}
        self.bands = {"2.4": 0, "5": 0, "6": 0}
        self.security = {}
        self.sightings = 0

    def observe(self, kind, mac, rssi, now, fix=None, **fields):
        """Record one sighting. Returns (device, is_new, reason) where reason is why this sighting
        should be logged ("new", "stronger", "weaker", "moved", "interval") or None to skip it."""
        key = (kind, mac)
        with self.lock:
            device = self.devices.get(key)
            new = device is None
            if new:
                device = self.devices[key] = Device(kind, mac, now)
                self.counts[kind] = self.counts.get(kind, 0) + 1
                band = fields.get("band")
                if kind == WIFI and band in self.bands:
                    self.bands[band] += 1
                if kind == WIFI:
                    label = fields.get("security") or "OPEN"
                    self.security[label] = self.security.get(label, 0) + 1
            device.last_seen = now
            device.sightings += 1
            self.sightings += 1
            device.rssi = rssi
            for name in ("security", "capabilities", "channel", "frequency", "band", "extra"):
                value = fields.get(name)
                if value not in (None, ""):
                    setattr(device, name, value)
            # A hidden network's name never replaces one learned earlier.
            if fields.get("name"):
                device.name = fields["name"]
            if not fix or not fix.get("valid") or rssi is None:
                return device, new, None
            point = (rssi, fix["latitude"], fix["longitude"], now)
            reason = None
            if device.best is None:
                device.best = device.worst = point
                reason = "new"
            else:
                if rssi > device.best[0]:
                    if rssi - device.best[0] >= self.rssi_step:
                        reason = "stronger"
                    device.best = point
                elif rssi < device.worst[0]:
                    if device.worst[0] - rssi >= self.rssi_step:
                        reason = "weaker"
                    device.worst = point
            if reason is None and device.logged_at is not None:
                moved = distance_m(device.logged_pos, point[1:3]) >= self.relog_meters
                waited = now - device.logged_at >= self.relog_seconds
                if moved and waited:
                    reason = "moved"
            if reason:
                device.logged_at, device.logged_pos = now, point[1:3]
                device.rows += 1
            return device, new, reason

    def recent(self, limit=50, kind=None):
        with self.lock:
            items = [d for d in self.devices.values() if kind is None or d.kind == kind]
        items.sort(key=lambda d: d.last_seen, reverse=True)
        return items[:limit]

    def summary(self):
        with self.lock:
            return {"wifi": self.counts.get(WIFI, 0), "ble": self.counts.get(BLE, 0) + self.counts.get(BT, 0),
                    "total": len(self.devices), "bands": dict(self.bands), "security": dict(self.security),
                    "sightings": self.sightings}
