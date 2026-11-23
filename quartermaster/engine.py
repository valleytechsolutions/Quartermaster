"""The wardrive: scanners in, deduplicated devices and WiGLE logs out."""

from collections import deque
import csv
import os
from pathlib import Path
import queue
import shutil
import threading
import time

from . import adsb, faults, hardware, radios, wigle
from .config import is_mac
from .ble import BleScanner
from .tracker import BLE, BT, WIFI, Tracker
from .wifi import ScanUnavailable, WifiFleet

MIN_FREE_BYTES = 32 * 1024 * 1024
# Every upload site takes a file this size: Wardrift stops at 10 MiB, WDGWars at 15 MB.
ROTATE_BYTES = 8 * 1024 * 1024
EXTREME_IDLE_SECONDS = 120
# A wireless node is somewhere within radio range of the hub, not at the hub.
AIR_RANGE_M = 50.0
FIX_HISTORY = 40
UPKEEP_SECONDS = 4.0
# Devices kept in memory: about 1 KB each with full tracks and 1.3 KB with closest + furthest. The CardputerZero
# leaves about 350 MB to programs.
MAX_TRACKED = 100_000
MAX_TRACKED_EXTREMES = 60_000
DEFAULT_ROOT = Path(os.environ.get("QUARTERMASTER_HOME", str(Path.home() / "quartermaster")))
BANDS = {"2.4": {"2.4"}, "5": {"5"}, "6": {"6"}, "5+6": {"5", "6"}}
SUMMARY_COLUMNS = ("MAC", "Type", "Name", "Security", "Channel", "FrequencyMHz", "Band", "FirstSeen", "LastSeen",
                   "Sightings", "LogRows", "StrongestRSSI", "StrongestLatitude", "StrongestLongitude",
                   "StrongestTime", "WeakestRSSI", "WeakestLatitude", "WeakestLongitude", "WeakestTime")


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


def _summary_row(d, tracker):
    best, worst, rows = d.best, d.worst, tracker.log_rows(d)
    if d.ends is not None:
        chosen = d.ends.chosen()
        best, worst = chosen[0], chosen[-1]
    return [d.mac.lower(), d.kind, wigle._text(d.name), d.security, d.channel or "", d.frequency or "", d.band,
            _utc(d.first_seen), _utc(d.last_seen), d.sightings, rows, *_extreme(best), *_extreme(worst)]


