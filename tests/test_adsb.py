import csv
import gzip
import io
import json
import os
import shutil
import signal
import subprocess
import sys
import time

import pytest

from quartermaster import adsb, hardware
from tests.fakes import FIX, FakeReadsb, Gps, plane, sky_json
from tests.test_core import NOW

HELP = ("Usage: readsb [OPTION...]\n  --write-json=<dir>\n  --write-json-every=<sec>\n"
        "  --preamble-threshold=<40-400>  lower threshold --> more CPU usage (default: 58, pi zero / pi 1: 75, "
        "hot CPU 89)\n")
NEAR, MID, FAR, CLOSEST = (25.10, -77.34), (25.5, -77.2), (26.0, -77.0), (25.08, -77.343)


@pytest.fixture(autouse=True)
def fresh_help(monkeypatch):
    monkeypatch.setattr(adsb, "_help", {})


class Clock:
    def __init__(self, now=NOW):
        self.now = now

    def __call__(self):
        return self.now


def helper(text=HELP, calls=None):
    def run(argv, **kwargs):
        if calls is not None:
            calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, text, "")
    return run


def receiver(tmp_path, clock, launcher=None, sdr=True, readsb=True, feeds=(), gps=None, **kwargs):
    launcher = launcher or FakeReadsb(clock)
    rx = adsb.Receiver(tmp_path / "logs", gps or Gps(FIX), clock=clock, launcher=launcher,
                       which=lambda name: "/usr/bin/readsb" if readsb else None,
                       dongles=lambda: [{"usb_id": "0bda:2838"}] if sdr else [], feeds=feeds, out=tmp_path / "run",
                       cpus=lambda: 4, runner=kwargs.pop("runner", helper()), kill=launcher.kill,
                       tools=kwargs.pop("tools", lambda name: "/usr/bin/" + name), **kwargs)
    (tmp_path / "logs").mkdir(parents=True, exist_ok=True)
    rx.prepare(NOW)
    rx.readsb = launcher
    return rx


def flight(rx, clock):
    """Four reads of one aircraft as it comes closer, goes far, and passes closest, beside aircraft to leave out."""
    fake = rx.readsb
    rx._tick()
    fake.air(plane("a1b2c3", *NEAR, rssi=-20.0), plane("~abcdef", *NEAR), plane("c0ffee", alt_baro=12000),
             plane("0a0b0c", 0.0, 0.0), plane("d00d11", 35.0, -77.0))
    rx._tick()
    clock.now += 5
    fake.air(plane("a1b2c3", *FAR, alt_baro=33000, gs=460.0, track=90.0, rssi=-30.0, seen=0.2, seen_pos=1.0))
    rx._tick()
    clock.now += 5
    fake.air(plane("a1b2c3", *MID, rssi=-25.0))
    rx._tick()
    clock.now += 5
    fake.air(plane("A1B2C3", *CLOSEST, flight="", alt_baro="ground", gs=3.0, track=None, rssi=-10.0))
    rx._tick()


def test_readsb_is_started_once_with_its_output_in_a_private_folder_and_no_network(tmp_path):
    clock = Clock()
    rx = receiver(tmp_path, clock)
    rx._tick()
    rx._tick()
    (argv, kwargs), = rx.readsb.started
    out = str(tmp_path / "run")
    assert argv == ["/usr/bin/setpriv", "--pdeathsig", "TERM", "/usr/bin/nice", "-n", "10", "/usr/bin/taskset", "-c",
                    "3", "/usr/bin/readsb", "--device-type", "rtlsdr", "--gain", "auto", "--quiet", "--write-json", out,
                    "--write-json-every", "5", "--max-range", "300", "--lat", "25.0781", "--lon", "-77.3431",
                    "--preamble-threshold", "75"]
    assert not any(arg.startswith("--net") for arg in argv)
    assert kwargs["start_new_session"] is True and kwargs["stdin"] == subprocess.DEVNULL
    assert "preexec_fn" not in kwargs
    assert kwargs["cwd"] == out and (tmp_path / "run").stat().st_mode & 0o777 == 0o700
    assert rx.status() == {"state": adsb.RUNNING, "aircraft": 0, "detail": "Running"}


def test_readsb_runs_at_low_priority_on_the_last_cpu_through_the_tools_that_are_there(tmp_path):
    two = receiver(tmp_path / "two", Clock())
    two.cpus = lambda: 2
    two._tick()
    assert two.readsb.started[0][0][:7] == ["/usr/bin/setpriv", "--pdeathsig", "TERM", "/usr/bin/nice", "-n", "10",
                                            "/usr/bin/readsb"]
    bare = receiver(tmp_path / "bare", Clock(), tools=lambda name: None)
    bare._tick()
    assert bare.readsb.started[0][0][0] == "/usr/bin/readsb"
    assert adsb.calm(5, {"nice": "/bin/nice", "taskset": "/bin/taskset"}.get) == ["/bin/nice", "-n", "10",
                                                                                  "/bin/taskset", "-c", "5"]


