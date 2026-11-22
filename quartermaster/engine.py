"""The wardrive: scanners in, deduplicated devices and WiGLE logs out."""

import csv
import os
from pathlib import Path
import re
import shutil
import threading
import time

from . import hardware, radios, wigle
from .ble import BleScanner
from .tracker import BLE, BT, WIFI, Tracker
from .wifi import ScanUnavailable, WifiFleet

MIN_FREE_BYTES = 32 * 1024 * 1024
# Every upload site takes a file this size: Wardrift stops at 10 MiB, WDGWars at 15 MB.
ROTATE_BYTES = 8 * 1024 * 1024
EXTREME_IDLE_SECONDS = 120
DEFAULT_ROOT = Path(os.environ.get("QUARTERMASTER_HOME", str(Path.home() / "quartermaster")))
BANDS = {"2.4": {"2.4"}, "5": {"5"}, "6": {"6"}, "5+6": {"5", "6"}}
SUMMARY_COLUMNS = ("MAC", "Type", "Name", "Security", "Channel", "FrequencyMHz", "Band", "FirstSeen", "LastSeen",
                   "Sightings", "LogRows", "StrongestRSSI", "StrongestLatitude", "StrongestLongitude",
                   "StrongestTime", "WeakestRSSI", "WeakestLatitude", "WeakestLongitude", "WeakestTime")
_MAC = re.compile(r"(?:[0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}")


def gps_epoch(fix, fallback):
    """UTC from the GPS when it has it: the CardputerZero has no battery clock."""
    utc = fix.get("utc") if fix else None
    if utc:
        try:
            from datetime import datetime
            return datetime.fromisoformat(utc.replace("Z", "+00:00")).timestamp()
        except ValueError:
            pass
    return fallback


def _utc(epoch):
    return time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(epoch)) if epoch else ""


def _record(device):
    return {"mac": device.mac, "name": device.name, "capabilities": device.capabilities,
            "channel": device.channel, "frequency": device.frequency, "mfgr": device.extra}


