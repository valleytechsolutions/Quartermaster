import csv
import os
import stat
import time

from quartermaster import engine
from quartermaster.engine import Wardrive
from quartermaster.wifi import parse_iw_scan
from tests.fakes import QuietFleet, networks
from tests.test_core import SCAN, FIX, NOW


class FakeGPS:
    def __init__(self, fix):
        self.fix = fix

    def snapshot(self):
        return dict(self.fix)


class FakeFleet:
    def __init__(self, adapters, sink, interval):
        self.adapters, self.sink = adapters, sink

    def start(self):
        for adapter in self.adapters:
            self.sink(parse_iw_scan(SCAN), adapter)
            self.sink(parse_iw_scan(SCAN), adapter)   # same sweep again: deduplicated

    def stop(self):
        pass

    def status(self):
        return []


ADAPTERS = [{"interface": "wlan1", "external": True, "bands": ["2.4", "5", "6"], "phy": "phy1"},
            {"interface": "wlan0", "external": False, "bands": ["2.4", "5"], "phy": "phy0"}]


def rows_of(path):
    return list(csv.reader(path.read_text(encoding="utf-8").splitlines()[2:]))


def test_wardrive_dedups_across_adapters_and_logs(tmp_path):
    drive = Wardrive({"ble": False}, FakeGPS(FIX), root=tmp_path, adapters=ADAPTERS, fleet=FakeFleet,
                     clock=lambda: NOW)
    drive.start()
    status = drive.status()
    assert status["wifi"] == 3 and status["rows"] == 3 and status["bands"] == {"2.4": 1, "5": 1, "6": 1}
    assert drive.active_log() == drive.log_path
    drive.stop()
    assert drive.active_log() is None
    lines = drive.log_path.read_text().splitlines()
    assert len(lines) == 5 and drive.log_path.name == "quartermaster_20261118-110640.wigle.csv"
    summary = list(csv.DictReader((tmp_path / "logs" / "quartermaster_20261118-110640.summary.csv").open()))
    assert len(summary) == 3 and summary[0]["StrongestRSSI"] == "-61" and summary[0]["WeakestRSSI"] == "-61"
    assert summary[0]["StrongestLatitude"] == "25.1234567" and summary[0]["StrongestTime"] == "2026-11-18 11:06:40"


def test_no_fix_means_no_rows_and_no_file(tmp_path):
    drive = Wardrive({"ble": False, "wifi_adapters": "external"}, FakeGPS({"valid": False}), root=tmp_path,
                     adapters=ADAPTERS, fleet=FakeFleet, clock=lambda: NOW)
    drive.start()
    assert drive.status()["wifi"] == 3 and drive.status()["rows"] == 0 and drive.log_path is None
    assert drive.skipped_no_fix == 6
    drive.stop()
    assert list((tmp_path / "logs").iterdir()) == []


class Sweeps:
    """A fleet that replays one network at a list of signal strengths, a second apart."""

    readings = ()

    def __init__(self, adapters, sink, interval):
        self.adapters, self.sink = adapters, sink

    def start(self):
        for step, rssi in enumerate(self.readings):
            Sweeps.clock[0] = NOW + step
            self.sink([dict(NET, rssi=rssi)], self.adapters[0])

    def stop(self):
        pass

    def status(self):
        return []


NET = {"bssid": "AA:BB:CC:DD:EE:01", "ssid": "Tortuga", "rssi": -70, "channel": 6, "frequency": 2437,
       "band": "2.4", "security": "WPA2", "capabilities": "[WPA2-PSK-CCMP][ESS]"}


def run_sweeps(tmp_path, readings, cfg=None, gps=None):
    Sweeps.readings, Sweeps.clock = readings, [NOW]
    drive = Wardrive(dict({"ble": False}, **(cfg or {})), gps or FakeGPS(FIX), root=tmp_path, adapters=ADAPTERS[:1],
                     fleet=Sweeps, clock=lambda: Sweeps.clock[0])
    drive.start()
    drive.stop()
    return drive