def test_the_help_is_asked_once_and_only_listed_flags_are_used(tmp_path):
    calls = []
    assert adsb.options("/usr/bin/readsb", helper(calls=calls)) == ["--preamble-threshold", "75"]
    assert adsb.options("/usr/bin/readsb", helper(calls=calls)) == ["--preamble-threshold", "75"]
    assert calls == [["/usr/bin/readsb", "--help"]]
    assert adsb.options("/opt/old/readsb", helper("  --write-json=<dir>\n  --quiet\n")) == []
    wrapped = ("  --preamble-threshold=<40-400>\n                             lower threshold --> more CPU usage\n"
               "                             (default: 58, pi zero / pi 1: 80)\n  --quiet\n")
    assert adsb.options("/opt/wrapped/readsb", helper(wrapped)) == ["--preamble-threshold", "80"]
    elsewhere = "  --preamble-threshold=<40-400>  lower threshold\n  --pi-zero-mode   pi zero: 90\n"
    assert adsb.options("/opt/elsewhere/readsb", helper(elsewhere)) == []

    def broken(argv, **kwargs):
        raise OSError("no such file")
    assert adsb.options("/gone/readsb", broken) == []


def test_a_readsb_the_system_already_runs_is_read_and_no_second_copy_starts(tmp_path):
    clock = Clock()
    feed = tmp_path / "system" / "readsb" / "aircraft.json"
    sky_json(feed, [plane("4ca7b3", *NEAR)], clock())
    rx = receiver(tmp_path, clock, feeds=(tmp_path / "system" / "adsb" / "aircraft.json", feed))
    rx._tick()
    assert rx.readsb.started == [] and rx.state == adsb.SYSTEM and rx.count == 1
    assert rx.status()["detail"] == "Uses the readsb that already runs"


def test_a_system_feed_that_is_old_or_broken_is_not_used(tmp_path):
    clock = Clock()
    old, broken = tmp_path / "readsb" / "aircraft.json", tmp_path / "adsb" / "aircraft.json"
    sky_json(old, [plane("4ca7b3", *NEAR)], clock() - 31)
    broken.parent.mkdir()
    broken.write_text('{"now": %d, "aircraft": [' % clock())
    rx = receiver(tmp_path, clock, feeds=(old, broken))
    rx._tick()
    assert len(rx.readsb.started) == 1 and rx.count == 0
    sky_json(old, [plane("4ca7b3", *NEAR)], clock() - 29)
    assert adsb.system_feed((broken, old), clock()) == old


def test_without_readsb_or_the_stick_nothing_starts_and_the_reason_is_given(tmp_path, monkeypatch):
    monkeypatch.setattr(adsb, "READSB_PATHS", (str(tmp_path / "none"),))
    missing = receiver(tmp_path / "a", Clock(), readsb=False)
    missing._tick()
    assert missing.readsb.started == [] and missing.status()["detail"] == "readsb is missing. Reinstall Quartermaster"
    unplugged = receiver(tmp_path / "b", Clock(), sdr=False)
    unplugged._tick()
    assert unplugged.readsb.started == [] and unplugged.status()["detail"] == "Not plugged in"
    assert unplugged.finish() is None and list((tmp_path / "b" / "logs").iterdir()) == []


def test_a_stick_held_by_another_program_is_said_and_tried_again_later(tmp_path, later):
    clock = Clock()
    rx = receiver(tmp_path, clock)
    rx._tick()
    rx.readsb.air(plane("a1b2c3", *NEAR))
    rx.readsb.processes[0].exit("Found 1 device(s):\nusb_claim_interface error -6\nFailed to open rtlsdr device\n")
    rx._tick()
    assert rx.state == adsb.BUSY and rx.status()["detail"] == "In use by another program" and rx.feed is None
    rx._tick()
    assert len(rx.readsb.started) == 1
    later(adsb.RETRY_SECONDS + 1)
    rx._tick()
    assert len(rx.readsb.started) == 2 and rx.state == adsb.RUNNING
    rx.readsb.processes[1].exit("No supported devices found.\n")
    rx._tick()
    assert rx.state == adsb.NO_DONGLE
    later(adsb.RETRY_SECONDS + 1)
    rx._tick()
    rx.readsb.processes[2].exit("Found 1 device(s):\nusb_open error -3\nPlease fix the device permissions\n")
    rx._tick()
    assert rx.state == adsb.DENIED and rx.status()["detail"] == "No permission. Run: sudo adduser $USER plugdev"