class Wardrive:
    def __init__(self, cfg, gps, root=DEFAULT_ROOT, adapters=None, ble=None, fleet=WifiFleet, clock=time.time):
        self.cfg = dict(cfg)
        self.gps = gps
        self.root = Path(root)
        self.logs = self.root / "logs"
        self.clock = clock
        self.state = "idle"
        self.error = ""
        self.adapters = adapters
        self.ble = ble
        self.fleet_class = fleet
        self.fleet = None
        self.tracker = Tracker(self.cfg.get("relog_seconds", 30), self.cfg.get("relog_meters", 25),
                               self.cfg.get("rssi_step", 4), accuracy=wigle.accuracy_m)
        self.writer = None
        self.log_paths = []
        self.rows = 0
        self.started_at = None
        self.skipped_no_fix = 0
        self.ble_error = ""
        ignore = [str(item).strip() for item in self.cfg.get("ignore", ()) if str(item).strip()]
        self.ignore_macs = {item.upper().replace("-", ":") for item in ignore if _MAC.fullmatch(item)}
        self.ignore_names = {item for item in ignore if not _MAC.fullmatch(item)}
        self._bands = BANDS.get(self.cfg.get("wifi_bands", "all"))
        self._stop = threading.Event()
        self._threads = []
        self._lock = threading.Lock()
        self._hotplug = False

    @property
    def log_path(self):
        return self.log_paths[0] if self.log_paths else None

    def active_log(self):
        """The file being written right now, which must not be uploaded yet."""
        writer = self.writer
        return writer.path if writer is not None and self.state == "running" else None

    # -- lifecycle -------------------------------------------------------
    def start(self):
        if self.state == "running":
            return
        self.logs.mkdir(parents=True, exist_ok=True, mode=0o700)
        if shutil.disk_usage(self.logs).free < MIN_FREE_BYTES:
            raise RuntimeError("Storage is almost full (under 32 MB free)")
        self._hotplug = self.adapters is None
        if self.adapters is None:
            self.adapters = hardware.wifi_adapters()
        chosen = self._chosen_adapters()
        if not chosen and not self.cfg.get("ble", True):
            raise RuntimeError("No Wi-Fi adapter found and Bluetooth is off")
        radios.claim("wardrive")
        self._stop.clear()
        self.started_at = self.clock()
        self.fleet = self.fleet_class(chosen, self._wifi_sink, self.cfg.get("wifi_pause", 0.0))
        self.state = "running"
        self.fleet.start()
        self._threads = [threading.Thread(target=self._housekeeping, daemon=True, name="wardrive-housekeeping")]
        if self.cfg.get("ble", True):
            if self.ble is None:
                self.ble = BleScanner()
            self._threads.append(threading.Thread(target=self._ble_loop, daemon=True, name="ble"))
        for thread in self._threads:
            thread.start()

    def _housekeeping(self):
        """Pick up adapters plugged in or pulled out, and write the extremes of devices left behind."""
        ticks = 0
        while not self._stop.wait(4.0):
            ticks += 1
            if self._hotplug and hasattr(self.fleet, "refresh"):
                try:
                    self.adapters = hardware.wifi_adapters()
                    self.fleet.refresh(self._chosen_adapters())
                except OSError:
                    pass
            if ticks % 15 == 0:
                self._flush_extremes(gps_epoch(self.gps.snapshot(), self.clock()) - EXTREME_IDLE_SECONDS)

    def _chosen_adapters(self):
        mode = self.cfg.get("wifi_adapters", "all")
        adapters = [a for a in self.adapters if a.get("bands") or not a.get("phy")]
        if mode == "external":
            external = [a for a in adapters if a["external"]]
            return external or adapters
        if mode == "internal":
            return [a for a in adapters if not a["external"]]
        if isinstance(mode, (list, tuple)):
            return [a for a in adapters if a["interface"] in mode]
        return adapters

    def stop(self):
        if self.state != "running":
            return
        self._stop.set()
        if self.fleet is not None:
            self.fleet.stop()
        for thread in self._threads:
            thread.join(timeout=5)
        if self.ble is not None:
            self.ble.close()
        self._flush_extremes()
        with self._lock:
            if self.writer is not None:
                self.writer.close()
                self.writer = None
        self.state = "stopped"
        radios.release("wardrive")
        self._write_summary()

    # -- logging ---------------------------------------------------------
    def _append(self, row, epoch):
        with self._lock:
            try:
                if self.writer is not None and self.writer.size >= ROTATE_BYTES:
                    self.writer.close()
                    self.writer = None
                if self.writer is None:
                    path = wigle.new_log_path(self.logs, epoch)
                    self.writer = wigle.WigleWriter(path, hardware.os_release())
                    self.log_paths.append(path)
                self.writer.write_row(row)
                self.rows += 1
                self.error = ""
                return True
            except OSError as exc:
                self.error = "Cannot write the log: %s" % (exc.strerror or exc)
                return False

    def _log(self, kind, record, rssi, fix, epoch):
        row = wigle.WigleWriter.row(kind, record, rssi, fix, epoch)
        return row is not None and self._append(row, epoch)

    def _flush_extremes(self, idle_before=None):
        """Give every device's exact strongest and weakest sighting a row of its own."""
        for device, point in self.tracker.take_extremes(idle_before):
            self._log(device.kind, _record(device), point[0], wigle.point_fix(point), point[3])

    def _position(self):
        """The fix, the time to stamp rows with, and the fix again only if it may be logged."""
        fix = self.gps.snapshot()
        now = gps_epoch(fix, self.clock())
        return fix, now, fix if wigle.usable(fix, now, self.cfg.get("gps_max_hdop") or None) else None

    # -- sinks -----------------------------------------------------------
    def _wifi_sink(self, networks, adapter):
        _, now, fix = self._position()
        min_rssi = self.cfg.get("min_rssi", -100)
        for net in networks:
            if net["rssi"] < min_rssi or (self._bands and net["band"] not in self._bands):
                continue
            if not net["ssid"] and not self.cfg.get("log_hidden", True):
                continue
            ignored = net["bssid"] in self.ignore_macs or net["ssid"] in self.ignore_names
            device, _, reason = self.tracker.observe(
                WIFI, net["bssid"], net["rssi"], now, fix, name=net["ssid"], security=net["security"],
                capabilities=net["capabilities"], channel=net["channel"], frequency=net["frequency"],
                band=net["band"], ignored=ignored)
            if reason:
                self._log(WIFI, _record(device), net["rssi"], fix, now)
            elif fix is None:
                self.skipped_no_fix += 1

    def _ble_loop(self):
        interval = self.cfg.get("ble_interval", 1.0)
        last_rssi = {}
        while not self._stop.is_set():
            try:
                seen = self.ble.scan()
                self.ble_error = ""
            except ScanUnavailable as exc:
                self.ble_error = str(exc)
                self._stop.wait(5)
                continue
            _, now, fix = self._position()
            include_random = self.cfg.get("ble_random", True)
            min_rssi = self.cfg.get("min_rssi", -100)
            for item in seen:
                mac = item["address"]
                # BlueZ keeps a device listed for a while; only a changed RSSI is a new sighting.
                if not mac or last_rssi.get(mac) == item["rssi"]:
                    continue
                last_rssi[mac] = item["rssi"]
                if item["rssi"] < min_rssi or (item.get("address_type", 1) != 0 and not include_random):
                    continue
                kind = BT if item.get("classic") else BLE
                manufacturer = item.get("manufacturer") or {}
                name = item.get("name") or ""
                device, _, reason = self.tracker.observe(
                    kind, mac, item["rssi"], now, fix, name=name, extra=min(manufacturer) if manufacturer else None,
                    capabilities="Misc [BT]" if kind == BT else "Misc [LE]",
                    ignored=mac in self.ignore_macs or (name and name in self.ignore_names))
                if reason:
                    self._log(kind, _record(device), item["rssi"], fix, now)
            if len(last_rssi) > 20000:
                last_rssi.clear()
            self._stop.wait(interval)

    # -- reporting -------------------------------------------------------
    def status(self):
        fix = self.gps.snapshot()
        summary = self.tracker.summary()
        hdop, limit = fix.get("hdop"), self.cfg.get("gps_max_hdop") or None
        summary.update(state=self.state, rows=self.rows, files=len(self.log_paths),
                       log=str(self.log_path) if self.log_path else "", skipped_no_fix=self.skipped_no_fix,
                       elapsed=(self.clock() - self.started_at) if self.started_at else 0,
                       adapters=self.fleet.status() if self.fleet else [], ble_error=self.ble_error,
                       error=self.error, gps=fix,
                       gps_weak=bool(fix.get("valid") and limit and isinstance(hdop, (int, float)) and hdop > limit))
        return summary

    def _write_summary(self):
        """A spreadsheet next to the log: one line per device with its strongest and weakest sighting."""
        if self.log_path is None:
            return None
        path = self.log_path.with_name(self.log_path.name.replace(".wigle.csv", ".summary.csv"))
        tmp = path.with_name("." + path.name)
        try:
            fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8", newline="") as out:
                writer = csv.writer(out, lineterminator="\n")
                writer.writerow(SUMMARY_COLUMNS)
                for d in sorted(self.tracker.recent(limit=10 ** 9), key=lambda d: d.first_seen):
                    if d.ignored:
                        continue
                    best, worst = d.best or (), d.worst or ()
                    writer.writerow([d.mac.lower(), d.kind, wigle._text(d.name), d.security, d.channel or "",
                                     d.frequency or "", d.band, _utc(d.first_seen), _utc(d.last_seen), d.sightings,
                                     d.rows, *_extreme(best), *_extreme(worst)])
            os.replace(tmp, path)
        except OSError:
            return None
        return path


def _extreme(point):
    if not point:
        return ["", "", "", ""]
    return [int(round(point[0])), "%.7f" % point[1], "%.7f" % point[2], _utc(point[3])]
