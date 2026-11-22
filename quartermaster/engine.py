"""The wardrive: scanners in, deduplicated devices and a WiGLE log out.

Only one mode runs at a time. `Wardrive` owns every radio while it runs, so
Recon can never take scan time away from it.
"""

import json
import os
from pathlib import Path
import shutil
import threading
import time

from . import __version__, hardware, wigle
from .ble import BleScanner
from .tracker import BLE, BT, WIFI, Tracker
from .wifi import ScanUnavailable, WifiFleet

MIN_FREE_BYTES = 32 * 1024 * 1024
DEFAULT_ROOT = Path(os.environ.get("QUARTERMASTER_HOME", str(Path.home() / "quartermaster")))


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


class Wardrive:
    def __init__(self, cfg, gps, root=DEFAULT_ROOT, adapters=None, ble=None, fleet=WifiFleet, clock=time.time):
        self.cfg = dict(cfg)
        self.gps = gps
        self.root = Path(root)
        self.clock = clock
        self.state = "idle"
        self.error = ""
        self.adapters = adapters
        self.ble = ble
        self.fleet_class = fleet
        self.fleet = None
        self.tracker = Tracker(self.cfg.get("relog_seconds", 30), self.cfg.get("relog_meters", 25),
                               self.cfg.get("rssi_step", 4))
        self.writer = None
        self.log_path = None
        self.started_at = None
        self.skipped_no_fix = 0
        self.ble_error = ""
        self._stop = threading.Event()
        self._ble_thread = None
        self._lock = threading.Lock()
        self._pending_name = None

    # -- lifecycle -------------------------------------------------------
    def start(self):
        if self.state == "running":
            return
        logs = self.root / "logs"
        logs.mkdir(parents=True, exist_ok=True, mode=0o700)
        if shutil.disk_usage(logs).free < MIN_FREE_BYTES:
            raise RuntimeError("Storage is almost full (under 32 MB free)")
        if self.adapters is None:
            self.adapters = hardware.wifi_adapters()
        chosen = self._chosen_adapters()
        if not chosen and not self.cfg.get("ble", True):
            raise RuntimeError("No Wi-Fi adapter found and Bluetooth is off")
        self._stop.clear()
        self.started_at = self.clock()
        # The file gets its name from GPS time once there is a fix, so the name is never 1970.
        self._pending_name = logs
        self.fleet = self.fleet_class(chosen, self._wifi_sink, self.cfg.get("wifi_pause", 0.0))
        self.fleet.start()
        if self.cfg.get("ble", True):
            if self.ble is None:
                self.ble = BleScanner()
            self._ble_thread = threading.Thread(target=self._ble_loop, daemon=True, name="ble")
            self._ble_thread.start()
        self.state = "running"

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
        if self._ble_thread is not None:
            self._ble_thread.join(timeout=5)
        if self.ble is not None:
            self.ble.close()
        with self._lock:
            if self.writer is not None:
                self.writer.close()
                self._write_summary()
        self.state = "stopped"

    # -- sinks -----------------------------------------------------------
    def _open_writer(self, epoch):
        self.log_path = self._pending_name / wigle.log_filename(epoch)
        self.writer = wigle.WigleWriter(self.log_path, hardware.os_release())

    def _log(self, kind, record, rssi, fix, epoch):
        with self._lock:
            if self.writer is None:
                if epoch < wigle.MIN_EPOCH:
                    return False
                self._open_writer(epoch)
            return self.writer.write(kind, record, rssi, fix, epoch)

    def _wifi_sink(self, networks, adapter):
        fix = self.gps.snapshot()
        now = gps_epoch(fix, self.clock())
        min_rssi = self.cfg.get("min_rssi", -100)
        for net in networks:
            if net["rssi"] < min_rssi:
                continue
            device, _, reason = self.tracker.observe(
                WIFI, net["bssid"], net["rssi"], now, fix, name=net["ssid"], security=net["security"],
                capabilities=net["capabilities"], channel=net["channel"], frequency=net["frequency"],
                band=net["band"])
            if reason:
                self._log(WIFI, {"mac": device.mac, "name": device.name, "capabilities": device.capabilities,
                                 "channel": device.channel, "frequency": device.frequency}, net["rssi"], fix, now)
            elif not fix.get("valid"):
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
            fix = self.gps.snapshot()
            now = gps_epoch(fix, self.clock())
            include_random = self.cfg.get("ble_random", True)
            for item in seen:
                mac = item["address"]
                # BlueZ keeps a device listed for a while; only a changed RSSI is a new sighting.
                if not mac or last_rssi.get(mac) == item["rssi"]:
                    continue
                last_rssi[mac] = item["rssi"]
                if item.get("address_type", 1) != 0 and not include_random:
                    continue
                kind = BT if item.get("classic") else BLE
                manufacturer = item.get("manufacturer") or {}
                mfgr = min(manufacturer) if manufacturer else None
                device, _, reason = self.tracker.observe(kind, mac, item["rssi"], now, fix, name=item.get("name"),
                                                         capabilities="Misc [BT]" if kind == BT else "Misc [LE]",
                                                         extra=mfgr)
                if reason:
                    self._log(kind, {"mac": mac, "name": device.name, "capabilities": device.capabilities,
                                     "mfgr": device.extra}, item["rssi"], fix, now)
            if len(last_rssi) > 20000:
                last_rssi.clear()
            self._stop.wait(interval)

    # -- reporting -------------------------------------------------------
    def status(self):
        summary = self.tracker.summary()
        summary.update(state=self.state, rows=self.writer.rows if self.writer else 0,
                       log=str(self.log_path) if self.log_path else "", skipped_no_fix=self.skipped_no_fix,
                       elapsed=(self.clock() - self.started_at) if self.started_at else 0,
                       adapters=self.fleet.status() if self.fleet else [], ble_error=self.ble_error,
                       gps=self.gps.snapshot())
        return summary

    def _write_summary(self):
        """A sidecar next to the CSV: totals plus every device's strongest and weakest sighting."""
        if self.log_path is None:
            return
        data = {"app": "Quartermaster", "version": __version__, "log": self.log_path.name,
                "started": self.started_at, "ended": self.clock(), "summary": self.tracker.summary(),
                "devices": [d.as_dict() for d in self.tracker.recent(limit=10 ** 9)]}
        path = self.log_path.with_suffix("").with_suffix(".devices.json")
        tmp = path.with_name("." + path.name)
        with open(tmp, "w", encoding="utf-8") as out:
            json.dump(data, out, ensure_ascii=True, allow_nan=False)
        os.replace(tmp, path)