def test_the_closest_and_furthest_position_of_each_aircraft_are_kept(tmp_path):
    clock = Clock()
    rx = receiver(tmp_path, clock)
    flight(rx, clock)
    planes = rx.sky.planes
    assert sorted(planes) == ["0a0b0c", "a1b2c3", "c0ffee", "d00d11"]
    a = planes["a1b2c3"]
    assert a.near[1:] == (25.08, -77.343, "ground", 3.0, None, NOW + 15 - 0.8)
    assert a.far[1:] == (26.0, -77.0, 33000, 460.0, 90.0, NOW + 5 - 1.0)
    assert a.near[0] == pytest.approx(0.22, abs=0.01) and a.far[0] == pytest.approx(108.2, abs=0.5)
    assert (a.first, a.last) == (NOW - 0.4, NOW + 15 - 0.4)
    assert (a.strongest, a.weakest, a.flight) == (-10.0, -30.0, "TST2C3")
    for dropped in ("0a0b0c", "d00d11", "c0ffee"):
        assert planes[dropped].near is None and planes[dropped].far is None
    assert planes["c0ffee"].alt == 12000


def test_without_a_receiver_fix_a_position_is_kept_until_one_with_a_distance_comes(tmp_path):
    clock = Clock()
    gps = Gps({"valid": False})
    rx = receiver(tmp_path, clock, gps=gps)
    rx._tick()
    rx.readsb.air(plane("a1b2c3", *FAR), plane("d00d11", 35.0, -77.0), plane("0a0b0c", 0.0, 0.0))
    rx._tick()
    assert rx.sky.planes["a1b2c3"].near[:3] == (None, 26.0, -77.0) and rx.sky.planes["d00d11"].near is not None
    assert rx.sky.planes["0a0b0c"].near is None
    gps.fix = FIX
    clock.now += 5
    rx._tick()
    rx.readsb.air(plane("a1b2c3", *MID))
    rx._tick()
    assert rx.sky.planes["a1b2c3"].near[1:3] == MID and rx.sky.planes["a1b2c3"].far[1:3] == MID


def test_an_old_file_and_an_unset_clock_are_not_read(tmp_path):
    clock = Clock()
    rx = receiver(tmp_path, clock)
    rx._tick()
    rx.readsb.air(plane("a1b2c3", *NEAR), now=clock() - 31)
    rx._tick()
    assert rx.count == 0
    unset = receiver(tmp_path / "unset", Clock(86400))
    unset._tick()
    unset.readsb.air(plane("a1b2c3", *NEAR))
    unset._tick()
    assert unset.count == 0


def test_the_aircraft_file_is_dump1090_json_at_the_closest_position_and_the_summary_has_both(tmp_path):
    clock = Clock()
    rx = receiver(tmp_path, clock)
    flight(rx, clock)
    clock.now += 5
    path = rx.finish()
    assert path == tmp_path / "logs" / "quartermaster_20261118-110640.aircraft.json.gz"
    with gzip.open(path) as packed:
        data = json.loads(packed.read())
    common = {"squawk": "1200", "category": "A3"}
    assert data == {"now": NOW + 20, "messages": 0, "aircraft": [
        dict({"hex": "0a0b0c", "flight": "TSTB0C", "alt_baro": 32000, "rssi": -18.4, "seen": 20.4}, **common),
        dict({"hex": "a1b2c3", "flight": "TST2C3", "lat": 25.08, "lon": -77.343, "alt_baro": "ground", "gs": 3.0},
             rssi=-10.0, seen=5.4, seen_pos=5.8, **common),
        dict({"hex": "c0ffee", "flight": "TSTFEE", "alt_baro": 12000, "rssi": -18.4, "seen": 20.4}, **common),
        dict({"hex": "d00d11", "flight": "TSTD11", "alt_baro": 32000, "rssi": -18.4, "seen": 20.4}, **common)]}
    assert list(data["aircraft"][1]) == ["hex", "flight", "lat", "lon", "alt_baro", "gs", "squawk", "category",
                                         "rssi", "seen", "seen_pos"]
    summary = adsb.summary_path(path)
    assert summary.name == "quartermaster_20261118-110640.aircraft.csv"
    rows = list(csv.DictReader(io.StringIO(summary.read_text(encoding="utf-8"))))
    assert list(rows[0]) == list(adsb.SUMMARY_COLUMNS) and len(rows) == 4
    a = rows[1]
    assert (a["ICAO"], a["Callsign"], a["StrongestRSSI"], a["WeakestRSSI"]) == ("a1b2c3", "TST2C3", "-10.0", "-30.0")
    assert (a["FirstSeen"], a["LastSeen"]) == ("2026-11-18 11:06:39", "2026-11-18 11:06:54")
    assert (a["ClosestKm"], a["ClosestLatitude"], a["ClosestAltitudeFt"], a["ClosestTime"]) == \
        ("0.22", "25.080000", "ground", "2026-11-18 11:06:54")
    furthest = ("26.000000", "-77.000000", "33000", "460.0", "90.0", "2026-11-18 11:06:44")
    assert (a["FurthestLatitude"], a["FurthestLongitude"], a["FurthestAltitudeFt"], a["FurthestSpeedKt"],
            a["FurthestTrack"], a["FurthestTime"]) == furthest
    assert float(a["FurthestKm"]) == pytest.approx(108.2, abs=0.5) and rows[2]["ClosestLatitude"] == ""
    assert not list((tmp_path / "logs").glob(".*"))


