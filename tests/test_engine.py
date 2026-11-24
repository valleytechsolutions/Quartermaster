import csv
import functools
import io
import os
import signal
import stat
import subprocess
import time

import pytest

from quartermaster import engine, wigle
from quartermaster.engine import Wardrive
from quartermaster.wifi import parse_iw_scan
from tests.fakes import QuietFleet, cards, networks, on_a_card
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


TRACK = {"ble": False, "log_rows": "track"}
ADAPTERS = [{"interface": "wlan1", "external": True, "bands": ["2.4", "5", "6"], "phy": "phy1"},
            {"interface": "wlan0", "external": False, "bands": ["2.4", "5"], "phy": "phy0"}]


def rows_of(path):
    return list(csv.reader(path.read_text(encoding="utf-8").splitlines()[2:]))


def summary_of(log):
    path = log.with_name(log.name.replace(".wigle.csv", ".summary.csv"))
    return list(csv.DictReader(io.StringIO(path.read_text(encoding="utf-8"), newline="")))


def whole(path):
    """The rows of a log in which every line is a complete one."""
    rows = rows_of(path)
    assert path.read_text(encoding="utf-8").endswith("\n") and {len(row) for row in rows} <= {len(wigle.COLUMNS)}
    return rows


def quiet_drive(tmp_path):
    drive = Wardrive(dict(TRACK), FakeGPS(FIX), root=tmp_path, adapters=ADAPTERS, fleet=QuietFleet,
                     clock=lambda: NOW)
    drive.start()
    return drive


def test_wardrive_dedups_across_adapters_and_logs(tmp_path):
    drive = Wardrive(dict(TRACK), FakeGPS(FIX), root=tmp_path, adapters=ADAPTERS, fleet=FakeFleet,
                     clock=lambda: NOW)
    drive.start()
    status = drive.status()
    assert status["wifi"] == 3 and status["rows"] == 3 and status["bands"] == {"2.4": 1, "5": 1, "6": 1}
    assert drive.active_log() == drive.log_path
    drive.stop()
    assert drive.active_log() is None
    lines = drive.log_path.read_text().splitlines()
    assert len(lines) == 5 and drive.log_path.name == "quartermaster_20261118-110640.wigle.csv"
    summary = summary_of(drive.log_path)
    assert len(summary) == 3 and summary[0]["StrongestRSSI"] == "-61" and summary[0]["WeakestRSSI"] == "-61"
    assert summary[0]["StrongestLatitude"] == "25.1234567" and summary[0]["StrongestTime"] == "2026-11-18 11:06:40"


def test_no_fix_means_no_rows_and_no_file(tmp_path):
    drive = Wardrive(dict(TRACK, wifi_adapters="external"), FakeGPS({"valid": False}), root=tmp_path,
                     adapters=ADAPTERS, fleet=FakeFleet, clock=lambda: NOW)
    drive.start()
    status = drive.status()
    assert status["waiting_for_fix"] and status["wifi"] == 0 and status["rows"] == 0 and status["elapsed"] == 0
    assert drive.log_path is None and drive.skipped_no_fix == 0
    drive._sync()
    drive.stop()
    assert list((tmp_path / "logs").iterdir()) == []
    status = drive.status()
    assert status["waiting_for_fix"] and status["elapsed"] == 0 and status["log"] == ""


class Alerts:
    """Stands in for Drive Alerts and keeps what it is offered."""

    def __init__(self, cfg, gps, root, player=None, clock=None):
        self.wifi, self.ble = [], []

    def start(self, epoch):
        pass

    def stop(self):
        pass

    def offer_wifi(self, networks):
        self.wifi.extend(net["bssid"] for net in networks)

    def offer_ble(self, item):
        self.ble.append(item["address"])

    def listening(self, kind, on):
        pass

    def status(self):
        return {"alerts": 0, "urgent": False, "speaker": None}


def test_a_wardrive_waits_for_the_first_fix_then_starts_timer_counters_and_log_together(tmp_path, monkeypatch):
    gps = FakeGPS({"valid": False})
    drive = Wardrive(dict(TRACK), gps, root=tmp_path, adapters=ADAPTERS, fleet=QuietFleet, clock=lambda: NOW,
                     alerts=Alerts)
    drive.start()
    seen = networks(5)
    drive._wifi_sink(seen, ADAPTERS[0])
    drive._ble_sink([{"address": "C4:7C:8D:11:22:01", "name": "Buds", "rssi": -60, "address_type": 0}], {})
    status = drive.status()
    assert status["waiting_for_fix"] and status["wifi"] == status["ble"] == status["rows"] == 0
    assert status["elapsed"] == 0 and drive.elapsed() == 0 and drive.tracker.found() == []
    assert drive.log_path is None and list((tmp_path / "logs").iterdir()) == []
    assert len(drive.watch.wifi) == 5 and drive.watch.ble == ["C4:7C:8D:11:22:01"]
    started = time.monotonic()
    gps.fix = FIX
    status = drive.status()
    assert not status["waiting_for_fix"] and status["wifi"] == 0 and 0 <= drive.elapsed() < 1
    assert drive._started >= started
    drive._wifi_sink(seen, ADAPTERS[0])
    drive._wifi_sink(seen, ADAPTERS[0])
    drive._ble_sink([{"address": "C4:7C:8D:11:22:01", "name": "Buds", "rssi": -60, "address_type": 0}], {})
    status = drive.status()
    assert status["wifi"] == 5 and status["ble"] == 1 and status["rows"] == 6
    assert all(device.sightings == 2 for device in drive.tracker.found() if device.kind == "WIFI")
    drive.stop()
    expected = sorted([n["bssid"].lower() for n in seen] + ["c4:7c:8d:11:22:01"])
    assert sorted(r[0] for r in rows_of(drive.log_path)) == expected


def test_a_fix_too_weak_for_the_gps_accuracy_setting_keeps_the_wardrive_waiting(tmp_path):
    gps = FakeGPS(dict(FIX, hdop=7.5))
    drive = Wardrive(dict(TRACK, gps_max_hdop=5), gps, root=tmp_path, adapters=ADAPTERS, fleet=QuietFleet,
                     clock=lambda: NOW)
    drive.start()
    drive._wifi_sink(networks(3), ADAPTERS[0])
    status = drive.status()
    assert status["waiting_for_fix"] and status["gps_weak"] and status["wifi"] == 0 and drive.elapsed() == 0
    gps.fix = FIX
    drive._wifi_sink(networks(3), ADAPTERS[0])
    assert not drive.status()["waiting_for_fix"] and drive.status()["wifi"] == 3
    drive.stop()


def test_the_upkeep_starts_the_drive_at_the_fix_when_nothing_is_heard(tmp_path, monkeypatch):
    monkeypatch.setattr(engine, "UPKEEP_SECONDS", 0.01)
    gps = FakeGPS({"valid": False})
    drive = Wardrive(dict(TRACK), gps, root=tmp_path, adapters=ADAPTERS, fleet=QuietFleet, clock=lambda: NOW)
    drive.start()
    assert drive.waiting
    gps.fix = FIX
    wait_for(lambda: not drive.waiting)
    drive.stop()


def test_a_fix_lost_during_the_drive_pauses_logging_but_not_the_timer_or_the_counts(tmp_path):
    gps = FakeGPS(FIX)
    drive = Wardrive(dict(TRACK), gps, root=tmp_path, adapters=ADAPTERS, fleet=QuietFleet, clock=lambda: NOW)
    drive.start()
    drive._wifi_sink(networks(3), ADAPTERS[0])
    started = drive._started
    gps.fix = {"valid": False}
    drive._wifi_sink(networks(5), ADAPTERS[0])
    status = drive.status()
    assert not status["waiting_for_fix"] and drive._started == started and status["elapsed"] > 0
    assert status["wifi"] == 5 and status["rows"] == 3 and drive.skipped_no_fix == 5
    drive.stop()


