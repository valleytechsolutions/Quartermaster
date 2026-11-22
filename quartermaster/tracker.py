"""One record per device, with the strongest and weakest place it was heard.

The strongest sighting is the closest pass and the weakest is the farthest edge
of the device's range. The tracker also decides which sightings earn a WiGLE
row, and remembers which extremes still have to be written to the log.
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
                 "last_seen", "sightings", "rssi", "best", "worst", "logged_best", "logged_worst", "logged_at",
                 "logged_pos", "rows", "extra", "ignored", "order")

    def __init__(self, kind, mac, now, order=0):
        self.kind, self.mac, self.order = kind, mac, order
        self.name = self.security = self.capabilities = self.band = ""
        self.channel = self.frequency = None
        self.first_seen = self.last_seen = now
        self.sightings = 0
        self.rssi = None
        self.best = self.worst = None      # (rssi, lat, lon, epoch, altitude, accuracy)
        self.logged_best = self.logged_worst = None
        self.logged_at = None
        self.logged_pos = None
        self.rows = 0
        self.extra = None
        self.ignored = False


class Tracker:
    def __init__(self, relog_seconds=30.0, relog_meters=25.0, rssi_step=4, accuracy=None):
        self.relog_seconds = relog_seconds
        self.relog_meters = relog_meters
        self.rssi_step = rssi_step
        self.accuracy = accuracy or (lambda fix: None)
        self.devices = {}                  # kept in order of last sighting, oldest first
        self.made = 0
        self.lock = threading.Lock()
        self.counts = {WIFI: 0, BLE: 0, BT: 0}
        self.bands = {"2.4": 0, "5": 0, "6": 0}
        self.security = {}
        self.sightings = 0

    def observe(self, kind, mac, rssi, now, fix=None, **fields):
        """Record one sighting and return (device, is_new, reason).

        reason says why the sighting earns a log row: "new", "stronger", "weaker" or "moved".
        It is None when the sighting adds nothing, or when there is no usable fix."""
        key = (kind, mac)
        with self.lock:
            device = self.devices.pop(key, None)
            new = device is None
            if new:
                device = Device(kind, mac, now, self.made)
                self.made += 1
                self.counts[kind] = self.counts.get(kind, 0) + 1
                band = fields.get("band")
                if kind == WIFI and band in self.bands:
                    self.bands[band] += 1
                if kind == WIFI:
                    label = fields.get("security") or "OPEN"
                    self.security[label] = self.security.get(label, 0) + 1
            self.devices[key] = device
            device.last_seen = now
            device.sightings += 1
            self.sightings += 1
            device.rssi = rssi
            for name in ("security", "capabilities", "channel", "frequency", "band", "extra"):
                value = fields.get(name)
                if value not in (None, ""):
                    setattr(device, name, value)
            if fields.get("name"):
                device.name = fields["name"]
            if fields.get("ignored"):
                device.ignored = True
            if device.ignored or not fix or not fix.get("valid") or rssi is None:
                return device, new, None
            point = (rssi, fix["latitude"], fix["longitude"], now, fix.get("altMSL", fix.get("altHAE")),
                     self.accuracy(fix))
            reason = None
            if device.best is None:
                device.best = device.worst = point
                reason = "new"
            else:
                if rssi > device.best[0]:
                    device.best = point
                elif rssi < device.worst[0]:
                    device.worst = point
                if rssi - device.logged_best >= self.rssi_step:
                    reason = "stronger"
                elif device.logged_worst - rssi >= self.rssi_step:
                    reason = "weaker"
                elif (now - device.logged_at >= self.relog_seconds
                      and distance_m(device.logged_pos, point[1:3]) >= self.relog_meters):
                    reason = "moved"
            if reason:
                device.logged_at, device.logged_pos = now, point[1:3]
                self._logged(device, point)
            return device, new, reason

    @staticmethod
    def _logged(device, point):
        device.logged_best = point[0] if device.logged_best is None else max(device.logged_best, point[0])
        device.logged_worst = point[0] if device.logged_worst is None else min(device.logged_worst, point[0])
        device.rows += 1

    def take_extremes(self, idle_before=None):
        """Strongest and weakest sightings that no log row carries yet.

        Each is returned once, as (device, point), and counts as logged from then on.
        With idle_before, only devices not heard since that time are considered."""
        out = []
        with self.lock:
            for device in self.devices.values():
                if device.best is None or device.ignored:
                    continue
                if idle_before is not None and device.last_seen > idle_before:
                    continue
                if device.best[0] > device.logged_best:
                    out.append((device, device.best))
                    self._logged(device, device.best)
                if device.worst[0] < device.logged_worst:
                    out.append((device, device.worst))
                    self._logged(device, device.worst)
        out.sort(key=lambda item: item[1][3])
        return out

    def recent(self, limit=50, kinds=None):
        """The devices heard last, newest first, and those heard in the same moment in the order they were found.

        After hours of driving there are far too many devices to walk them all for every frame."""
        found = []
        with self.lock:
            for device in reversed(self.devices.values()):
                if len(found) >= limit and device.last_seen != found[-1].last_seen:
                    break
                if kinds is None or device.kind in kinds:
                    found.append(device)
        found.sort(key=lambda d: (-d.last_seen, d.order))
        return found[:limit]

    def found(self):
        """Every device, in the order it was first heard."""
        with self.lock:
            return sorted(self.devices.values(), key=lambda d: d.order)

    def forget(self, limit, idle_before):
        """On a very long drive, free memory: past `limit` devices, drop the ones unheard for longest.

        Only devices unheard since idle_before with nothing left to log go. They are returned, and one
        heard again later simply counts as new.
        """
        gone = []
        with self.lock:
            spare = len(self.devices) - limit
            if spare <= 0:
                return gone
            for key, device in self.devices.items():
                if len(gone) >= spare + limit // 5 or device.last_seen > idle_before:
                    break
                if device.best is None or device.ignored or (device.best[0] <= device.logged_best
                                                             and device.worst[0] >= device.logged_worst):
                    gone.append(key)
            gone = [self.devices.pop(key) for key in gone]
        return gone

    def summary(self):
        with self.lock:
            return {"wifi": self.counts.get(WIFI, 0), "ble": self.counts.get(BLE, 0) + self.counts.get(BT, 0),
                    "total": len(self.devices), "bands": dict(self.bands), "security": dict(self.security),
                    "sightings": self.sightings}