def test_the_file_takes_the_name_of_the_wigle_log_when_there_is_one(tmp_path):
    clock = Clock()
    log = tmp_path / "logs" / "quartermaster_20261118-110702-1.wigle.csv"
    rx = receiver(tmp_path, clock, log=lambda: log)
    flight(rx, clock)
    assert rx.finish() == tmp_path / "logs" / "quartermaster_20261118-110702-1.aircraft.json.gz"


def test_a_name_already_taken_gets_the_next_number(tmp_path):
    rx = receiver(tmp_path, Clock())
    (tmp_path / "logs" / "quartermaster_20261118-110640.aircraft.json.gz").write_bytes(b"")
    rx.prepare(NOW)
    assert rx.target.name == "quartermaster_20261118-110640-1.aircraft.json.gz"


def test_a_journal_every_30_seconds_lets_a_crash_lose_nothing_and_the_next_start_finishes_it(tmp_path, later):
    clock = Clock()
    rx = receiver(tmp_path, clock)
    flight(rx, clock)
    assert not rx.journal.exists()
    later(adsb.JOURNAL_SECONDS)
    rx._tick()
    assert rx.journal.name == ".quartermaster_20261118-110640.aircraft.json.part" and rx.journal.exists()
    stamp = rx.journal.stat().st_mtime_ns
    rx._tick()
    assert rx.journal.stat().st_mtime_ns == stamp
    written = adsb.recover(tmp_path / "logs")
    assert written == [tmp_path / "logs" / "quartermaster_20261118-110640.aircraft.json.gz"]
    assert not rx.journal.exists()
    data = adsb.load(written[0])
    assert data["now"] == NOW + 15 and [a["hex"] for a in data["aircraft"]] == ["0a0b0c", "a1b2c3", "c0ffee",
                                                                                "d00d11"]
    assert data["aircraft"][1]["lat"] == 25.08 and data["aircraft"][1]["seen_pos"] == 0.8
    summary = adsb.summary_path(written[0]).read_text().splitlines()
    assert len(summary) == 5 and ",26.000000,-77.000000," in summary[2]


def test_a_journal_whose_file_exists_or_that_is_broken_is_only_removed(tmp_path, noted):
    logs = tmp_path / "logs"
    logs.mkdir()
    done = logs / "quartermaster_20261118-110640.aircraft.json.gz"
    done.write_bytes(b"kept")
    adsb.journal_path(done).write_text(json.dumps({"name": done.name, "now": NOW, "aircraft": []}))
    broken = logs / ".quartermaster_20261118-120000.aircraft.json.part"
    broken.write_text('{"name": ')
    (logs / ".quartermaster_20261118-130000.aircraft.json.gz.tmp").write_bytes(b"half")
    assert adsb.recover(logs) == []
    assert sorted(p.name for p in logs.iterdir()) == [done.name] and done.read_bytes() == b"kept"


def test_a_journal_save_cut_short_by_a_power_cut_leaves_nothing_once_recovered(tmp_path, monkeypatch):
    logs = tmp_path / "logs"
    logs.mkdir()
    journal = adsb.journal_path(logs / "quartermaster_20261118-110640.aircraft.json.gz")

    def cut(fd):
        raise SystemExit("power cut")
    monkeypatch.setattr(adsb.wigle, "fsync", cut)
    monkeypatch.setattr(adsb.Path, "unlink", lambda self, missing_ok=False: None)
    with pytest.raises(SystemExit):
        adsb.save_journal(journal, adsb.Sky(), "quartermaster_20261118-110640.aircraft.json.gz", NOW)
    monkeypatch.undo()
    assert [p.name for p in logs.iterdir()] == [journal.name + ".tmp"]
    adsb.recover(logs)
    assert list(logs.iterdir()) == []