def _settle(folder):
    """A file's new name lasts through a power cut only once its folder is on disk as well."""
    try:
        fd = os.open(str(folder), os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def _summary_path(log):
    return log.with_name(log.name.replace(".wigle.csv", ".summary.csv"))


def _compact(journal, names=None):
    """Finish a log from its journal and remove the journal. Returns the rows in the log of each (type, mac)."""
    kept = wigle.compact(journal, wigle.log_of(journal), names)
    if kept:
        _settle(journal.parent)
    try:
        journal.unlink(missing_ok=True)
    except OSError:
        pass
    return kept


_recovering = threading.Lock()
_writing = set()


def ignoring(items):
    """The addresses and the names on an ignore list, which are shown but never logged."""
    items = [str(item).strip() for item in items if str(item).strip()]
    macs = {item.upper().replace("-", ":") for item in items if is_mac(item)}
    return macs, {item for item in items if not is_mac(item)}


def ignored_names(items):
    """A names function for compact() that leaves out what the ignore list names."""
    macs, names = ignoring(items)

    def named(kind, mac, ssid, auth):
        return None if mac.upper() in macs or ssid in names else (ssid, auth)
    return named


def leftovers(logs):
    """Whether a crash or a power cut left a log to finish."""
    try:
        return any(Path(logs).glob(wigle.JOURNAL_GLOB)) or any(Path(logs).glob(adsb.JOURNAL_GLOB))
    except OSError:
        return False


def recover(logs, names=None):
    """Finish the logs of a wardrive that a crash or a power cut ended before its journal was compacted. Returns
    how many logs were finished. Only one runs at a time, and never while a wardrive writes to the folder."""
    with _recovering:
        if os.path.realpath(str(logs)) in _writing:
            return 0
        return _recover(Path(logs), names)


def _recover(logs, names):
    done = 0
    for journal in sorted(logs.glob(wigle.JOURNAL_GLOB)):
        try:
            if wigle.log_of(journal).exists():
                journal.unlink()
            elif _compact(journal, names):
                done += 1
        except OSError:
            faults.note("Finishing an earlier log")
    for leftover in logs.glob(wigle.JOURNAL_GLOB.replace(".part", ".tmp")):
        try:
            leftover.unlink(missing_ok=True)
        except OSError:
            pass
    try:
        done += len(adsb.recover(logs))
    except OSError:
        faults.note("Finishing an earlier aircraft log")
    return done


def _record(device):
    return {"mac": device.mac, "name": device.name, "capabilities": device.capabilities,
            "channel": device.channel, "frequency": device.frequency, "mfgr": device.extra}


class Wardrive:
    def __init__(self, cfg, gps, root=DEFAULT_ROOT, adapters=None, ble=None, fleet=WifiFleet, clock=time.time,
                 nodes=None, receiver=adsb.Receiver):
        self.cfg = dict(cfg)
        self.nodes = nodes
        self.gps = gps
        self.root = Path(root)
        self.logs = self.root / "logs"
        self.clock = clock
        self.state = "idle"
        self.error = self.failed = ""
        self.adapters = adapters
        self.ble = ble
        self.fleet_class = fleet
        self.fleet = None
        self.receiver = receiver
        self.aircraft = None
        self.aircraft_path = None
        self.extremes = self.cfg.get("log_rows", "extremes") != "track"
        self.tracker = Tracker(self.cfg.get("relog_seconds", 30), self.cfg.get("relog_meters", 25),
                               self.cfg.get("rssi_step", 4), accuracy=wigle.accuracy_m, extremes=self.extremes)
        self.writer = None
        self.log_paths = []
        self._taken = set()
        self._aside = None
        self.rows = 0
        self._finishing = 0
        self._fixes = deque(maxlen=FIX_HISTORY)
        self._parts = queue.Queue()
        self._finisher = None
        self._started = self._stopped = None
        self.skipped_no_fix = 0
        self.ble_error = ""
        self.set_ignore(self.cfg.get("ignore", ()))
        self._bands = BANDS.get(self.cfg.get("wifi_bands", "all"))
        self._stop = threading.Event()
        self._threads = []
        self._lock = threading.Lock()
        self._hotplug = False

    @property
    def log_path(self):
        paths = self.log_paths
        return paths[0] if paths else None

    def set_ignore(self, items):
        """Names and addresses that are shown but never logged. Takes effect at once."""
        self.ignore_macs, self.ignore_names = ignoring(items)

    def elapsed(self):
        if self._started is None:
            return 0
        return (self._stopped if self._stopped is not None else time.monotonic()) - self._started

    def active_log(self):
        """The file being written right now, which must not be uploaded yet. With extremes, that is the journal."""
        writer = self.writer
        return writer.path if writer is not None and self.state == "running" else None

    def start(self):
        if self.state == "running":
            return
        self.logs.mkdir(parents=True, exist_ok=True, mode=0o700)
        for leftover in self.logs.glob(".*.summary.csv*"):
            try:
                leftover.unlink(missing_ok=True)
            except OSError:
                pass
        self._recover()
        try:
            self._begin()
        except BaseException:
            if self.state != "running":
                _writing.discard(os.path.realpath(str(self.logs)))
            raise

    def _begin(self):
        if shutil.disk_usage(self.logs).free < MIN_FREE_BYTES:
            raise RuntimeError("Storage is almost full (under 32 MB free)")
        self._hotplug = self.adapters is None
        if self.adapters is None:
            self.adapters = hardware.wifi_adapters()
        chosen = self._chosen_adapters()
        if not chosen and not self.cfg.get("ble", True) and not self.cfg.get("adsb") and \
                not (self.nodes is not None and self.nodes.in_use()):
            raise RuntimeError("No Wi-Fi adapter found and Bluetooth is off")
        radios.claim("wardrive")
        self._stop.clear()
        self._started, self._stopped = time.monotonic(), None
        self.fleet = self.fleet_class(chosen, self._wifi_sink, self.cfg.get("wifi_pause", 0.0))
        self.state = "running"
        self.fleet.start()
        if self.cfg.get("adsb"):
            self.aircraft = self.receiver(self.logs, self.gps, clock=self.clock, log=lambda: self.log_path,
                                          max_hdop=self.cfg.get("gps_max_hdop") or None,
                                          epoch=lambda fix: gps_epoch(fix, self.clock()))
            self.aircraft.start(gps_epoch(self.gps.snapshot(), self.clock()))
        if self.nodes is not None:
            self.nodes.attach(self, self._wifi_sink, self._node_ble, ble=self.cfg.get("ble", True),
                              bands=self._bands)
        self._threads = [threading.Thread(target=self._housekeeping, daemon=True, name="wardrive-housekeeping")]
        if self.cfg.get("ble", True):
            if self.ble is None:
                self.ble = BleScanner()
            self._threads.append(threading.Thread(target=self._ble_loop, daemon=True, name="ble"))
        for thread in self._threads:
            thread.start()

    def leftovers(self):
        return leftovers(self.logs)

    def _recover(self):
        """Finish what an earlier wardrive left, and keep any other recovery away from this folder until the stop."""
        with _recovering:
            _recover(self.logs, self._names)
            _writing.add(os.path.realpath(str(self.logs)))

    def _housekeeping(self):
        """Put waiting rows on disk, follow adapters plugged in or pulled out, write the extremes of devices left."""
        ticks = 0
        while not self._stop.wait(UPKEEP_SECONDS):
            ticks += 1
            try:
                self._sync()
                self._follow_adapters()
                if ticks % 15 == 0:
                    self._tidy()
            except Exception:
                faults.note("Wardrive upkeep")

    def _sync(self):
        """Rows wait in memory for the next one. Where nothing new is heard, that row may never come."""
        with self._lock:
            try:
                if self.writer is not None:
                    self.writer.sync()
            except OSError as exc:
                self._refused(exc)

    def _refused(self, exc):
        """The log took no more. Its file is given up, the next row starts a new one, and the summary will say so.

        Call this with the lock held.
        """
        self.error = self.failed = "Cannot write the log: %s" % (exc.strerror or exc)
        writer = self.writer
        if writer is None:
            return
        try:
            lost = writer.abandon(exc)
        finally:
            self.writer = None
        if self.extremes:
            self._finish(writer)
            return
        self.rows -= lost
        if lost == writer.rows:
            self._drop(writer.path)

    def _finish(self, writer):
        """Compact the journal of a part that has ended into its log. Call this with the lock held.

        A journal that cannot be compacted stays, and the next start finishes it.
        """
        self.tracker.reset_part()
        self._finished(writer.path, *self._compacted(writer.path))

    def _compacted(self, journal):
        try:
            return _compact(journal, self._names), None
        except OSError as exc:
            return {}, exc

    def _finished(self, journal, kept, exc):
        """Count the rows of a finished part. Call this with the lock held."""
        if exc is not None:
            self.error = self.failed = "Cannot write the log: %s" % (exc.strerror or exc)
        self.tracker.add_rows(kept)
        self.rows += sum(kept.values())
        if not kept:
            path = wigle.log_of(journal)
            self.log_paths = [other for other in self.log_paths if other != path]

    def _rotate(self, writer):
        """A part has reached the rotation size: it is finished away from the lock, so that no scan waits on it.
        Call this with the lock held."""
        waiting = self.tracker.reset_part()
        self._finishing += waiting
        self._parts.put((writer.path, waiting))
        if self._finisher is None:
            self._finisher = threading.Thread(target=self._finish_parts, daemon=True, name="wardrive-parts")
            self._finisher.start()

    def _finish_parts(self):
        while True:
            item = self._parts.get()
            try:
                if item is None:
                    return
                journal, waiting = item
                kept, exc = self._compacted(journal)
                with self._lock:
                    self._finishing -= waiting
                    self._finished(journal, kept, exc)
            except Exception:
                faults.note("Finishing a part of the log")
            finally:
                self._parts.task_done()

    def _parts_done(self):
        """Wait until every part handed over has been finished."""
        if self._finisher is not None:
            self._parts.put(None)
            self._finisher.join()
            self._finisher = None

    def _names(self, kind, mac, ssid, auth):
        """The name and capabilities a device's rows are finished with, or None when it is not to be logged."""
        device = self.tracker.find(kind, mac)
        if device is not None:
            ssid, auth = wigle._text(device.name) or ssid, device.capabilities or auth
        if mac.upper() in self.ignore_macs or ssid in self.ignore_names:
            return None
        return ssid, auth

    def _drop(self, path):
        """A part left without a single row is no log, even where it cannot be removed: the next start clears it.

        The list is replaced, for whoever is reading it.
        """
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
        self.log_paths = [kept for kept in self.log_paths if kept != path]

    def _tidy(self):
        """Write the extremes of devices left behind and, on a very long drive, make room in memory."""
        idle = gps_epoch(self.gps.snapshot(), self.clock()) - EXTREME_IDLE_SECONDS
        self._flush_extremes(idle)
        limit = min(MAX_TRACKED, MAX_TRACKED_EXTREMES) if self.extremes else MAX_TRACKED
        self._set_aside(self.tracker.forget(limit, idle))

    def _follow_adapters(self):
        if self._hotplug and hasattr(self.fleet, "refresh"):
            try:
                self.adapters = hardware.wifi_adapters()
                self.fleet.refresh(self._chosen_adapters())
            except OSError:
                pass

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
        if self.nodes is not None:
            self.nodes.detach(self)
        if self.fleet is not None:
            self.fleet.stop()
        if self.aircraft is not None:
            self.aircraft.stop()
        for thread in self._threads:
            thread.join(timeout=5)
        if self.ble is not None:
            self.ble.close()
        self._flush_extremes()
        self._parts_done()
        with self._lock:
            try:
                if self.writer is not None:
                    writer = self.writer
                    self.writer.close()
                    self.writer = None
                    if self.extremes:
                        self._finish(writer)
            except OSError as exc:
                self._refused(exc)
            self._stopped = time.monotonic()
            self.state = "stopped"
        radios.release("wardrive")
        self._write_summary()
        self._write_aircraft()
        _writing.discard(os.path.realpath(str(self.logs)))

    def _write_aircraft(self):
        if self.aircraft is None:
            return
        try:
            self.aircraft_path = self.aircraft.finish()
        except (OSError, ValueError) as exc:
            self.failed = self.failed or "Cannot write the aircraft log: %s" % (getattr(exc, "strerror", None) or exc)

    def _append(self, row, epoch):
        with self._lock:
            if self.state != "running":
                return False
            try:
                if not self.extremes and self.writer is not None and self.writer.size >= ROTATE_BYTES:
                    self.writer.close()
                    self.writer = None
                if self.writer is None:
                    if self.extremes:
                        path = wigle.new_log_path(self.logs, epoch, self._taken)
                        self.writer = wigle.WigleWriter(wigle.journal_path(path), hardware.os_release())
                        self._taken.add(path.name)
                    else:
                        path = wigle.new_log_path(self.logs, epoch)
                        self.writer = wigle.WigleWriter(path, hardware.os_release())
                    self.log_paths.append(path)
                    _settle(self.logs)
                self.writer.write_row(row)
                if not self.extremes:
                    self.rows += 1
                elif self.writer.size >= ROTATE_BYTES:
                    self._ends_to(self.writer)
                    writer = self.writer
                    writer.close()
                    self.writer = None
                    self._rotate(writer)
                self.error = ""
                return True
            except OSError as exc:
                self._refused(exc)
                return False

    def _ends_to(self, writer):
        """Before a part is finished, give it the exact strongest and weakest sightings it does not hold yet."""
        for device, point in self.tracker.take_extremes():
            row = wigle.WigleWriter.row(device.kind, _record(device), point[0], wigle.point_fix(point), point[3])
            if row is not None:
                writer.write_row(row)

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
        usable = fix if wigle.usable(fix, now, self.cfg.get("gps_max_hdop") or None) else None
        fixes = self._fixes
        if usable is not None and (not fixes or fixes[-1][0] != now or fixes[-1][1] != usable):
            fixes.append((now, usable))
        return fix, now, usable

    def _placed(self, record, now, fix):
        """When and where a node heard what it reports "lag" seconds late: the fix nearest that moment, its accuracy
        widened by the way driven between the two, and by the radio range of a wireless node."""
        lag = record.get("lag")
        if lag is None:
            return now, fix
        when = now - lag
        if fix is None or not self._fixes:
            return when, None
        gap, found = min(((abs(epoch - when), earlier) for epoch, earlier in self._fixes), key=lambda item: item[0])
        spread = (found.get("speed") or 0.0) * gap + (AIR_RANGE_M if record.get("air") else 0.0)
        accuracy = wigle.accuracy_m(found)
        if spread and accuracy != "":
            found = dict(found, accuracy=round(accuracy + spread, 1))
        return when, found

    def _wifi_sink(self, networks, adapter):
        _, now, fix = self._position()
        min_rssi = self.cfg.get("min_rssi", -100)
        for net in networks:
            try:
                self._wifi_seen(net, *self._placed(net, now, fix), min_rssi)
            except Exception:
                faults.note("Logging a Wi-Fi network")

    def _wifi_seen(self, net, now, fix, min_rssi):
        if net["rssi"] < min_rssi or (self._bands and net["band"] not in self._bands):
            return
        if not net["ssid"] and not self.cfg.get("log_hidden", True):
            return
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
                self._ble_sink(seen, last_rssi)
            except ScanUnavailable as exc:
                self.ble_error = str(exc)
                self._stop.wait(5)
                continue
            except Exception:
                faults.note("Bluetooth scan")
                self.ble_error = "Bluetooth scan failed. Trying again."
                self._stop.wait(5)
                continue
            if len(last_rssi) > 20000:
                last_rssi.clear()
            self._stop.wait(interval)

    def _node_ble(self, seen):
        """A node reports a device again only when it has something new to say."""
        self._ble_sink(seen, {})

    def _ble_sink(self, seen, last_rssi):
        _, now, fix = self._position()
        include_random = self.cfg.get("ble_random", True)
        min_rssi = self.cfg.get("min_rssi", -100)
        for item in seen:
            try:
                self._ble_seen(item, last_rssi, *self._placed(item, now, fix), include_random, min_rssi)
            except Exception:
                faults.note("Logging a Bluetooth device")

    def _ble_seen(self, item, last_rssi, now, fix, include_random, min_rssi):
        mac = item["address"]
        # BlueZ keeps a device listed for a while; only a changed RSSI is a new sighting.
        if not is_mac(mac) or last_rssi.get(mac) == item["rssi"]:
            return
        last_rssi[mac] = item["rssi"]
        if item["rssi"] < min_rssi or (item.get("address_type", 1) != 0 and not include_random):
            return
        kind = BT if item.get("classic") else BLE
        manufacturer = item.get("manufacturer") or {}
        name = (item.get("name") or "")[:248]
        device, _, reason = self.tracker.observe(
            kind, mac, item["rssi"], now, fix, name=name, extra=min(manufacturer) if manufacturer else None,
            capabilities="Misc [BT]" if kind == BT else "Misc [LE]",
            ignored=mac in self.ignore_macs or (name and name in self.ignore_names))
        if reason:
            self._log(kind, _record(device), item["rssi"], fix, now)

    def status(self):
        fix = self.gps.snapshot()
        summary = self.tracker.summary()
        hdop, limit = fix.get("hdop"), self.cfg.get("gps_max_hdop") or None
        paths = self.log_paths
        kept = self.tracker.kept if self.extremes and self.writer is not None else 0
        summary.update(state=self.state, rows=self.rows + self._finishing + kept, files=len(paths),
                       log=str(paths[0]) if paths else "", skipped_no_fix=self.skipped_no_fix,
                       elapsed=self.elapsed(),
                       aircraft=self.aircraft.count if self.aircraft is not None else None,
                       adsb=self.aircraft.status() if self.aircraft is not None else None,
                       aircraft_log=str(self.aircraft_path) if self.aircraft_path else "",
                       adapters=self.radio_status(), ble_error=self.ble_error,
                       error=self.error, failed=self.failed, gps=fix,
                       gps_weak=bool(fix.get("valid") and limit and isinstance(hdop, (int, float)) and hdop > limit))
        return summary

    def radio_status(self):
        """The radios of the wardrive: the Wi-Fi adapters, then the nodes."""
        found = self.fleet.status() if self.fleet else []
        return found + (self.nodes.adapters() if self.nodes is not None and self.fleet else [])

    def _set_aside(self, devices):
        """Summary lines of devices dropped from memory wait in a file until the wardrive ends."""
        devices, log = [d for d in devices if not d.ignored], self.log_path
        if not devices or log is None and self._aside is None:
            return
        if self._aside is None:
            path = _summary_path(log)
            self._aside = path.with_name("." + path.name + ".part")
        try:
            _mend(self._aside)
            fd = os.open(str(self._aside), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            with os.fdopen(fd, "a", encoding="utf-8", newline="") as out:
                csv.writer(out, lineterminator="\n").writerows(_summary_row(d, self.tracker) for d in devices)
        except OSError as exc:
            self.failed = self.failed or "Cannot write the summary: %s" % (exc.strerror or exc)

    def _write_summary(self):
        """A spreadsheet next to the log: one line per device with its strongest and weakest sighting."""
        log = self.log_path
        if log is None:
            return
        path = _summary_path(log)
        tmp, aside = path.with_name("." + path.name), self._aside or path.with_name("." + path.name + ".part")
        try:
            fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8", newline="") as out:
                writer = csv.writer(out, lineterminator="\n")
                writer.writerow(SUMMARY_COLUMNS)
                if aside.exists():
                    _mend(aside)
                    with open(aside, encoding="utf-8", newline="") as earlier:
                        shutil.copyfileobj(earlier, out)
                writer.writerows(_summary_row(d, self.tracker) for d in self.tracker.found() if not d.ignored)
                out.flush()
                wigle.fsync(out.fileno())
            os.replace(tmp, path)
        except (OSError, ValueError) as exc:
            self.failed = self.failed or "Cannot write the summary: %s" % (getattr(exc, "strerror", None) or exc)
        for hidden in (tmp, aside):
            try:
                hidden.unlink(missing_ok=True)
            except OSError:
                pass
        _settle(path.parent)


def _mend(path):
    """Cut off a last line that a full card left half written, so that the next line does not join it."""
    try:
        with open(path, "rb+") as part:
            data = part.read()
            if data and not data.endswith(b"\n"):
                part.truncate(data.rfind(b"\n") + 1)
    except FileNotFoundError:
        pass


def _extreme(point):
    if not point:
        return ["", "", "", ""]
    return [int(round(point[0])), "%.7f" % point[1], "%.7f" % point[2], _utc(point[3])]