def test_bluetooth_heard_while_waiting_is_logged_once_heard_again_with_the_fix(tmp_path):
    from tests.fakes import Ble
    gps = FakeGPS({"valid": False})
    ble = Ble([("C4:7C:8D:11:22:01", "Buds", -60)])
    drive = Wardrive({"log_rows": "track", "ble_interval": 0.01}, gps, root=tmp_path, adapters=ADAPTERS,
                     fleet=QuietFleet, clock=lambda: NOW, ble=ble)
    drive.start()
    time.sleep(0.1)
    assert drive.status()["ble"] == 0
    gps.fix = FIX
    wait_for(lambda: drive.status()["rows"] == 1)
    drive.stop()
    assert drive.status()["ble"] == 1 and [r[0] for r in rows_of(drive.log_path)] == ["c4:7c:8d:11:22:01"]


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
    drive = Wardrive(dict(TRACK, **(cfg or {})), gps or FakeGPS(FIX), root=tmp_path, adapters=ADAPTERS[:1],
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
    summary = summary_of(drive.log_path)[0]
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
    assert drive.rows == 0 and drive.waiting and drive.log_path is None
    drive = run_sweeps(tmp_path, [-60], {"gps_max_hdop": 10}, weak)
    assert drive.rows == 1


def test_header_only_logs_are_pruned(tmp_path):
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
    drive = Wardrive(dict(TRACK), FakeGPS(FIX), root=tmp_path, adapters=ADAPTERS, fleet=QuietFleet,
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
    assert [row["MAC"] for row in summary_of(drive.log_path)] == [net["bssid"].lower() for net in heard + heard[:1]]
    assert [path.name for path in (tmp_path / "logs").iterdir() if path.name.startswith(".")] == []


def test_the_log_and_the_summary_are_on_disk_under_their_names_when_the_wardrive_stops(tmp_path, monkeypatch):
    real, synced = os.fsync, []

    def fsync(fd):
        if os.readlink("/proc/self/fd/%d" % fd).startswith(str(tmp_path)):
            folder = os.path.samestat(os.fstat(fd), os.stat(tmp_path / "logs"))
            assert folder or stat.S_ISREG(os.fstat(fd).st_mode)
            synced.append(("folder" if folder else os.fstat(fd).st_size,
                           sorted(path.name for path in (tmp_path / "logs").iterdir())))
        return real(fd)
    monkeypatch.setattr(os, "fsync", fsync)
    drive = Wardrive(dict(TRACK), FakeGPS(FIX), root=tmp_path, adapters=ADAPTERS, fleet=FakeFleet,
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
    assert engine.UPKEEP_SECONDS + wigle.SYNC_SECONDS < 10
    monkeypatch.setattr(engine, "UPKEEP_SECONDS", 0.01)
    drive = Wardrive(dict(TRACK), FakeGPS(FIX), root=tmp_path, adapters=ADAPTERS, fleet=QuietFleet,
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


def test_upkeep_that_fails_is_noted_and_goes_on(tmp_path, monkeypatch, noted):
    monkeypatch.setattr(engine, "UPKEEP_SECONDS", 0.01)
    drive, calls = quiet_drive(tmp_path), []

    def sync():
        calls.append(len(calls))
        if len(calls) == 1:
            raise ValueError("nothing a log does")
    monkeypatch.setattr(drive, "_sync", sync)
    deadline = time.monotonic() + 5
    while len(calls) < 3 and time.monotonic() < deadline:
        time.sleep(0.01)
    drive.stop()
    assert len(calls) >= 3 and "Wardrive upkeep" in noted.read_text() and "nothing a log does" in noted.read_text()


def test_a_quiet_spell_writes_nothing_and_the_first_row_after_it_is_on_disk_at_once(tmp_path, monkeypatch, later):
    real, synced = os.fsync, []

    def fsync(fd):
        synced.append(fd)
        return real(fd)
    monkeypatch.setattr(os, "fsync", fsync)
    drive = quiet_drive(tmp_path)
    drive._wifi_sink(networks(6)[:5], ADAPTERS[0])
    later(5)
    drive._sync()
    count = len(synced)
    assert len(whole(drive.log_path)) == 5
    for _ in range(4):
        later(4)
        drive._sync()
    assert len(synced) == count
    drive._wifi_sink(networks(6)[5:], ADAPTERS[0])
    assert len(synced) == count + 1 and len(whole(drive.log_path)) == 6
    drive.stop()


def test_a_card_that_takes_no_more_ends_the_log_at_a_whole_row_and_the_next_row_starts_a_new_log(tmp_path, later):
    drive, heard = quiet_drive(tmp_path), networks(70)
    drive._wifi_sink(heard[:30], ADAPTERS[0])
    later(5)
    drive._sync()
    first, size = drive.log_path, drive.log_path.stat().st_size
    assert len(whole(first)) == 30 and (drive.error, drive.failed) == ("", "")
    on_a_card(drive.writer, room=150)
    drive._wifi_sink(heard[30:60], ADAPTERS[0])
    assert drive.error == "" and first.stat().st_size == size
    later(5)
    drive._sync()
    assert drive.error == drive.failed == "Cannot write the log: No space left on device" and drive.writer is None
    assert 30 < len(whole(first)) < 60 and size < first.stat().st_size < size + 150
    assert drive.active_log() is None and drive.status()["error"] == drive.error
    assert drive.rows == drive.status()["rows"] == len(whole(first))
    drive._wifi_sink(heard[60:], ADAPTERS[0])
    assert drive.error == "" and drive.failed and drive.log_paths == [first, drive.active_log()]
    drive.stop()
    assert len(whole(drive.log_paths[1])) == 10 and drive.log_paths[1].name.endswith("-1.wigle.csv")
    assert drive.rows == len(whole(first)) + 10
    assert drive.status()["failed"] == "Cannot write the log: No space left on device" and drive.status()["error"] == ""
    summary = first.with_name(first.name.replace("wigle", "summary"))
    assert len(summary.read_text().splitlines()) == 71


def test_what_a_card_takes_after_all_is_kept_and_the_log_still_holds_whole_rows_only(tmp_path, later):
    drive, heard = quiet_drive(tmp_path), networks(60)
    drive._wifi_sink(heard[:30], ADAPTERS[0])
    on_a_card(drive.writer, room=150, then=1 << 20)
    drive._wifi_sink(heard[30:], ADAPTERS[0])
    later(5)
    drive._sync()
    assert drive.failed == "Cannot write the log: No space left on device" and drive.writer is None
    assert len(whole(drive.log_path)) == drive.rows == 60
    drive.stop()
    assert len(whole(drive.log_path)) == drive.rows == 60 and len(drive.log_paths) == 1


def test_rows_that_fail_when_the_next_part_of_a_log_starts_are_said(tmp_path, monkeypatch):
    drive, heard = quiet_drive(tmp_path), networks(40)
    drive._wifi_sink(heard[:10], ADAPTERS[0])
    on_a_card(drive.writer, room=0)
    drive._wifi_sink(heard[10:20], ADAPTERS[0])
    assert (drive.error, drive.failed) == ("", "")
    with monkeypatch.context() as patch:
        patch.setattr(engine, "ROTATE_BYTES", 1)
        drive._wifi_sink(heard[20:21], ADAPTERS[0])
    assert drive.error == drive.failed == "Cannot write the log: No space left on device" and drive.writer is None
    drive._wifi_sink(heard[21:], ADAPTERS[0])
    drive.stop()
    assert [len(whole(path)) for path in drive.log_paths] == [10, 19] and drive.rows == 29


def test_only_rows_that_are_in_a_log_are_counted(tmp_path, later):
    drive, heard = quiet_drive(tmp_path), networks(40)
    drive._wifi_sink(heard[:10], ADAPTERS[0])
    on_a_card(drive.writer, room=0)
    later(5)
    drive._wifi_sink(heard[10:15], ADAPTERS[0])
    assert drive.failed == "Cannot write the log: No space left on device" and len(drive.log_paths) == 2
    assert drive.rows == 14 and [len(whole(path)) for path in drive.log_paths] == [10, 0]
    on_a_card(drive.writer, room=0)
    drive._wifi_sink(heard[15:25], ADAPTERS[0])
    first, second = drive.log_paths
    second.unlink()
    later(5)
    drive._sync()
    assert drive.rows == 10 and drive.writer is None and drive.log_paths == [first]
    drive.stop()
    assert drive.status()["rows"] == len(whole(first)) == 10 and drive.status()["files"] == 1


def test_a_wardrive_whose_last_rows_did_not_reach_the_card_does_not_count_as_saved(tmp_path, later):
    drive = quiet_drive(tmp_path)
    drive._wifi_sink(networks(40)[:30], ADAPTERS[0])
    later(5)
    drive._sync()
    on_a_card(drive.writer, room=0, error=OSError(30, "Read-only file system"))
    drive._wifi_sink(networks(40)[30:], ADAPTERS[0])
    assert (drive.error, drive.failed) == ("", "")
    drive.stop()
    assert drive.state == "stopped" and drive.writer is None and len(whole(drive.log_path)) == drive.rows == 30
    assert drive.failed == drive.status()["failed"] == "Cannot write the log: Read-only file system"


def test_a_card_that_does_not_keep_what_it_took_counts_as_a_failed_write(tmp_path, monkeypatch, later):
    real, errors, synced = os.fsync, [], []

    def fsync(fd):
        synced.append(os.fstat(fd).st_size)
        if errors:
            raise errors.pop()
        return real(fd)
    monkeypatch.setattr(os, "fsync", fsync)
    drive, heard = quiet_drive(tmp_path), networks(40)
    for rows, cannot in ((5, OSError(22, "Invalid argument")), (10, OSError(95, "Operation not supported"))):
        drive._wifi_sink(heard[rows - 5:rows], ADAPTERS[0])
        errors.append(cannot)
        later(5)
        drive._sync()
        assert errors == [] and (drive.error, drive.failed) == ("", "") and len(whole(drive.log_path)) == rows
    drive._wifi_sink(heard[10:14], ADAPTERS[0])
    later(5)
    drive._wifi_sink(heard[14:20], ADAPTERS[0])
    assert len(whole(drive.log_path)) == 15 and drive.rows == 20
    kept, calls = drive.log_path.stat().st_size, len(synced)
    errors.append(OSError(5, "Input/output error"))
    later(5)
    drive._sync()
    assert drive.error == drive.failed == "Cannot write the log: Input/output error" and drive.writer is None
    assert drive.rows == 15 and synced[calls + 1:] == [kept] < synced[calls:calls + 1]
    assert [row[0] for row in whole(drive.log_path)] == [net["bssid"].lower() for net in heard[:15]]
    drive._wifi_sink(heard[20:], ADAPTERS[0])
    assert drive.error == "" and len(drive.log_paths) == 2
    drive.stop()
    assert [len(whole(path)) for path in drive.log_paths] == [15, 20] and drive.rows == 35
    assert drive.failed == "Cannot write the log: Input/output error"
    assert drive.status()["log"] == str(drive.log_paths[0]) != str(drive.log_paths[1]) and drive.status()["files"] == 2


def test_a_card_that_fails_only_as_a_log_is_closed_is_said_at_the_next_part_and_at_the_stop(
        tmp_path, monkeypatch, later):
    real, errors = os.fsync, []

    def fsync(fd):
        if errors:
            raise errors.pop()
        return real(fd)
    monkeypatch.setattr(os, "fsync", fsync)
    drive, heard = quiet_drive(tmp_path), networks(40)
    drive._wifi_sink(heard[:10], ADAPTERS[0])
    later(5)
    drive._sync()
    drive._wifi_sink(heard[10:15], ADAPTERS[0])
    errors.append(OSError(5, "Input/output error"))
    with monkeypatch.context() as patch:
        patch.setattr(engine, "ROTATE_BYTES", 1)
        drive._wifi_sink(heard[15:16], ADAPTERS[0])
    assert drive.error == drive.failed == "Cannot write the log: Input/output error" and drive.writer is None
    assert drive.rows == len(whole(drive.log_path)) == 10
    drive._wifi_sink(heard[16:21], ADAPTERS[0])
    later(5)
    drive._sync()
    drive._wifi_sink(heard[21:26], ADAPTERS[0])
    assert drive.error == "" and drive.rows == 20
    errors.append(OSError(122, "Disk quota exceeded"))
    drive.stop()
    assert drive.error == drive.failed == "Cannot write the log: Disk quota exceeded" and drive.state == "stopped"
    assert [len(whole(path)) for path in drive.log_paths] == [10, 5] and drive.rows == 15 and drive.writer is None


def test_a_row_that_reached_the_log_while_the_card_said_no_is_counted(tmp_path, later):
    for error in (OSError(28, "No space left on device"), OSError(122, "Disk quota exceeded")):
        drive, heard = quiet_drive(tmp_path / str(error.errno)), networks(12)
        drive._wifi_sink(heard[:10], ADAPTERS[0])
        on_a_card(drive.writer, room=10, error=error, then=1 << 20)
        later(5)
        drive._wifi_sink(heard[10:11], ADAPTERS[0])
        assert drive.failed == "Cannot write the log: " + error.strerror and drive.writer is None
        assert drive.rows == len(whole(drive.log_path)) == 11
        drive.stop()


def test_what_a_full_card_took_counts_only_once_it_is_on_disk(tmp_path, monkeypatch, later):
    real, refuse = os.fsync, {}

    def fsync(fd):
        error = refuse.pop("once", None) or refuse.get("always")
        if error:
            raise error
        return real(fd)
    monkeypatch.setattr(os, "fsync", fsync)
    heard = networks(20)

    def filled(name):
        drive = quiet_drive(tmp_path / name)
        drive._wifi_sink(heard[:10], ADAPTERS[0])
        later(5)
        drive._sync()
        size = drive.log_path.stat().st_size
        on_a_card(drive.writer, room=150)
        drive._wifi_sink(heard[10:], ADAPTERS[0])
        later(5)
        return drive, size
    drive, size = filled("lost")
    refuse["once"] = OSError(5, "Input/output error")
    drive._sync()
    assert drive.failed == "Cannot write the log: No space left on device" and refuse == {}
    assert drive.rows == len(whole(drive.log_path)) == 10 and drive.log_path.stat().st_size == size
    drive.stop()
    refuse["always"] = OSError(95, "Operation not supported")
    drive, size = filled("no fsync")
    drive._sync()
    assert drive.failed == "Cannot write the log: No space left on device"
    assert drive.rows == len(whole(drive.log_path)) == 11 and size < drive.log_path.stat().st_size < size + 150
    drive.stop()


def test_a_log_that_cannot_be_opened_again_counts_the_rows_of_its_last_sync(tmp_path, monkeypatch, later):
    drive, heard = quiet_drive(tmp_path), networks(20)
    drive._wifi_sink(heard[:10], ADAPTERS[0])
    later(5)
    drive._sync()
    size = drive.log_path.stat().st_size
    on_a_card(drive.writer, room=150)
    drive._wifi_sink(heard[10:], ADAPTERS[0])

    def read_only(path, mode="r", *args, **kwargs):
        if "+" in mode:
            raise OSError(30, "Read-only file system")
        return open(path, mode, *args, **kwargs)
    monkeypatch.setattr(wigle, "open", read_only, raising=False)
    later(5)
    drive._sync()
    assert drive.failed == "Cannot write the log: No space left on device" and drive.writer is None
    assert drive.rows == 10 and drive.log_path.stat().st_size == size + 150
    drive.stop()
    assert drive.rows == 10 and len(drive.log_paths) == 1


def test_a_part_left_without_a_row_goes_at_once_and_the_summary_is_named_after_a_log_that_stays(tmp_path, later):
    drive, heard = quiet_drive(tmp_path), networks(30)
    drive._wifi_sink(heard[:1], ADAPTERS[0])
    first = drive.log_path
    on_a_card(drive.writer, room=0, error=OSError(5, "Input/output error"))
    drive._wifi_sink(heard[1:10], ADAPTERS[0])
    assert drive.rows == 10 and first.exists()
    listed = drive.log_paths
    later(5)
    drive._sync()
    assert drive.failed == "Cannot write the log: Input/output error" and drive.rows == 0
    assert drive.log_paths == [] and drive.log_path is None and drive.status()["log"] == "" and listed == [first]
    assert list((tmp_path / "logs").iterdir()) == []
    clock = drive.clock
    drive.clock = lambda: clock() + 90
    drive.gps.fix = dict(FIX, utc="2026-11-18T11:08:10Z")
    drive._wifi_sink(heard[10:], ADAPTERS[0])
    drive.stop()
    assert drive.rows == 20 and len(drive.log_paths) == 1 and drive.log_path != first
    assert len(whole(drive.log_path)) == 20 and len(summary_of(drive.log_path)) == 30
    assert sorted(path.name for path in (tmp_path / "logs").iterdir()) == [
        "quartermaster_20261118-110810.summary.csv", "quartermaster_20261118-110810.wigle.csv"]


def test_a_part_without_a_row_that_cannot_be_removed_is_no_longer_listed(tmp_path, monkeypatch, later):
    drive, heard = quiet_drive(tmp_path), networks(2)
    drive._wifi_sink(heard[:1], ADAPTERS[0])
    first = drive.log_path
    on_a_card(drive.writer, room=0, error=OSError(5, "Input/output error"))
    drive._wifi_sink(heard[1:], ADAPTERS[0])

    def stuck(self, *args, **kwargs):
        raise OSError(30, "Read-only file system")
    monkeypatch.setattr(type(first), "unlink", stuck)
    later(5)
    drive._sync()
    assert drive.failed == "Cannot write the log: Input/output error" and drive.rows == 0
    assert drive.log_paths == [] and first.exists() and drive.status()["files"] == 0
    assert drive.log_path is None and drive.status()["log"] == ""
    drive.gps.fix = dict(FIX, utc="2026-11-18T11:08:10Z")
    drive._wifi_sink(networks(3)[2:], ADAPTERS[0])
    drive.stop()
    assert drive.log_paths == [drive.log_path] and drive.log_path != first and len(whole(drive.log_path)) == 1
    assert len(summary_of(drive.log_path)) == 3


def test_a_card_with_room_for_a_header_and_no_more_leaves_no_file_per_row_heard(tmp_path, monkeypatch, later):
    probe = wigle.WigleWriter(tmp_path / "probe.csv", engine.hardware.os_release())
    probe.close()
    drive, sync = quiet_drive(tmp_path), wigle.fsync

    def slow(fd):
        sync(fd)
        later(5)
    monkeypatch.setattr(wigle, "fsync", slow)
    cards(monkeypatch, room=probe.size)
    for network in networks(5):
        drive._wifi_sink([network], ADAPTERS[0])
        assert drive.error == "Cannot write the log: No space left on device" and drive.rows == 0
        assert list((tmp_path / "logs").iterdir()) == [] and drive.log_paths == []
    drive.stop()
    assert list((tmp_path / "logs").iterdir()) == [] and drive.status()["log"] == ""


def test_a_log_that_cannot_be_started_leaves_no_file_behind(tmp_path, monkeypatch):
    drive, asked = quiet_drive(tmp_path), []

    def full(fd):
        asked.append(fd)
        raise OSError(28, "No space left on device")
    with monkeypatch.context() as patch:
        patch.setattr(os, "fsync", full)
        open_before = len(os.listdir("/proc/self/fd"))
        with pytest.raises(OSError) as refused:
            wigle.WigleWriter(tmp_path / "log.csv")
        assert refused.value.errno == 28 and len(os.listdir("/proc/self/fd")) == open_before
        assert not (tmp_path / "log.csv").exists() and len(asked) == 1
        with monkeypatch.context() as card:
            cards(card, room=0)
            with pytest.raises(OSError):
                wigle.WigleWriter(tmp_path / "log.csv")
        assert not (tmp_path / "log.csv").exists() and len(os.listdir("/proc/self/fd")) == open_before
        assert len(asked) == 1
        for network in networks(8)[:5]:
            drive._wifi_sink([network], ADAPTERS[0])
        assert drive.error == "Cannot write the log: No space left on device" and drive.rows == 0
        assert drive.log_path is None and list((tmp_path / "logs").iterdir()) == [] and len(asked) == 6
    drive._wifi_sink(networks(8)[5:], ADAPTERS[0])
    assert drive.error == "" and drive.rows == 3 and drive.log_path.name == "quartermaster_20261118-110640.wigle.csv"
    drive.stop()
    assert len(whole(drive.log_path)) == 3 and drive.failed == "Cannot write the log: No space left on device"


def test_only_a_last_line_without_an_end_is_cut_from_a_log(tmp_path):
    path = tmp_path / "log.csv"
    for before, after, window in ((b"", b"", 65536), (b"a,b\n", b"a,b\n", 65536), (b"a,b\nc,d", b"a,b\n", 65536),
                                  (b"a,b", b"", 65536), (b"a\nbb\ncc", b"a\nbb\n", 4), (b"a\nbb\n", b"a\nbb\n", 2),
                                  (b"a\n" + b"c" * 9, b"a\n" + b"c" * 9, 4)):
        path.write_bytes(before)
        assert wigle.trim(path, window) is (before != b"a\n" + b"c" * 9)
        assert path.read_bytes() == after, (before, window)
    path.write_bytes(b"a,b\n" + b"c" * 20000)
    assert wigle.trim(path) and path.read_bytes() == b"a,b\n"
    assert wigle.trim(tmp_path / "missing.csv") is False and not (tmp_path / "missing.csv").exists()
    path.write_bytes(b"a,b\nc,d\n")
    for size, after in ((9, b"a,b\nc,d\n"), (6, b"a,b\nc,"), (4, b"a,b\n"), (8, b"a,b\n"), (0, b"")):
        wigle.cut(path, size)
        assert path.read_bytes() == after
    wigle.cut(tmp_path / "missing.csv", 0)
    assert not (tmp_path / "missing.csv").exists()


def test_a_log_is_closed_even_when_its_last_rows_cannot_be_written(tmp_path):
    writer = wigle.WigleWriter(tmp_path / "log.csv")
    on_a_card(writer, room=0)
    writer.write("WIFI", {"mac": "AA:BB:CC:DD:EE:01"}, -60, FIX, NOW)
    with pytest.raises(OSError):
        writer.close()
    assert writer.file.closed and writer.file.buffer.raw.closed


def test_rows_that_arrive_after_the_stop_are_not_written_anywhere(tmp_path):
    drive = quiet_drive(tmp_path)
    drive._wifi_sink(networks(9)[:5], ADAPTERS[0])
    drive.stop()
    files = sorted(path.name for path in (tmp_path / "logs").iterdir())
    drive._wifi_sink(networks(9)[5:], ADAPTERS[0])
    assert drive.writer is None and drive.rows == 5 and len(whole(drive.log_path)) == 5
    assert sorted(path.name for path in (tmp_path / "logs").iterdir()) == files and len(files) == 2


def test_a_row_that_arrives_as_the_stop_lets_go_of_the_log_is_not_written_either(tmp_path):
    drive = quiet_drive(tmp_path)
    drive._wifi_sink(networks(9)[:5], ADAPTERS[0])

    class Lock:
        def __init__(self, real):
            self.real, self.let_go = real, []

        def __enter__(self):
            self.real.acquire()

        def __exit__(self, *exc):
            self.real.release()
            if drive._stopped is not None and not self.let_go:
                self.let_go.append(drive.state)
                drive._wifi_sink(networks(9)[5:], ADAPTERS[0])
    drive._lock = lock = Lock(drive._lock)
    drive.stop()
    assert lock.let_go == ["stopped"] and drive.writer is None and drive.rows == 5
    assert [len(whole(path)) for path in drive.log_paths] == [5] and len(list((tmp_path / "logs").iterdir())) == 2


def long_drive(tmp_path, monkeypatch):
    """A wardrive that has let devices go from memory, so that their summary lines wait in a hidden file."""
    monkeypatch.setattr(engine, "MAX_TRACKED", 10)
    clock = [NOW]
    drive = Wardrive(dict(TRACK), FakeGPS(FIX), root=tmp_path, adapters=ADAPTERS, fleet=QuietFleet,
                     clock=lambda: clock[0])
    drive.start()
    drive._wifi_sink(networks(30), ADAPTERS[0])
    clock[0] += 600
    drive._tidy()
    hidden = [path for path in (tmp_path / "logs").iterdir() if path.name.startswith(".")]
    assert [path.name for path in hidden] == [".quartermaster_20261118-110640.summary.csv.part"]
    return drive, hidden[0]


def test_devices_let_go_before_the_first_row_are_not_set_aside(tmp_path, monkeypatch):
    monkeypatch.setattr(engine, "MAX_TRACKED", 10)
    clock = [NOW]
    drive = Wardrive(dict(TRACK), FakeGPS(FIX), root=tmp_path, adapters=ADAPTERS, fleet=QuietFleet,
                     clock=lambda: clock[0])
    drive.start()
    drive.status()
    drive.gps.fix = {"valid": False}
    drive._wifi_sink(networks(30), ADAPTERS[0])
    clock[0] += 600
    drive._tidy()
    assert drive.status()["wifi"] == 30 and len(drive.tracker.found()) == 8
    assert list((tmp_path / "logs").iterdir()) == [] and drive.failed == ""


def test_a_summary_that_cannot_be_written_is_said_and_leaves_nothing_hidden(tmp_path, monkeypatch):
    drive, _ = long_drive(tmp_path, monkeypatch)
    settle, settled = engine._settle, []

    def full(*args, **kwargs):
        raise OSError(28, "No space left on device")

    def record(folder):
        settled.append([path.name for path in folder.iterdir()])
        settle(folder)
    monkeypatch.setattr(os, "replace", full)
    monkeypatch.setattr(engine, "_settle", record)
    drive.stop()
    assert drive.failed == "Cannot write the summary: No space left on device" and drive.error == ""
    assert settled == [[drive.log_path.name]] == [[path.name for path in (tmp_path / "logs").iterdir()]]
    assert len(whole(drive.log_path)) == 30


def test_a_summary_fails_without_stopping_the_stop(tmp_path, monkeypatch):
    drive, aside = long_drive(tmp_path / "torn", monkeypatch)
    aside.write_bytes(b"\xff\xfe\n")
    drive.stop()
    assert drive.failed.startswith("Cannot write the summary: 'utf-8'") and drive.state == "stopped"
    assert [path.name for path in (tmp_path / "torn" / "logs").iterdir()] == [drive.log_path.name]
    drive, aside = long_drive(tmp_path / "stuck", monkeypatch)
    real = type(aside).unlink

    def unlink(self, *args, **kwargs):
        if self.name.startswith("."):
            raise OSError(30, "Read-only file system")
        return real(self, *args, **kwargs)
    monkeypatch.setattr(type(aside), "unlink", unlink)
    monkeypatch.setattr(os, "replace", unlink)
    drive.stop()
    assert drive.failed == "Cannot write the summary: Read-only file system" and drive.state == "stopped"
    assert aside.exists() and len(whole(drive.log_path)) == 30


def test_an_error_that_brings_no_words_of_its_own_is_still_said(tmp_path, later):
    drive = quiet_drive(tmp_path)
    drive._wifi_sink(networks(8)[:5], ADAPTERS[0])
    on_a_card(drive.writer, room=0, error=OSError("the card is gone"))
    drive._wifi_sink(networks(8)[5:], ADAPTERS[0])
    later(5)
    drive._sync()
    assert drive.error == drive.failed == "Cannot write the log: the card is gone"
    drive.stop()


def test_rows_that_were_lost_are_what_is_said_whatever_else_went_wrong(tmp_path, monkeypatch, later):
    monkeypatch.setattr(engine, "MAX_TRACKED", 10)
    clock, heard, real = [NOW], networks(90), os.open
    drive = Wardrive(dict(TRACK), FakeGPS(FIX), root=tmp_path, adapters=ADAPTERS, fleet=QuietFleet,
                     clock=lambda: clock[0])
    drive.start()

    def no_room(path, *args, **kwargs):
        if os.path.basename(path).startswith("."):
            raise OSError(28, "No space left on device")
        return real(path, *args, **kwargs)

    def left_behind(found):
        drive._wifi_sink(found, ADAPTERS[0])
        clock[0] += 600
        with monkeypatch.context() as patch:
            patch.setattr(os, "open", no_room)
            drive._tidy()
    left_behind(heard[:30])
    assert (drive.error, drive.failed) == ("", "Cannot write the summary: No space left on device")
    later(5)
    drive._sync()
    on_a_card(drive.writer, room=0, error=OSError(5, "Input/output error"))
    drive._wifi_sink(heard[30:60], ADAPTERS[0])
    later(5)
    drive._sync()
    assert drive.error == drive.failed == "Cannot write the log: Input/output error" and drive.rows == 30
    left_behind(heard[60:])
    assert (drive.error, drive.failed) == ("", "Cannot write the log: Input/output error")
    monkeypatch.setattr(os, "open", no_room)
    drive.stop()
    assert (drive.error, drive.failed) == ("", "Cannot write the log: Input/output error")
    assert len(drive.log_paths) == 2 and set((tmp_path / "logs").iterdir()) == set(drive.log_paths)


def test_a_folder_that_cannot_be_put_on_disk_does_not_stop_the_log(tmp_path, monkeypatch):
    real_open, real_fsync = os.open, os.fsync

    def no_open(path, flags, *args, **kwargs):
        if os.path.isdir(path):
            raise OSError(13, "Permission denied")
        return real_open(path, flags, *args, **kwargs)

    def no_fsync(fd):
        if stat.S_ISDIR(os.fstat(fd).st_mode):
            raise OSError(22, "Invalid argument")
        return real_fsync(fd)

    def never(fd):
        raise OSError(95, "Operation not supported")
    for root, name, stand_in in (("open", "open", no_open), ("folder", "fsync", no_fsync), ("never", "fsync", never)):
        open_before = len(os.listdir("/proc/self/fd"))
        with monkeypatch.context() as patch:
            patch.setattr(os, name, stand_in)
            drive = Wardrive(dict(TRACK), FakeGPS(FIX), root=tmp_path / root, adapters=ADAPTERS, fleet=FakeFleet,
                             clock=lambda: NOW)
            drive.start()
            drive.stop()
        assert (drive.error, drive.failed) == ("", "") and len(whole(drive.log_path)) == 3
        assert drive.log_path.with_name(drive.log_path.name.replace("wigle", "summary")).exists()
        assert len(os.listdir("/proc/self/fd")) == open_before


def test_a_part_stays_the_active_log_until_it_has_been_repaired(tmp_path, later):
    drive, heard = quiet_drive(tmp_path), networks(2)
    drive._wifi_sink(heard[:1], ADAPTERS[0])
    first, seen = drive.log_path, []
    on_a_card(drive.writer, room=0, error=OSError(5, "Input/output error"))
    drive._wifi_sink(heard[1:], ADAPTERS[0])
    abandon = drive.writer.abandon

    def watched(exc):
        seen.append(drive.active_log())
        lost = abandon(exc)
        seen.append(drive.active_log())
        return lost
    drive.writer.abandon = watched
    later(5)
    drive._sync()
    assert seen == [first, first] and drive.active_log() is None and drive.writer is None


def test_a_half_written_summary_line_set_aside_does_not_join_the_next(tmp_path, monkeypatch):
    drive, aside = long_drive(tmp_path, monkeypatch)
    with open(aside, "ab") as part:
        part.write(b"AA:BB:CC:00:00:01,half a li")
    drive._set_aside(drive.tracker.found()[:1])
    drive.stop()
    rows = summary_of(drive.log_path)
    assert len(rows) == 31 and all(None not in row and None not in row.values() for row in rows)
    assert not any(row[engine.SUMMARY_COLUMNS[0]].startswith("AA:BB:CC") for row in rows)


def closest_and_furthest(tmp_path, clock=None, **cfg):
    clock = clock or [NOW]
    drive = Wardrive(dict({"ble": False}, **cfg), FakeGPS(FIX), root=tmp_path, adapters=ADAPTERS, fleet=QuietFleet,
                     clock=lambda: clock[0])
    drive.start()
    return drive, clock


def hear(drive, clock, rssi, net=NET, **fix):
    drive.gps.fix = dict(FIX, **fix)
    drive._wifi_sink([dict(net, rssi=rssi)], ADAPTERS[0])
    clock[0] += 1


def per_device(rows):
    found = {}
    for row in rows:
        found.setdefault((row[13], row[0]), []).append(row)
    return found


def expected_ends(points):
    """The rows a device should keep, worked out the slow way from every sighting: (rssi, accuracy, time, lat)."""
    def accuracy(point):
        return float("inf") if point[1] == "" else point[1]
    best = min(points, key=lambda p: (-p[0], accuracy(p), p[2]))
    weak = [p for p in points if p[0] >= -92 and (p[1] == "" and best[1] == "" or p[1] != "" and p[1] <= 25)]
    edge = min(weak, key=lambda p: (p[0], accuracy(p), p[2])) if weak else best
    return [best] if edge == best else [best, edge]


def as_points(rows):
    return [(int(r[6]), float(r[10]) if r[10] else "", r[3], r[7]) for r in rows]


def test_a_long_noisy_drive_keeps_only_the_closest_and_furthest_row_of_each_device(tmp_path, monkeypatch):
    import random
    monkeypatch.setattr(engine, "MAX_TRACKED", 30)
    rnd, heard, seen, last = random.Random(3), networks(40), {}, {}
    drive, clock = closest_and_furthest(tmp_path)
    for sweep in range(600):
        fix = dict(FIX, latitude=FIX["latitude"] + sweep / 10000, hdop=rnd.choice([0.9, 0.9, 3, 6]))
        drive.gps.fix = fix
        found = [dict(net, rssi=rnd.randrange(-99, -35)) for net in (heard[:30] if sweep < 300 else heard[20:])]
        drive._wifi_sink(found, ADAPTERS[0])
        ble = [{"address": "11:22:33:44:55:%02X" % i, "rssi": rnd.randrange(-99, -50), "name": ""} for i in range(5)]
        drive._ble_sink(ble, last)
        stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(clock[0]))
        sightings = [("WIFI", n["bssid"], n["rssi"]) for n in found] + [("BLE", b["address"], b["rssi"]) for b in ble]
        for kind, mac, rssi in sightings:
            seen.setdefault((kind, mac.lower()), []).append((rssi, wigle.accuracy_m(fix), stamp,
                                                             "%.7f" % fix["latitude"]))
        clock[0] += 1
        if sweep % 100 == 99:
            drive._tidy()
    journal = wigle.journal_path(drive.log_path)
    assert journal.exists() and not drive.log_path.exists() and drive.status()["rows"] > 0
    assert len(drive.tracker.devices) < 45 == drive.status()["wifi"] + drive.status()["ble"]
    assert len(rows_of(journal)) > 2 * len(seen)
    drive.stop()
    rows = whole(drive.log_path)
    assert len(rows) == drive.rows == drive.status()["rows"] and not journal.exists()
    assert [row[3] for row in rows] == sorted(row[3] for row in rows)
    found = per_device(rows)
    assert set(found) == set(seen) and all(len(kept) <= 2 for kept in found.values())
    for key, kept in found.items():
        assert sorted(as_points(kept)) == sorted(expected_ends(seen[key])), key
    summary = summary_of(drive.log_path)
    assert {(row["Type"], row["MAC"]): int(row["LogRows"]) for row in summary} == {k: len(v) for k, v in found.items()}
    assert sorted(path.name for path in (tmp_path / "logs").iterdir()) == [
        "quartermaster_20261118-110640.summary.csv", "quartermaster_20261118-110640.wigle.csv"]


def test_a_device_heard_once_or_always_alike_gets_one_row(tmp_path):
    drive, clock = closest_and_furthest(tmp_path)
    for _ in range(50):
        hear(drive, clock, -70)
    hear(drive, clock, -70, net=dict(NET, bssid="AA:BB:CC:DD:EE:02"))
    drive.stop()
    rows = whole(drive.log_path)
    assert [(row[0], row[6], row[3]) for row in rows] == [("aa:bb:cc:dd:ee:01", "-70", "2026-11-18 11:06:40"),
                                                          ("aa:bb:cc:dd:ee:02", "-70", "2026-11-18 11:07:30")]
    assert drive.rows == 2 and [row["LogRows"] for row in summary_of(drive.log_path)] == ["1", "1"]


def test_the_furthest_row_is_never_below_92_dbm_or_less_sure_than_25_m(tmp_path):
    drive, clock = closest_and_furthest(tmp_path / "floor")
    for rssi, hdop in ((-60, 0.9), (-95, 0.9), (-93, 0.9), (-88, 6), (-80, 0.9), (-78, 0.9)):
        hear(drive, clock, rssi, hdop=hdop)
    drive.stop()
    assert [(row[6], row[10]) for row in whole(drive.log_path)] == [("-60", "4.5"), ("-80", "4.5")]
    assert summary_of(drive.log_path)[0]["WeakestRSSI"] == "-80"
    drive, clock = closest_and_furthest(tmp_path / "unknown")
    unknown = {"valid": True, "latitude": 25.2, "longitude": -77.7, "age_seconds": 0.1}
    drive.gps = FakeGPS(dict(FIX))
    hear(drive, clock, -60)
    drive.gps = FakeGPS(unknown)
    drive._wifi_sink([dict(NET, rssi=-85)], ADAPTERS[0])
    drive.stop()
    assert [(row[6], row[10]) for row in whole(drive.log_path)] == [("-60", "4.5")]
    drive, clock = closest_and_furthest(tmp_path / "nothing better")
    for rssi in (-50, -96, -99):
        hear(drive, clock, rssi)
    drive.stop()
    assert [row[6] for row in whole(drive.log_path)] == ["-50"] and drive.rows == 1


def test_between_equal_signals_the_surer_and_then_the_earlier_sighting_is_kept(tmp_path):
    drive, clock = closest_and_furthest(tmp_path)
    for rssi, hdop, lat in ((-60, 3, 1), (-60, 0.9, 2), (-60, 0.9, 3), (-80, 3, 4), (-80, 0.9, 5), (-80, 0.9, 6)):
        hear(drive, clock, rssi, hdop=hdop, latitude=25 + lat / 1000)
    drive.stop()
    rows = whole(drive.log_path)
    assert [(row[6], row[7], row[10]) for row in rows] == [("-60", "25.0020000", "4.5"), ("-80", "25.0050000", "4.5")]


def test_a_name_heard_later_is_on_both_rows(tmp_path):
    drive, clock = closest_and_furthest(tmp_path)
    hear(drive, clock, -60, net=dict(NET, ssid="", capabilities="[ESS]"))
    hear(drive, clock, -80, net=dict(NET, ssid="", capabilities="[ESS]"))
    hear(drive, clock, -79)
    hear(drive, clock, -60, net=dict(NET, bssid="AA:BB:CC:DD:EE:02", ssid="Port, \"Royal\""))
    drive.stop()
    rows = whole(drive.log_path)
    assert [(row[1], row[2], row[6]) for row in rows] == [
        ("Tortuga", NET["capabilities"], "-60"), ("Tortuga", NET["capabilities"], "-80"),
        ('Port, "Royal"', NET["capabilities"], "-60")]


def test_rows_that_only_say_the_car_moved_are_not_kept_even_in_the_journal(tmp_path):
    drive, clock = closest_and_furthest(tmp_path)
    for step in range(20):
        clock[0] += 40
        hear(drive, clock, -70 + step % 2, latitude=25 + step / 100)
    drive.writer.sync(force=True)
    assert len(rows_of(wigle.journal_path(drive.log_path))) == 1 and drive.status()["rows"] == 1
    drive.stop()
    assert [row[6] for row in whole(drive.log_path)] == ["-70", "-69"] and drive.rows == 2


def test_the_journal_is_hidden_from_uploads_the_logs_list_and_pruning(tmp_path):
    from quartermaster import uploads
    drive, clock = closest_and_furthest(tmp_path)
    drive._wifi_sink(networks(20), ADAPTERS[0])
    journal = wigle.journal_path(drive.log_path)
    assert journal.name == ".quartermaster_20261118-110640.wigle.csv.part" and drive.active_log() == journal
    assert [path.name for path in (tmp_path / "logs").iterdir()] == [journal.name]
    assert list((tmp_path / "logs").glob(wigle.LOG_GLOB)) == [] and wigle.prune_empty(tmp_path / "logs") == []
    assert uploads.Uploader(tmp_path, {}, active=lambda: None).logs() == [] and journal.exists()
    assert drive.status()["rows"] == 20 and drive.status()["log"] == str(drive.log_path)
    drive.stop()
    assert uploads.Uploader(tmp_path, {}, active=drive.active_log).logs() == [drive.log_path]


def test_the_log_appears_only_once_it_is_whole_and_on_disk_and_then_the_journal_goes(tmp_path, monkeypatch):
    drive, clock = closest_and_furthest(tmp_path)
    drive._wifi_sink(networks(20), ADAPTERS[0])
    log, journal = drive.log_path, wigle.journal_path(drive.log_path)
    real_replace, real_settle, real_fsync, steps = os.replace, engine._settle, os.fsync, []

    def fsync(fd):
        steps.append(("fsync", os.readlink("/proc/self/fd/%d" % fd).rsplit("/", 1)[-1], log.exists()))
        return real_fsync(fd)

    def replace(src, dst):
        if str(dst) == str(log):
            steps.append(("replace", len(whole(type(log)(src))), log.exists()))
        return real_replace(src, dst)

    def settle(folder):
        steps.append(("settle", journal.exists(), log.exists()))
        return real_settle(folder)
    monkeypatch.setattr(os, "fsync", fsync)
    monkeypatch.setattr(os, "replace", replace)
    monkeypatch.setattr(engine, "_settle", settle)
    drive.stop()
    tmp = "." + log.name + ".tmp"
    assert steps[:4] == [("fsync", journal.name, False), ("fsync", tmp, False), ("replace", 20, False),
                         ("settle", True, True)]
    assert len(whole(log)) == drive.rows == 20 and not journal.exists()


def test_a_journal_that_reaches_the_rotation_size_becomes_a_log_of_its_own(tmp_path, monkeypatch):
    from quartermaster import uploads
    monkeypatch.setattr(engine, "ROTATE_BYTES", 3000)
    drive, clock = closest_and_furthest(tmp_path)
    heard = networks(60)
    for sweep in range(12):
        drive._wifi_sink([dict(net, rssi=-90 + 4 * ((sweep + i) % 12)) for i, net in enumerate(heard)], ADAPTERS[0])
        clock[0] += 1
    drive._parts.join()
    assert len(drive.log_paths) > 2
    finished = [path for path in drive.log_paths if path.exists()]
    assert finished == drive.log_paths[:-1] and not wigle.journal_path(finished[0]).exists()
    assert uploads.Uploader(tmp_path, {}, active=drive.active_log).logs() == sorted(finished)
    drive.stop()
    assert len({path.name for path in drive.log_paths}) == len(drive.log_paths) > 2
    total = {}
    for path in drive.log_paths:
        rows = whole(path)
        assert 0 < len(rows) and all(len(kept) <= 2 for kept in per_device(rows).values())
        assert [row[3] for row in rows] == sorted(row[3] for row in rows) and path.stat().st_size < 3000 + 200
        for key, kept in per_device(rows).items():
            total[key] = total.get(key, 0) + len(kept)
    assert drive.rows == sum(total.values()) == sum(len(whole(path)) for path in drive.log_paths)
    assert drive.status()["rows"] == drive.rows and drive.status()["files"] == len(drive.log_paths)
    summary = summary_of(drive.log_paths[0])
    assert {(row["Type"], row["MAC"]): int(row["LogRows"]) for row in summary} == total
    assert [path for path in (tmp_path / "logs").iterdir() if path.name.startswith(".")] == []


def test_a_part_is_finished_away_from_the_scanners_so_that_none_waits_for_it(tmp_path, monkeypatch):
    import threading
    monkeypatch.setattr(engine, "ROTATE_BYTES", 5000)
    drive, clock = closest_and_furthest(tmp_path)
    release, real = threading.Event(), wigle.compact

    def slow(*args, **kwargs):
        release.wait(10)
        return real(*args, **kwargs)
    monkeypatch.setattr(wigle, "compact", slow)
    heard = networks(60)
    drive._wifi_sink(heard[:40], ADAPTERS[0])
    first = drive.log_path
    scanner = threading.Thread(target=drive._wifi_sink, args=(heard[40:], ADAPTERS[0]))
    scanner.start()
    scanner.join(2)
    alive = scanner.is_alive()
    counted = drive.status()["rows"]
    release.set()
    scanner.join()
    assert not alive and len(drive.log_paths) == 2 and not first.exists() and counted == 60
    drive._parts.join()
    assert first.exists() and drive.rows == len(whole(first)) and drive.status()["rows"] == 60
    drive.stop()
    assert drive.rows == 60 == sum(len(whole(path)) for path in drive.log_paths)
    assert {int(row["LogRows"]) for row in summary_of(first)} == {1}


def test_with_closest_and_furthest_fewer_devices_are_kept_in_memory(tmp_path, monkeypatch):
    monkeypatch.setattr(engine, "MAX_TRACKED_EXTREMES", 10)
    drive, clock = closest_and_furthest(tmp_path)
    drive._wifi_sink(networks(20), ADAPTERS[0])
    clock[0] += 600
    drive._tidy()
    assert len(drive.tracker.devices) == 8
    drive.stop()
    assert len(summary_of(drive.log_path)) == 20 and drive.rows == 20


def test_a_journal_left_by_a_power_cut_is_finished_at_the_next_start(tmp_path):
    drive, clock = closest_and_furthest(tmp_path)
    hear(drive, clock, -50, net=dict(NET, ssid=""))
    hear(drive, clock, -90)
    drive._wifi_sink(networks(5), ADAPTERS[0])
    drive.writer.sync(force=True)
    log, journal = drive.log_path, wigle.journal_path(drive.log_path)
    with open(journal, "a", encoding="utf-8") as torn:
        torn.write("aa:bb:cc:dd:ee:01,Tortuga,[ESS],2026-11-18 11:06:50,6,2437,-30,25.1")
    (tmp_path / "logs" / ("." + log.name + ".tmp")).write_text("half")
    drive._stop.set()
    drive.writer.file.close()
    drive.writer, drive.state = None, "stopped"
    again, _ = closest_and_furthest(tmp_path)
    assert [(row[1], row[6]) for row in whole(log)][:2] == [("Tortuga", "-50"), ("Tortuga", "-90")]
    assert len(whole(log)) == 7
    assert sorted(path.name for path in (tmp_path / "logs").iterdir()) == [log.name]
    again.stop()
    assert again.log_path is None and again.rows == 0
    log.write_text("finished before the power went\n")
    journal.write_text("")
    closest_and_furthest(tmp_path)[0].stop()
    assert log.read_text() == "finished before the power went\n" and not journal.exists()


def test_a_log_that_cannot_be_compacted_keeps_its_journal_for_the_next_start(tmp_path, monkeypatch):
    drive, clock = closest_and_furthest(tmp_path)
    drive._wifi_sink(networks(20), ADAPTERS[0])
    log, journal = drive.log_path, wigle.journal_path(drive.log_path)

    def full(*args, **kwargs):
        raise OSError(28, "No space left on device")
    with monkeypatch.context() as patch:
        patch.setattr(os, "replace", full)
        drive.stop()
    assert drive.failed == drive.status()["failed"] == "Cannot write the log: No space left on device"
    assert drive.rows == drive.status()["rows"] == 0 and drive.log_path is None and drive.status()["log"] == ""
    assert [path.name for path in (tmp_path / "logs").iterdir()] == [journal.name]
    assert len(rows_of(journal)) == 20
    closest_and_furthest(tmp_path)[0].stop()
    assert len(whole(log)) == 20 and not journal.exists()


def test_a_journal_that_storage_refuses_is_finished_and_the_next_row_starts_a_new_log(tmp_path, later):
    drive, heard = closest_and_furthest(tmp_path)[0], networks(70)
    drive._wifi_sink(heard[:30], ADAPTERS[0])
    later(5)
    drive._sync()
    first = drive.log_path
    on_a_card(drive.writer, room=150)
    drive._wifi_sink(heard[30:60], ADAPTERS[0])
    later(5)
    drive._sync()
    assert drive.error == drive.failed == "Cannot write the log: No space left on device" and drive.writer is None
    assert 30 < len(whole(first)) < 60 and drive.rows == drive.status()["rows"] == len(whole(first))
    assert not wigle.journal_path(first).exists()
    drive._wifi_sink(heard[60:], ADAPTERS[0])
    assert drive.error == "" and drive.status()["rows"] == len(whole(first)) + 10
    drive.stop()
    second = drive.log_paths[1]
    assert drive.log_paths == [first, second] and second.name.endswith("-1.wigle.csv") and len(whole(second)) == 10
    assert drive.rows == len(whole(first)) + 10 and drive.failed == "Cannot write the log: No space left on device"


def test_a_journal_without_a_row_left_leaves_no_log_and_its_name_is_not_used_again(tmp_path, later):
    drive, heard = closest_and_furthest(tmp_path)[0], networks(3)
    drive._wifi_sink(heard[:1], ADAPTERS[0])
    first = drive.log_path
    on_a_card(drive.writer, room=0, error=OSError(5, "Input/output error"))
    drive._wifi_sink(heard[1:2], ADAPTERS[0])
    later(5)
    drive._sync()
    assert drive.failed == "Cannot write the log: Input/output error" and drive.log_paths == [] and drive.rows == 0
    assert list((tmp_path / "logs").iterdir()) == []
    drive._wifi_sink(heard[2:], ADAPTERS[0])
    drive.stop()
    assert drive.log_path != first and drive.log_path.name.endswith("-1.wigle.csv") and len(whole(drive.log_path)) == 1
    assert len(summary_of(drive.log_path)) == 3


def power_cut(drive):
    drive.writer.sync(force=True)
    drive._stop.set()
    drive.writer.file.close()
    drive.writer, drive.state = None, "stopped"


def test_a_journal_cut_short_holds_the_closest_and_furthest_rows_to_within_the_signal_step(tmp_path):
    drive, clock = closest_and_furthest(tmp_path)
    for i, rssi in enumerate((-70, -67, -64, -61, -58)):
        clock[0] += 40
        hear(drive, clock, rssi, latitude=25 + i / 100)
    for rssi in (-60, -99, -88, -86, -85):
        hear(drive, clock, rssi, net=dict(NET, bssid="AA:BB:CC:DD:EE:02"))
    log, journal = drive.log_path, wigle.journal_path(drive.log_path)
    power_cut(drive)
    good = ",Tortuga,[ESS],2026-11-18 11:20:00,6,2437,-30,25.1,-77.7,10,4.5,,,"
    with open(journal, "a", encoding="utf-8") as torn:
        torn.write("\0\0aa:bb:cc:dd:ee:09" + good + "WIFI\n" + "aa:bb:cc:dd:ee:09" + good + "WIFX\n")
    closest_and_furthest(tmp_path)[0].stop()
    assert [(row[0][-2:], row[6]) for row in whole(log)] == [("01", "-70"), ("01", "-58"), ("02", "-60"),
                                                             ("02", "-88")]


def test_a_part_that_rotates_holds_the_exact_ends_of_what_was_heard_while_it_was_open(tmp_path, monkeypatch):
    monkeypatch.setattr(engine, "ROTATE_BYTES", 3000)
    drive, clock = closest_and_furthest(tmp_path)
    for rssi in (-70, -68, -67, -72, -73):
        hear(drive, clock, rssi)
    drive._wifi_sink(networks(40), ADAPTERS[0])
    assert len(drive.log_paths) == 2
    drive.stop()
    first, second = (whole(path) for path in drive.log_paths)
    assert [row[6] for row in first if row[0] == "aa:bb:cc:dd:ee:01"] == ["-67", "-73"]
    assert [row for row in second if row[0] == "aa:bb:cc:dd:ee:01"] == []
    assert drive.rows == len(first) + len(second)


def test_a_name_on_the_ignore_list_keeps_a_device_out_of_the_log_even_when_heard_after_its_first_row(tmp_path):
    drive, clock = closest_and_furthest(tmp_path)
    drive.set_ignore(["My Tracker"])
    last = {}
    drive._ble_sink([{"address": "11:22:33:44:55:66", "rssi": -60, "name": ""}], last)
    drive._ble_sink([{"address": "11:22:33:44:55:66", "rssi": -61, "name": "My Tracker"}], last)
    hear(drive, clock, -60)
    hear(drive, clock, -60, net=dict(NET, bssid="AA:BB:CC:DD:EE:02", ssid="Later"))
    hear(drive, clock, -60, net=dict(NET, bssid="AA:BB:CC:DD:EE:03", ssid="Gone"))
    drive.set_ignore(["My Tracker", "Later", "aa-bb-cc-dd-ee-03"])
    drive.stop()
    assert [row[0] for row in whole(drive.log_path)] == ["aa:bb:cc:dd:ee:01"] and drive.rows == 1
    drive, clock = closest_and_furthest(tmp_path / "cut")
    hear(drive, clock, -60, net=dict(NET, ssid="Later"))
    hear(drive, clock, -60, net=dict(NET, bssid="AA:BB:CC:DD:EE:02"))
    log = drive.log_path
    power_cut(drive)
    closest_and_furthest(tmp_path / "cut", ignore=["Later"])[0].stop()
    assert [row[0] for row in whole(log)] == ["aa:bb:cc:dd:ee:02"]


def test_a_part_that_cannot_be_compacted_counts_no_rows_and_loses_no_summary_line(tmp_path, monkeypatch):
    monkeypatch.setattr(engine, "MAX_TRACKED", 10)
    monkeypatch.setattr(engine, "ROTATE_BYTES", 3000)
    drive, clock = closest_and_furthest(tmp_path)
    heard = networks(45)
    drive._wifi_sink(heard[:20], ADAPTERS[0])
    clock[0] += 600
    drive._tidy()
    first, real = drive.log_path, os.replace
    assert len(drive.tracker.devices) == 8

    def replace(src, dst):
        if str(dst) == str(first):
            raise OSError(28, "No space left on device")
        return real(src, dst)
    monkeypatch.setattr(os, "replace", replace)
    drive._wifi_sink(heard[20:], ADAPTERS[0])
    drive._parts.join()
    assert drive.failed == "Cannot write the log: No space left on device" and drive.log_path != first
    monkeypatch.setattr(os, "replace", real)
    drive.stop()
    assert drive.log_paths == [drive.log_path] and wigle.journal_path(first).exists()
    rows = per_device(whole(drive.log_path))
    assert drive.rows == sum(len(kept) for kept in rows.values()) > 0
    summary = summary_of(drive.log_path)
    assert len(summary) == 45
    for row in summary[12:]:
        assert int(row["LogRows"]) == len(rows.get(("WIFI", row["MAC"]), [])), row["MAC"]
    assert {row["LogRows"] for row in summary[12:20]} == {"0"}


def sky_drive(tmp_path, gps=None, sdr=True, fleet=FakeFleet, **cfg):
    from quartermaster import adsb
    from tests.fakes import FakeRtlAdsb
    fake = FakeRtlAdsb()
    receiver = functools.partial(adsb.Receiver, launcher=fake,
                                 which=lambda name: "/usr/bin/rtl_adsb" if name == "rtl_adsb" else None,
                                 dongles=lambda: [{"usb_id": "0bda:2838"}] if sdr else [], feeds=(),
                                 out=tmp_path / "run", cpus=lambda: 4, kill=fake.kill, poll=0.01,
                                 runner=lambda argv, **kw: subprocess.CompletedProcess(argv, 0, "", ""))
    drive = Wardrive(dict(TRACK, adsb=True, **cfg), gps or FakeGPS(FIX), root=tmp_path, adapters=ADAPTERS,
                     fleet=fleet, clock=lambda: NOW, receiver=receiver)
    drive.start()
    return drive, fake


def wait_for(test, seconds=5):
    deadline = time.monotonic() + seconds
    while not test() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert test()


def test_an_aircraft_heard_only_while_waiting_for_the_fix_is_not_counted(tmp_path):
    from tests.fakes import plane
    gps = FakeGPS({"valid": False})
    drive, fake = sky_drive(tmp_path, gps=gps)
    wait_for(lambda: fake.processes)
    fake.air(plane("a1b2c3", 25.2, -77.7))
    time.sleep(0.3)
    gps.fix = FIX
    drive.status()
    time.sleep(0.3)
    assert drive.status()["aircraft"] == 0
    fake.air(plane("c0ffee", 25.2, -77.7))
    wait_for(lambda: drive.status()["aircraft"] == 1)
    drive.stop()
    assert sorted(drive.aircraft.sky.planes) == ["c0ffee"]


def test_aircraft_are_heard_beside_wifi_and_written_beside_the_wigle_log_at_the_stop(tmp_path):
    from quartermaster import adsb
    from tests.fakes import plane
    drive, fake = sky_drive(tmp_path)
    wait_for(lambda: fake.processes)
    fake.air(plane("a1b2c3", 25.2, -77.7), plane("c0ffee"))
    wait_for(lambda: drive.status()["aircraft"] == 2)
    assert drive.status()["adsb"] == {"state": adsb.RUNNING, "aircraft": 2, "detail": "Running"}
    assert drive.status()["wifi"] == 3
    drive.stop()
    assert fake.processes[0].signals == [signal.SIGTERM] and len(fake.processes) == 1
    path = tmp_path / "logs" / "quartermaster_20261118-110640.aircraft.json.gz"
    assert drive.aircraft_path == path and drive.status()["aircraft_log"] == str(path)
    assert [a["hex"] for a in adsb.load(path)["aircraft"]] == ["a1b2c3", "c0ffee"]
    assert drive.log_path.name == "quartermaster_20261118-110640.wigle.csv" and not drive.failed
    assert sorted(p.name for p in (tmp_path / "logs").iterdir()) == [
        "quartermaster_20261118-110640.aircraft.csv", "quartermaster_20261118-110640.aircraft.json.gz",
        "quartermaster_20261118-110640.summary.csv", "quartermaster_20261118-110640.wigle.csv"]


def test_with_aircraft_off_no_receiver_is_made(tmp_path):
    def never(*args, **kwargs):
        raise AssertionError("made a receiver")
    drive = Wardrive(dict(TRACK), FakeGPS(FIX), root=tmp_path, adapters=ADAPTERS, fleet=FakeFleet,
                     clock=lambda: NOW, receiver=never)
    drive.start()
    drive.stop()
    status = drive.status()
    assert status["aircraft"] is None and status["adsb"] is None and status["aircraft_log"] == ""


def test_without_the_stick_the_wardrive_logs_wifi_as_ever_and_says_why_no_aircraft(tmp_path):
    from quartermaster import adsb
    drive, fake = sky_drive(tmp_path, sdr=False)
    wait_for(lambda: drive.status()["adsb"]["state"] == adsb.NO_DONGLE)
    assert drive.status()["rows"] == 3 and fake.started == []
    drive.stop()
    assert drive.aircraft_path is None and not drive.failed
    assert not list((tmp_path / "logs").glob("*.aircraft*"))


def test_aircraft_heard_without_a_wigle_log_are_saved_under_the_time_the_wardrive_started(tmp_path):
    from tests.fakes import plane
    drive, fake = sky_drive(tmp_path, fleet=QuietFleet)
    wait_for(lambda: fake.processes)
    fake.air(plane("a1b2c3", 25.2, -77.7))
    wait_for(lambda: drive.status()["aircraft"] == 1)
    drive.stop()
    assert drive.log_path is None and drive.aircraft_path.name == "quartermaster_20261118-110640.aircraft.json.gz"


def test_aircraft_heard_before_the_first_fix_are_not_kept(tmp_path):
    from tests.fakes import plane
    gps = FakeGPS({"valid": False})
    drive, fake = sky_drive(tmp_path, gps=gps)
    wait_for(lambda: fake.processes)
    fake.air(plane("a1b2c3", 25.2, -77.7))
    time.sleep(0.2)
    assert drive.status()["aircraft"] == 0 and drive.aircraft.count == 0
    drive.stop()
    assert drive.aircraft_path is None and list((tmp_path / "logs").iterdir()) == []


def test_aircraft_count_from_the_first_fix(tmp_path):
    from tests.fakes import plane
    gps = FakeGPS({"valid": False})
    drive, fake = sky_drive(tmp_path, gps=gps)
    wait_for(lambda: fake.processes)
    gps.fix = FIX
    drive.status()
    fake.air(plane("a1b2c3", 25.2, -77.7))
    wait_for(lambda: drive.status()["aircraft"] == 1)
    drive.stop()
    assert drive.aircraft_path is not None


def test_aircraft_left_in_a_journal_by_a_power_cut_are_finished_at_the_next_start(tmp_path):
    from quartermaster import adsb
    from tests.fakes import plane
    logs = tmp_path / "logs"
    logs.mkdir()
    sky = adsb.Sky()
    sky.update({"aircraft": [plane("a1b2c3", 25.2, -77.7)]}, NOW - 600, (FIX["latitude"], FIX["longitude"]))
    adsb.save_journal(logs / ".quartermaster_20261118-105640.aircraft.json.part", sky,
                      "quartermaster_20261118-105640.aircraft.json.gz", NOW - 590)
    drive, _ = sky_drive(tmp_path)
    finished = logs / "quartermaster_20261118-105640.aircraft.json.gz"
    assert finished.exists() and not list(logs.glob(adsb.JOURNAL_GLOB))
    assert adsb.load(finished)["now"] == NOW - 590 and drive.aircraft.target.name != finished.name
    drive.stop()


def test_with_aircraft_on_a_wardrive_starts_without_wifi_or_bluetooth(tmp_path):
    from quartermaster import adsb
    from tests.fakes import FakeRtlAdsb
    fake = FakeRtlAdsb()
    receiver = functools.partial(adsb.Receiver, launcher=fake,
                                 which=lambda name: "/usr/bin/rtl_adsb" if name == "rtl_adsb" else None,
                                 dongles=lambda: [{"usb_id": "0bda:2838"}], feeds=(), out=tmp_path / "run",
                                 cpus=lambda: 4, kill=fake.kill, poll=0.01, tools=lambda name: None,
                                 runner=lambda argv, **kw: subprocess.CompletedProcess(argv, 0, "", ""))
    bare = Wardrive(dict(TRACK), FakeGPS(FIX), root=tmp_path, adapters=[], fleet=FakeFleet, clock=lambda: NOW,
                    receiver=receiver)
    with pytest.raises(RuntimeError):
        bare.start()
    drive = Wardrive(dict(TRACK, adsb=True), FakeGPS(FIX), root=tmp_path, adapters=[], fleet=FakeFleet,
                     clock=lambda: NOW, receiver=receiver)
    drive.start()
    wait_for(lambda: fake.processes)
    assert drive.state == "running"
    drive.stop()


def test_finishing_a_journal_keeps_where_rows_start_not_the_rows_themselves(tmp_path):
    import tracemalloc

    def peak(name, devices=1000):
        journal = tmp_path / (".quartermaster_20261118-11064%d.wigle.csv.part" % len(name))
        lines = [",".join(wigle.pre_header()) + "\n", ",".join(wigle.COLUMNS) + "\n"]
        for i in range(devices):
            for k, rssi in enumerate((-50, -70, -88)):
                lines.append("aa:bb:cc:dd:%02x:%02x,%s,[ESS],2026-11-18 11:%02d:%02d,6,2437,%d,25.1,-77.3,10,4.5,,,"
                             "WIFI\n" % (i >> 8, i & 255, name, k, i % 60, rssi))
        journal.write_text("".join(lines))
        tracemalloc.start()
        try:
            kept = wigle.compact(journal, wigle.log_of(journal))
            return tracemalloc.get_traced_memory()[1], kept
        finally:
            tracemalloc.stop()
    short, kept = peak("N")
    long, _ = peak("N" * 300)
    assert len(kept) == 1000 and set(kept.values()) == {2}
    assert long - short < 1000 * 300


def test_a_node_line_heard_late_is_placed_where_and_when_it_was_heard(tmp_path):
    drive, clock = closest_and_furthest(tmp_path)
    for second in range(0, 21, 10):
        drive.gps.fix = dict(FIX, latitude=25 + second / 1000, speed=14.0)
        drive._wifi_sink([], ADAPTERS[0])
        clock[0] += 10
    clock[0] -= 10
    drive._wifi_sink([dict(NET, rssi=-60, lag=10.0, air=False)], ADAPTERS[0])
    drive._wifi_sink([dict(NET, bssid="AA:BB:CC:DD:EE:02", rssi=-60, lag=16.0, air=False)], ADAPTERS[0])
    wireless = dict(NET, bssid="AA:BB:CC:DD:EE:03", lag=0.0, air=True)
    drive._wifi_sink([dict(wireless, rssi=-60), dict(wireless, rssi=-85)], ADAPTERS[0])
    drive.stop()
    rows = {(row[0][-2:], row[6]): row for row in whole(drive.log_path)}
    assert sorted(rows) == [("01", "-60"), ("02", "-60"), ("03", "-60")]
    usb = rows[("01", "-60")]
    assert (usb[3], usb[7], usb[10]) == (engine._utc(NOW + 10), "25.0100000", "4.5")
    old = rows[("02", "-60")]
    assert (old[3], old[7], old[10]) == (engine._utc(NOW + 4), "25.0000000", str(4.5 + 14 * 4))
    assert rows[("03", "-60")][10] == "54.5"


def test_what_a_node_heard_before_the_first_fix_is_left_out_when_it_arrives_after(tmp_path):
    clock = [NOW]
    gps = FakeGPS({"valid": False})
    drive = Wardrive({"log_rows": "track", "ble": False}, gps, root=tmp_path, adapters=ADAPTERS, fleet=QuietFleet,
                     clock=lambda: clock[0])
    drive.start()
    assert drive.status()["waiting_for_fix"]
    clock[0] += 30
    gps.fix = FIX
    drive.status()
    clock[0] += 5
    early = [dict(NET, rssi=-60, lag=600.0, air=False), dict(NET, bssid="AA:BB:CC:DD:EE:02", lag=600.0)]
    drive._wifi_sink(early, ADAPTERS[0])
    drive._node_ble([{"address": "C4:7C:8D:11:22:01", "rssi": -60, "address_type": 0, "lag": 600.0}])
    status = drive.status()
    assert status["wifi"] == status["ble"] == status["rows"] == 0 and drive.skipped_no_fix == 0
    drive._wifi_sink([dict(NET, rssi=-60, lag=0.0, air=False)], ADAPTERS[0])
    drive._node_ble([{"address": "C4:7C:8D:11:22:01", "rssi": -61, "address_type": 0, "lag": 0.0}])
    assert drive.status()["wifi"] == drive.status()["ble"] == 1 and drive.status()["rows"] == 2
    gps.fix = {"valid": False}
    drive._wifi_sink([dict(NET, bssid="AA:BB:CC:DD:EE:03", lag=0.0)], ADAPTERS[0])
    assert drive.status()["wifi"] == 2 and drive.skipped_no_fix == 1
    drive.stop()
    assert sorted(row[0] for row in rows_of(drive.log_path)) == ["aa:bb:cc:dd:ee:01", "c4:7c:8d:11:22:01"]