def test_readsb_is_stopped_with_sigterm_then_sigkill(tmp_path):
    rx = receiver(tmp_path, Clock())
    rx._tick()
    rx.stop()
    assert rx.readsb.processes[0].signals == [signal.SIGTERM] and rx.proc is None
    stubborn = receiver(tmp_path / "two", Clock(), launcher=FakeReadsb(Clock(), stubborn=True))
    stubborn._tick()
    process = stubborn.readsb.processes[0]
    stubborn.stop()
    assert process.signals == [signal.SIGTERM, signal.SIGKILL] and process.waits == [adsb.STOP_WAIT, 1.0]


def test_whatever_is_still_running_is_stopped_when_quartermaster_exits(tmp_path, monkeypatch):
    rx = receiver(tmp_path, Clock())
    rx._tick()
    process = rx.readsb.processes[0]
    assert process in adsb._children
    monkeypatch.setattr(os, "killpg", rx.readsb.kill)
    adsb.end_all()
    assert process.signals == [signal.SIGTERM] and process not in adsb._children


def test_500_aircraft_are_taken_in_well_under_50_ms(tmp_path):
    sky = adsb.Sky()
    frame = {"now": NOW, "aircraft": [plane("%06x" % (0x400000 + i), 25.0 + i / 1000, -77.0 - i / 1000)
                                      for i in range(500)]}
    took = []
    for step in range(3):
        start = time.perf_counter()
        sky.update(frame, NOW + step * 5, (FIX["latitude"], FIX["longitude"]))
        took.append(time.perf_counter() - start)
    assert len(sky) == 500 and max(took) < 0.05


def test_the_stick_is_found_on_usb(tmp_path):
    for name, ids in (("1-1.3", ("0bda", "2838")), ("1-1.4", ("0BDA", "2832")), ("1-1.2", ("0e8d", "7961")),
                      ("1-1.3:1.0", None)):
        node = tmp_path / name
        node.mkdir()
        if ids:
            (node / "idVendor").write_text(ids[0] + "\n")
            (node / "idProduct").write_text(ids[1] + "\n")
    (tmp_path / "1-1.3" / "product").write_text("RTL2838UHIDIR\n")
    assert hardware.rtl_sdr_dongles(tmp_path) == [
        {"usb_id": "0bda:2838", "label": "RTL2838UHIDIR", "port": "1-1.3"},
        {"usb_id": "0bda:2832", "label": "RTL-SDR", "port": "1-1.4"}]
    assert hardware.rtl_sdr_dongles(tmp_path / "missing") == []


def test_wardrift_gets_the_times_counted_back_from_the_upload_in_parts_of_5000(tmp_path, monkeypatch):
    data = {"now": NOW, "aircraft": [{"hex": "a1b2c3", "seen": 5.4, "seen_pos": 5.8},
                                     {"hex": "c0ffee", "seen": 20.4}, {"hex": "d00d11", "seen": 1.0}]}
    first, second = adsb.batches(data, NOW + 100, size=2)
    sent = json.loads(gzip.decompress(first))
    assert sent == {"now": NOW + 100, "aircraft": [{"hex": "a1b2c3", "seen": 105.4, "seen_pos": 105.8},
                                                   {"hex": "c0ffee", "seen": 120.4}]}
    assert json.loads(gzip.decompress(second))["aircraft"] == [{"hex": "d00d11", "seen": 101.0}]
    monkeypatch.setattr(adsb, "WARDRIFT_BATCH", 5000)
    assert len(list(adsb.batches({"now": NOW, "aircraft": [{"hex": "%06x" % i, "seen": 1} for i in range(5001)]},
                                 NOW))) == 2


FAKE_READSB = r'''#!%s
import json, os, subprocess, sys, time
args = sys.argv[1:]
if "--help" in args:
    print("--write-json-every\n--preamble-threshold=<40-400> lower threshold (default: 58, pi zero / pi 1: 75)")
    sys.exit(0)
out = args[args.index("--write-json") + 1]
child = subprocess.Popen(["sleep", "60"])
with open(os.path.join(out, "child"), "w") as f:
    f.write(str(child.pid))
while True:
    with open(os.path.join(out, "aircraft.tmp"), "w") as f:
        json.dump({"now": time.time(), "aircraft": [{"hex": "a1b2c3", "seen": 0.1, "lat": 25.1, "lon": -77.3,
                                                     "seen_pos": 0.1, "rssi": -20}]}, f)
    os.replace(os.path.join(out, "aircraft.tmp"), os.path.join(out, "aircraft.json"))
    time.sleep(0.05)
'''


