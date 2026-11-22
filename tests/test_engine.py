import csv
import io
import os
import stat
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
    drive = Wardrive({"ble": False}, FakeGPS(FIX), root=tmp_path, adapters=ADAPTERS, fleet=QuietFleet,
                     clock=lambda: NOW)
    drive.start()
    return drive


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
    summary = summary_of(drive.log_path)
    assert len(summary) == 3 and summary[0]["StrongestRSSI"] == "-61" and summary[0]["WeakestRSSI"] == "-61"
    assert summary[0]["StrongestLatitude"] == "25.1234567" and summary[0]["StrongestTime"] == "2026-11-18 11:06:40"


def test_no_fix_means_no_rows_and_no_file(tmp_path):
    drive = Wardrive({"ble": False, "wifi_adapters": "external"}, FakeGPS({"valid": False}), root=tmp_path,
                     adapters=ADAPTERS, fleet=FakeFleet, clock=lambda: NOW)
    drive.start()
    assert drive.status()["wifi"] == 3 and drive.status()["rows"] == 0 and drive.log_path is None
    assert drive.skipped_no_fix == 6
    drive._sync()
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
    assert drive.rows == 0 and drive.skipped_no_fix == 1
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
    assert engine.UPKEEP_SECONDS + wigle.SYNC_SECONDS < 10
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


def test_a_part_without_a_row_that_cannot_be_removed_stays_listed(tmp_path, monkeypatch, later):
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
    assert drive.log_paths == [first] and first.exists() and drive.status()["files"] == 1


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
    drive = Wardrive({"ble": False}, FakeGPS(FIX), root=tmp_path, adapters=ADAPTERS, fleet=QuietFleet,
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
    drive = Wardrive({"ble": False}, FakeGPS({"valid": False}), root=tmp_path, adapters=ADAPTERS, fleet=QuietFleet,
                     clock=lambda: clock[0])
    drive.start()
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
    aside.write_bytes(b"\xff\xfe")
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
    drive = Wardrive({"ble": False}, FakeGPS(FIX), root=tmp_path, adapters=ADAPTERS, fleet=QuietFleet,
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
            drive = Wardrive({"ble": False}, FakeGPS(FIX), root=tmp_path / root, adapters=ADAPTERS, fleet=FakeFleet,
                             clock=lambda: NOW)
            drive.start()
            drive.stop()
        assert (drive.error, drive.failed) == ("", "") and len(whole(drive.log_path)) == 3
        assert drive.log_path.with_name(drive.log_path.name.replace("wigle", "summary")).exists()
        assert len(os.listdir("/proc/self/fd")) == open_before