def test_exact_strongest_and_weakest_sighting_reach_the_wigle_log(tmp_path):
    readings = list(range(-80, -49)) + list(range(-51, -96, -1))
    drive = run_sweeps(tmp_path, readings)
    rows = rows_of(drive.log_path)
    signal = [int(r[6]) for r in rows]
    assert max(signal) == -50 and min(signal) == -95 and all(len(r) == 14 for r in rows)
    strongest = next(r for r in rows if r[6] == "-50")
    assert strongest[3] == "2026-11-18 11:07:10" and strongest[7] == "25.1234567" and strongest[10] == "4.5"
    assert len(rows) == drive.rows == len(set(map(tuple, rows)))
    summary = next(csv.DictReader((tmp_path / "logs" / "quartermaster_20261118-110640.summary.csv").open()))
    assert (summary["StrongestRSSI"], summary["WeakestRSSI"], summary["Sightings"]) == ("-50", "-95", "76")


def test_logs_rotate_before_any_site_limit(tmp_path, monkeypatch):
    monkeypatch.setattr(engine, "ROTATE_BYTES", 600)
    drive = run_sweeps(tmp_path, [-90 + 4 * (i % 12) for i in range(40)], {"rssi_step": 2})
    assert len(drive.log_paths) > 1 and len({p.name for p in drive.log_paths}) == len(drive.log_paths)
    for path in drive.log_paths:
        lines = path.read_text().splitlines()
        assert lines[0].startswith("WigleWifi-1.6,") and lines[1].startswith("MAC,SSID,") and len(lines) > 2
        assert path.stat().st_size < 600 + 200
    assert sum(len(rows_of(p)) for p in drive.log_paths) == drive.rows


def test_ignored_hidden_and_band_filters(tmp_path):
    drive = run_sweeps(tmp_path, [-60, -50], {"ignore": ["Tortuga"]})
    assert drive.log_path is None and drive.status()["wifi"] == 1
    drive = run_sweeps(tmp_path, [-60], {"ignore": ["aa-bb-cc-dd-ee-01"]})
    assert drive.log_path is None
    drive = run_sweeps(tmp_path, [-60], {"wifi_bands": "5+6"})
    assert drive.status()["wifi"] == 0
    drive = run_sweeps(tmp_path, [-60], {"wifi_bands": "2.4", "min_rssi": -70})
    assert drive.status()["wifi"] == 1 and drive.rows == 1


def test_weak_gps_fix_holds_rows_back(tmp_path):
    weak = FakeGPS(dict(FIX, hdop=7.5))
    drive = run_sweeps(tmp_path, [-60], {"gps_max_hdop": 5}, weak)
    assert drive.rows == 0 and drive.skipped_no_fix == 1
    drive = run_sweeps(tmp_path, [-60], {"gps_max_hdop": 10}, weak)
    assert drive.rows == 1


def test_header_only_logs_are_pruned(tmp_path):
    from quartermaster import wigle
    empty = tmp_path / "quartermaster_20260101-000000.wigle.csv"
    wigle.WigleWriter(empty).close()
    full = tmp_path / "quartermaster_20260101-000001.wigle.csv"
    w = wigle.WigleWriter(full)
    w.write("WIFI", {"mac": "AA:BB:CC:DD:EE:01"}, -60, FIX, NOW)
    w.close()
    assert wigle.data_rows(empty) == 0 and wigle.data_rows(full) == 1
    assert wigle.prune_empty(tmp_path) == [empty] and full.exists() and not empty.exists()


def test_fleet_adds_and_drops_adapters_live():
    import threading
    from quartermaster.wifi import WifiFleet
    seen, ready = [], threading.Event()

    class Scanner:
        method = last_error = ""
        sweeps = 0
        last_sweep_seconds = None

        def __init__(self, adapter):
            self.adapter, self.interface = adapter, adapter["interface"]

        def scan(self):
            return [{"bssid": self.interface}]

    def sink(networks, adapter):
        seen.append(adapter["interface"])
        if {"wlan0", "wlan1", "wlan2"} <= set(seen):
            ready.set()

    fleet = WifiFleet([ADAPTERS[1]], sink, interval=0.01, scanner=Scanner)
    fleet.start()
    fleet.refresh(ADAPTERS + [{"interface": "wlan2"}])
    assert ready.wait(3)
    fleet.refresh([ADAPTERS[1]])
    assert [s["interface"] for s in fleet.status()] == ["wlan0"]
    fleet.stop()
    assert fleet.status() == []