def gone(pid):
    try:
        with open("/proc/%d/stat" % pid) as stat:
            return stat.read().rsplit(")", 1)[1].split()[0] == "Z"
    except FileNotFoundError:
        return True


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="reads /proc")
def test_a_real_readsb_stand_in_is_read_runs_niced_and_pinned_and_its_whole_group_ends(tmp_path):
    script = tmp_path / "readsb"
    script.write_text(FAKE_READSB % sys.executable)
    script.chmod(0o755)
    (tmp_path / "logs").mkdir()
    rx = adsb.Receiver(tmp_path / "logs", Gps(FIX), which=lambda name: str(script), dongles=lambda: [{}],
                       feeds=(), out=tmp_path / "run", poll=0.05)
    rx.start(time.time())
    try:
        deadline = time.monotonic() + 10
        while (rx.count == 0 or not (tmp_path / "run" / "child").exists()) and time.monotonic() < deadline:
            time.sleep(0.02)
        assert rx.count == 1 and rx.state == adsb.RUNNING
        pid = rx.proc.pid
        with open("/proc/%d/stat" % pid) as stat:
            assert int(stat.read().rsplit(")", 1)[1].split()[16]) == 10
        if (os.cpu_count() or 1) >= 4:
            assert os.sched_getaffinity(pid) == {os.cpu_count() - 1}
        assert os.getpgid(pid) == pid
        with open("/proc/%d/cmdline" % pid, "rb") as cmdline:
            assert b"--preamble-threshold\x0075" in cmdline.read()
        child = int((tmp_path / "run" / "child").read_text())
    finally:
        rx.stop()
    deadline = time.monotonic() + 3
    while not gone(child) and time.monotonic() < deadline:
        time.sleep(0.02)
    assert gone(pid) and gone(child)
    assert rx.finish().name.endswith(".aircraft.json.gz")


def test_a_read_that_fails_is_noted_and_the_receiver_goes_on(tmp_path, noted):
    rx = receiver(tmp_path, Clock(), poll=0.01)
    ticks = []

    def broken():
        ticks.append(1)
        raise RuntimeError("boom")
    rx._tick = broken
    rx.start(NOW)
    deadline = time.monotonic() + 5
    while len(ticks) < 2 and time.monotonic() < deadline:
        time.sleep(0.01)
    rx.stop()
    assert len(ticks) >= 2 and "Aircraft" in noted.read_text()


def test_readsb_that_cannot_start_is_said_and_tried_again_later(tmp_path, noted, later):
    clock = Clock()
    fake = FakeReadsb(clock)
    tries = []

    def refuses(argv, **kwargs):
        tries.append(argv)
        raise PermissionError(13, "Permission denied")
    rx = receiver(tmp_path, clock, launcher=fake)
    rx.launcher = refuses
    rx._tick()
    rx._tick()
    assert rx.state == adsb.FAILED and len(tries) == 1 and "Starting readsb" in noted.read_text()
    later(adsb.RETRY_SECONDS + 1)
    rx.launcher = fake
    rx._tick()
    assert rx.state == adsb.RUNNING and len(fake.started) == 1


def test_a_wardrive_started_before_the_clock_was_set_is_named_after_its_first_aircraft(tmp_path):
    clock = Clock(86400)
    gps = Gps(dict(FIX, utc="2026-11-18T11:06:40Z"))
    rx = receiver(tmp_path, clock, gps=gps, epoch=lambda fix: NOW if fix.get("utc") else clock())
    rx.prepare(86400)
    rx._tick()
    rx.readsb.air(plane("a1b2c3", *NEAR))
    rx._tick()
    early = rx.journal
    adsb.save_journal(early, rx.sky, early.name[1:].replace(".part", ".gz"), 86400)
    later_path = rx.finish()
    assert later_path.name == "quartermaster_20261118-110640.aircraft.json.gz" and not early.exists()


def test_aircraft_heard_while_the_system_clock_is_days_off_are_moved_to_the_gps_time(tmp_path):
    from quartermaster.engine import gps_epoch
    behind = 5 * 86400
    clock = Clock(NOW - behind)
    gps = Gps(dict(FIX, valid=False))
    rx = receiver(tmp_path, clock, gps=gps, epoch=lambda fix: gps_epoch(fix, clock()))
    rx.prepare(clock())
    rx._tick()
    rx.readsb.air(plane("a1b2c3", *NEAR))
    rx._tick()
    assert rx.journal.name == ".quartermaster_20261113-110640.aircraft.json.part"
    rx._journaled -= adsb.JOURNAL_SECONDS
    rx._tick()
    stale = rx.journal
    assert stale.exists()
    clock.now += 10
    gps.fix = dict(FIX, utc=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(NOW + 10)))
    rx._tick()
    rx.readsb.air(plane("a1b2c3", *FAR))
    rx._tick()
    assert not stale.exists() and rx.journal.name == ".quartermaster_20261118-110640.aircraft.json.part"
    gps.fix = dict(FIX, valid=False)
    clock.now += 10
    rx.readsb.air(plane("c0ffee", *MID))
    rx._tick()
    path = rx.finish()
    assert path.name == "quartermaster_20261118-110640.aircraft.json.gz"
    data = adsb.load(path)
    assert NOW + 10 <= data["now"] < NOW + 30
    planes = {p.hex: p for p in rx.sky.planes.values()}
    assert NOW - 2 <= planes["a1b2c3"].first <= NOW and NOW + 9 <= planes["a1b2c3"].last <= NOW + 10
    assert NOW + 19 <= planes["c0ffee"].first <= NOW + 20


def test_a_system_feed_that_goes_quiet_is_left_for_a_readsb_of_our_own(tmp_path):
    clock = Clock()
    feed = tmp_path / "system" / "aircraft.json"
    sky_json(feed, [plane("4ca7b3", *NEAR)], clock())
    rx = receiver(tmp_path, clock, feeds=(feed,))
    rx._tick()
    assert rx.state == adsb.SYSTEM and rx.readsb.started == []
    clock.now += adsb.STALE_SECONDS + 1
    rx._tick()
    assert rx.state == adsb.RUNNING and len(rx.readsb.started) == 1 and rx.feed == tmp_path / "run" / "aircraft.json"


def test_the_aircraft_json_of_an_earlier_wardrive_is_not_read_into_this_one(tmp_path):
    clock = Clock()
    sky_json(tmp_path / "run" / "aircraft.json", [plane("4ca7b3", *NEAR)], clock())
    rx = receiver(tmp_path, clock)
    rx._tick()
    assert len(rx.readsb.started) == 1 and rx.count == 0 and not (tmp_path / "run" / "aircraft.json").exists()


def test_the_output_folder_must_be_a_real_folder_of_this_user(tmp_path, noted, monkeypatch):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (tmp_path / "run").symlink_to(elsewhere)
    linked = receiver(tmp_path, Clock())
    linked._tick()
    assert linked.readsb.started == [] and linked.state == adsb.FAILED and "Starting readsb" in noted.read_text()
    (tmp_path / "run").unlink()
    uid = os.getuid()
    monkeypatch.setattr(os, "getuid", lambda: uid + 1)
    other = receiver(tmp_path, Clock())
    other._tick()
    assert other.readsb.started == [] and other.state == adsb.FAILED
    monkeypatch.setattr(os, "getuid", lambda: uid)
    mine = receiver(tmp_path / "mine", Clock())
    mine._tick()
    assert len(mine.readsb.started) == 1


def test_a_weak_fix_gives_readsb_no_position_and_it_is_started_again_once_a_good_one_comes(tmp_path):
    clock = Clock()
    gps = Gps(dict(FIX, hdop=9.0))
    rx = receiver(tmp_path, clock, gps=gps, max_hdop=5.0)
    rx._tick()
    assert "--lat" not in rx.readsb.started[0][0]
    rx.readsb.air(plane("a1b2c3", *FAR))
    rx._tick()
    assert rx.sky.planes["a1b2c3"].near[0] is None
    rx._tick()
    assert len(rx.readsb.started) == 1
    gps.fix = FIX
    clock.now += 5
    rx._tick()
    first, second = rx.readsb.processes
    assert first.signals == [signal.SIGTERM] and "--lat" in second.argv and rx.state == adsb.RUNNING
    rx._tick()
    assert len(rx.readsb.started) == 2
    stale = receiver(tmp_path / "stale", Clock(), gps=Gps(dict(FIX, age_seconds=600)))
    stale._tick()
    assert "--lat" not in stale.readsb.started[0][0]


def test_first_seen_goes_back_when_an_older_report_comes_later_and_positions_keep_six_decimals():
    sky = adsb.Sky()
    sky.update({"aircraft": [plane("a1b2c3", 25.123456789, -77.987654321, seen=1.0)]}, NOW)
    sky.update({"aircraft": [plane("a1b2c3", seen=10.0)]}, NOW + 5)
    a = sky.planes["a1b2c3"]
    assert (a.first, a.last) == (NOW - 5, NOW - 1)
    entry = a.entry(NOW)
    assert (entry["lat"], entry["lon"]) == (25.123457, -77.987654)


def test_a_geometric_height_is_not_written_as_the_barometric_one():
    sky = adsb.Sky()
    report = plane("a1b2c3", 25.2, -77.7, alt_geom=31500)
    del report["alt_baro"]
    sky.update({"aircraft": [report, dict(plane("c0ffee"), alt_baro=None, alt_geom=9000)]}, NOW)
    assert "alt_baro" not in sky.planes["a1b2c3"].entry(NOW) and "alt_baro" not in sky.planes["c0ffee"].entry(NOW)