def test_an_all_day_drive_makes_room_in_memory_and_still_summarises_every_device(tmp_path, monkeypatch):
    monkeypatch.setattr(engine, "MAX_TRACKED", 10)
    clock = [NOW]
    drive = Wardrive({"ble": False}, FakeGPS(FIX), root=tmp_path, adapters=ADAPTERS, fleet=QuietFleet,
                     clock=lambda: clock[0])
    drive.start()
    heard = networks(40)
    drive._wifi_sink(heard[:30], ADAPTERS[0])
    clock[0] += 600
    drive._wifi_sink(heard[25:], ADAPTERS[0])
    drive._tidy()
    assert len(drive.tracker.devices) == 15 and drive.status()["wifi"] == 40     # the 25 left behind are let go
    clock[0] += 600
    drive._wifi_sink(heard[:1], ADAPTERS[0])                                     # heard again: logged as new
    drive.stop()
    assert drive.rows == len(rows_of(drive.log_path)) == 41
    summary = list(csv.DictReader(drive.log_path.with_name(drive.log_path.name.replace("wigle", "summary")).open()))
    assert [row["MAC"] for row in summary] == [net["bssid"].lower() for net in heard + heard[:1]]
    assert [path.name for path in (tmp_path / "logs").iterdir() if path.name.startswith(".")] == []


def test_the_log_and_the_summary_are_on_disk_under_their_names_when_the_wardrive_stops(tmp_path, monkeypatch):
    real, synced = os.fsync, []

    def fsync(fd):
        folder = stat.S_ISDIR(os.fstat(fd).st_mode)
        synced.append(("folder" if folder else os.fstat(fd).st_size,
                       sorted(path.name for path in (tmp_path / "logs").iterdir())))
        return real(fd)
    monkeypatch.setattr(os, "fsync", fsync)
    drive = Wardrive({"ble": False}, FakeGPS(FIX), root=tmp_path, adapters=ADAPTERS, fleet=FakeFleet,
                     clock=lambda: NOW)
    drive.start()
    log, summary = "quartermaster_20261118-110640.wigle.csv", "quartermaster_20261118-110640.summary.csv"
    header = len("\n".join(drive.log_path.read_text().splitlines()[:2])) + 1
    assert synced == [(header, [log]), ("folder", [log])]
    drive.stop()
    sizes = [(tmp_path / "logs" / name).stat().st_size for name in (log, summary)]
    assert sizes[0] > header and sizes[1] > 0
    assert synced[2:] == [(sizes[0], [log]), (sizes[1], ["." + summary, log]), ("folder", [summary, log])]


def test_the_last_rows_reach_the_disk_without_waiting_for_the_next_row(tmp_path, monkeypatch, later):
    monkeypatch.setattr(engine, "UPKEEP_SECONDS", 0.01)
    drive = Wardrive({"ble": False}, FakeGPS(FIX), root=tmp_path, adapters=ADAPTERS, fleet=QuietFleet,
                     clock=lambda: NOW)
    drive.start()
    drive._wifi_sink(networks(30), ADAPTERS[0])
    time.sleep(0.1)
    assert drive.rows == 30 and len(rows_of(drive.log_path)) < 30
    later(5)
    deadline = time.monotonic() + 5
    while len(rows_of(drive.log_path)) < 30 and time.monotonic() < deadline:
        time.sleep(0.01)
    assert len(rows_of(drive.log_path)) == 30 and drive.error == ""

    def full(force=False):
        raise OSError(28, "No space left on device")
    with monkeypatch.context() as patch:
        patch.setattr(drive.writer, "sync", full)
        deadline = time.monotonic() + 5
        while not drive.error and time.monotonic() < deadline:
            time.sleep(0.01)
    assert drive.error == "Cannot write the log: No space left on device"
    drive.stop()
    assert len(rows_of(drive.log_path)) == 30