def test_the_journal_is_written_only_when_something_new_was_heard(tmp_path, later):
    clock = Clock()
    rx = receiver(tmp_path, clock)
    flight(rx, clock)
    later(adsb.JOURNAL_SECONDS)
    rx._tick()
    assert rx.journal.exists()
    rx.journal.unlink()
    later(adsb.JOURNAL_SECONDS)
    rx._tick()
    assert not rx.journal.exists()
    clock.now += 5
    rx.readsb.air(plane("bead01", *MID))
    rx._tick()
    assert rx.journal.exists() and "bead01" in rx.journal.read_text()


def test_the_same_aircraft_json_is_taken_once(tmp_path):
    clock = Clock()
    rx = receiver(tmp_path, clock)
    rx._tick()
    rx.readsb.air(plane("a1b2c3", *NEAR))
    taken = []
    update = rx.sky.update
    rx.sky.update = lambda *args: taken.append(1) or update(*args)
    rx._tick()
    rx._tick()
    assert taken == [1]


def test_between_wardrives_a_readsb_that_runs_or_another_program_on_the_stick_is_found(tmp_path):
    proc = tmp_path / "proc"
    for pid, name, parent in (("1", "systemd", 0), ("40", "bash", 1), ("41", "readsb", 99), ("self", "x", 0)):
        (proc / pid).mkdir(parents=True)
        (proc / pid / "stat").write_text("%s (%s) S %d 1 1 0 -1\n" % (pid, name, parent))
    feed = tmp_path / "readsb" / "aircraft.json"
    assert adsb.in_use((feed,), proc, NOW, me=99) == ""
    assert adsb.in_use((feed,), proc, NOW, me=7) == adsb.BUSY
    (proc / "41" / "stat").write_text("41 (rtl_tcp) S 1 1 1 0 -1\n")
    assert adsb.in_use((feed,), proc, NOW, me=7) == adsb.BUSY
    sky_json(feed, [], NOW)
    assert adsb.in_use((feed,), proc, NOW, me=7) == adsb.SYSTEM
    assert adsb.in_use((), tmp_path / "missing", NOW) == ""


QUARTERMASTER = r"""
import sys, time
from pathlib import Path
from quartermaster import adsb
from tests.fakes import FIX, Gps
script, base, mode = sys.argv[1:]
base = Path(base)
rx = adsb.Receiver(base / "logs", Gps(FIX), which=lambda name: script, dongles=lambda: [{}], feeds=(),
                   out=base / "run", poll=0.05)
rx.start(time.time())
while rx.proc is None or not (base / "run" / "child").exists():
    time.sleep(0.02)
(base / "pid").write_text(str(rx.proc.pid))
if mode == "exit":
    sys.exit(0)
time.sleep(60)
"""


def run_quartermaster(tmp_path, mode):
    script = tmp_path / "readsb"
    script.write_text(FAKE_READSB % sys.executable)
    script.chmod(0o755)
    (tmp_path / "logs").mkdir()
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    app = subprocess.Popen([sys.executable, "-c", QUARTERMASTER, str(script), str(tmp_path), mode], cwd=root,
                           env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))
    deadline = time.monotonic() + 15
    while not (tmp_path / "pid").exists() and time.monotonic() < deadline:
        time.sleep(0.02)
    return app, int((tmp_path / "pid").read_text()), int((tmp_path / "run" / "child").read_text())


def wait_gone(*pids, seconds=5):
    deadline = time.monotonic() + seconds
    while not all(gone(pid) for pid in pids) and time.monotonic() < deadline:
        time.sleep(0.02)
    return all(gone(pid) for pid in pids)


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="reads /proc")
def test_quartermaster_closing_without_a_stop_ends_readsb_and_all_it_started(tmp_path):
    app, pid, child = run_quartermaster(tmp_path, "exit")
    try:
        assert app.wait(15) == 0
        assert wait_gone(pid, child)
    finally:
        for left in (pid, child):
            if not gone(left):
                os.kill(left, signal.SIGKILL)


@pytest.mark.skipif(not sys.platform.startswith("linux") or not shutil.which("setpriv"), reason="needs setpriv")
def test_quartermaster_killed_outright_still_ends_readsb(tmp_path):
    app, pid, child = run_quartermaster(tmp_path, "sleep")
    try:
        assert not gone(pid)
        app.kill()
        app.wait(5)
        assert wait_gone(pid)
    finally:
        app.kill()
        for left in (pid, child):
            if not gone(left):
                os.kill(left, signal.SIGKILL)
