import csv
import os
import re
import shutil
import signal
import time
from types import SimpleNamespace

import pytest

from quartermaster import adsb, skyview, wigle
from tests.fakes import (FIX, NO_FIX, FakeRtlAdsb, Gps, adsb_frame, frames_for, plane, position_me, sky_json,
                         velocity_me)
from tests.test_adsb import Clock, hear as heard_by, rtl_receiver, wait_until
from tests.test_core import NOW
from tests.test_modes import EASYJET, GROUND_SPEED, KLM, LUX_EVEN, LUX_ODD

HERE = (FIX["latitude"], FIX["longitude"])
STICK = [{"usb_id": "0bda:2838"}]


@pytest.fixture
def view(tmp_path):
    made = []

    def make(gps=None, drive=None, sdr=True, tool=True, feeds=(), **kwargs):
        fake = FakeRtlAdsb()
        viewer = skyview.Viewer(tmp_path, gps or Gps(FIX), drive=drive, launcher=fake,
                                which=lambda name: "/usr/bin/rtl_adsb" if tool and name == "rtl_adsb" else None,
                                dongles=lambda: STICK if sdr else [], feeds=feeds, out=tmp_path / "run",
                                cpus=lambda: 4, kill=fake.kill, tools=lambda name: "/usr/bin/" + name,
                                clock=lambda: NOW, threads=False, **kwargs)
        viewer.rtl = fake
        made.append(viewer)
        return viewer
    yield make
    for viewer in made:
        viewer.stop()
        viewer.rtl.close()


@pytest.fixture
def rtl(tmp_path):
    made = []

    def make():
        rx = rtl_receiver(tmp_path / "drive", Clock())
        made.append(rx)
        return rx
    yield make
    for rx in made:
        rx.stop()
        rx.rtl.close()


def lines(*frames):
    return [("*%s;\r\n" % (frame if isinstance(frame, str) else frame.hex()).lower()).encode() for frame in frames]


def send(viewer, *raw):
    """rtl_adsb prints lines, and the viewer has decoded them all."""
    wanted = viewer.frames + sum(1 for line in raw if skyview.frame_of(line) is not None)
    viewer.rtl.lines(*raw)

    def taken():
        viewer.step()
        return viewer.frames >= wanted
    assert wait_until(taken)


def hear(viewer, *planes):
    send(viewer, *[line for report in planes for line in frames_for(report)])


def moved(viewer, code, places, alt=30000, pause=0.0):
    """One aircraft sends a position at each place, an even and an odd frame in turn."""
    for index, (lat, lon) in enumerate(places):
        send(viewer, *lines(adsb_frame(int(code, 16), position_me(lat, lon, alt, index % 2))))
        if pause:
            time.sleep(pause)


def test_the_viewer_starts_rtl_adsb_once_and_lists_what_it_hears(view, tmp_path):
    viewer = view()
    viewer.step()
    viewer.step()
    assert len(viewer.rtl.started) == 1 and viewer.status()["state"] == adsb.RUNNING
    argv, options = viewer.rtl.started[0]
    assert argv[-4:] == ["/usr/bin/rtl_adsb", "-d", "0", "-S"] and "/usr/bin/nice" in argv
    assert options["start_new_session"] and options["cwd"] == str(tmp_path / "run")
    assert viewer.planes() == [] and viewer.status()["detail"] == "Listening"
    hear(viewer, plane("4840d6", 25.3, -77.4, flight="KLM1023", alt_baro=38000, gs=459.0, track=270.0),
         plane("a1b2c3", 25.1, -77.45, flight="TST2C3", squawk="7700"), plane("c0ffee", flight="", gs=None))
    rows = viewer.planes("near", HERE)
    assert [row[0].hex for row in rows] == ["a1b2c3", "4840d6", "c0ffee"]
    near, klm, bare = (row[0] for row in rows)
    assert (klm.name, klm.alt_baro, klm.squawk, klm.category) == ("KLM1023", 38000, "1200", "A3")
    assert round(klm.gs) == 459 and round(klm.track) == 270 and klm.rssi is None and klm.alt_geom is None
    assert round(klm.lat, 3) == 25.3 and round(klm.lon, 3) == -77.4 and len(klm.points) == 1
    assert rows[0][1] == pytest.approx(skyview.kilometres(HERE, 25.1, -77.45), abs=0.05) and 0 <= rows[0][2] < 360
    assert rows[1][1] > rows[0][1] and rows[2][1] is None and rows[2][2] is None
    assert (bare.name, bare.flight, bare.gs, bare.lat, bare.squawk, bare.points) == ("C0FFEE", None, None, None,
                                                                                      "1200", [])
    assert (bare.category, bare.track, bare.rate, bare.alt_baro) == ("A3", None, None, 32000)
    assert near.squawk == "7700" and near.messages >= 5 and near.first <= near.last
    assert [row[1] for row in viewer.planes("near", None)] == [None] * 3
    assert viewer.status()["aircraft"] == 3 and viewer.status()["raw"]


def test_the_newest_come_first_when_asked_and_a_distance_needs_a_fresh_position(view, later):
    viewer = view()
    viewer.step()
    hear(viewer, plane("4840d6", 25.3, -77.4))
    later(70)
    hear(viewer, plane("a1b2c3", flight="", gs=None))
    assert [row[0].hex for row in viewer.planes("latest", HERE)] == ["a1b2c3", "4840d6"]
    assert [row[1] for row in viewer.planes("near", HERE)] == [None, None]


def test_one_frame_with_good_parity_or_noise_does_not_make_an_aircraft(view):
    viewer = view()
    viewer.step()
    send(viewer, *lines(KLM), b"*8d4840d6202cc371c32ce0576099;\r\n", b"rubbish\r\n", b"*ffff;\r\n")
    assert viewer.planes() == [] and viewer.frames == 2
    send(viewer, *lines(KLM))
    assert [row[0].name for row in viewer.planes()] == ["KLM1023"]


def test_known_frames_show_as_their_published_values(view):
    viewer = view(gps=Gps(NO_FIX))
    viewer.step()
    send(viewer, *lines(KLM, KLM, EASYJET, EASYJET, LUX_EVEN, LUX_ODD, LUX_ODD, GROUND_SPEED, GROUND_SPEED))
    found = {row[0].hex: row[0] for row in viewer.planes()}
    assert found["4840d6"].flight.strip() == "KLM1023" and found["406b90"].name == "EZY85MH"
    assert found["406b90"].category is None
    lux = found["40058b"]
    assert (round(lux.lat, 5), round(lux.lon, 5), lux.alt_baro) == (49.81755, 6.08442, 39000)
    fast = found["485020"]
    assert (fast.gs, fast.track, fast.geom_rate, fast.baro_rate, fast.rate) == (159.2, 182.9, -832, None, -832)
    assert fast.alt_baro is None and fast.alt_geom is None and fast.lat is None


def test_the_gnss_height_comes_from_the_difference_an_aircraft_sends(view):
    viewer = view()
    viewer.step()
    hear(viewer, plane("485020", 25.2, -77.5, alt_baro=30000, gs=None))
    send(viewer, *lines(GROUND_SPEED))
    assert viewer.find("485020").alt_geom == 30550


def test_the_track_keeps_every_place_until_it_is_long_and_then_thins_out(view, monkeypatch):
    monkeypatch.setattr(skyview, "POINT_SECONDS", 0.0)
    monkeypatch.setattr(skyview, "TRACK_POINTS", 8)
    viewer = view()
    viewer.step()
    places = [(25.2 + index * 0.002, -77.5 + index * 0.003) for index in range(14)]
    moved(viewer, "a1b2c3", places[:10])
    track = viewer.track("a1b2c3")
    assert [round(point[1], 3) for point in track] == [round(lat, 3) for lat, _ in places[2:10]]
    assert all(point[3] == 30000 and point[4] is None for point in track) and viewer.find("a1b2c3").gap == 0.0
    moved(viewer, "a1b2c3", places[10:11])
    track = viewer.track("a1b2c3")
    assert [round(point[1], 3) for point in track] == [round(lat, 3) for lat, _ in places[2:11:2]]
    moved(viewer, "a1b2c3", places[11:])
    track = viewer.track("a1b2c3")
    assert len(track) == 8 and round(track[-1][1], 3) == round(places[-1][0], 3)
    assert round(track[0][1], 3) == round(places[2][0], 3)
    assert viewer.track("ffffff") == []


def test_a_place_is_kept_at_most_once_a_second_for_the_screen(view):
    viewer = view()
    viewer.step()
    places = [(25.2 + index * 0.001, -77.5) for index in range(6)]
    moved(viewer, "a1b2c3", places)
    seen, track = viewer.find("a1b2c3"), viewer.track("a1b2c3")
    assert len(track) == 1 and round(track[0][1], 3) == round(places[2][0], 3) and viewer.parts("a1b2c3") == [track]
    assert round(seen.lat, 3) == round(places[5][0], 3) and seen.pos_time >= track[0][0]
    assert seen.heard == 4


def spot(when):
    return (when, 25.0 + when * 2e-6, -77.0, 30000, 100.0, 0.0, 0, None)


def test_the_newest_position_ends_the_track_once_the_ones_kept_are_further_apart():
    seen = skyview.Seen("abcdef", 0.0)
    seen.gap = 8.0
    for when, kept, shown in ((0.0, [0.0], [0.0]), (0.5, [0.0], [0.0]), (3.0, [0.0], [0.0, 3.0]),
                              (3.5, [0.0], [0.0, 3.5]), (8.0, [0.0, 8.0], [0.0, 8.0]),
                              (8.9, [0.0, 8.0], [0.0, 8.0]), (9.0, [0.0, 8.0], [0.0, 8.0, 9.0])):
        seen.place(spot(when))
        assert [point[0] for point in seen.points] == kept and [point[0] for point in seen.route()] == shown
        assert seen.parts() == [seen.route()]
    seen.place(spot(9.5 + skyview.SILENCE_SECONDS))
    assert [[point[0] for point in part] for part in seen.parts()] == [[0.0, 8.0, 9.0], [129.5]]
    seen.place(spot(130.0 + skyview.SILENCE_SECONDS + 5))
    assert [[point[0] for point in part] for part in seen.parts()] == [[0.0, 8.0, 9.0], [129.5], [255.0]]


def flown(seen, start, until, step=0.5):
    """An aircraft sends a position every half second from `start` until `until`. Returns when the next is due."""
    at = start
    while at < until:
        seen.place(spot(at))
        at += step
    return at


def test_a_track_heard_for_hours_thins_out_and_stays_in_one_part():
    seen = skyview.Seen("abcdef", 0.0)
    due = flown(seen, 0.0, 4.5 * 3600)
    assert seen.gap == 128.0 and 120 <= len(seen.points) <= skyview.TRACK_POINTS and seen.cuts == set()
    assert seen.heard == 4.5 * 3600 * 2
    track = seen.route()
    assert seen.parts() == [track] and track[0][0] == 0.0 and 0 <= due - 0.5 - track[-1][0] < skyview.POINT_SECONDS
    assert all(later[0] - earlier[0] >= 128.0 for earlier, later in zip(track, track[1:-1]))
    assert track[-1][0] - track[-2][0] >= skyview.POINT_SECONDS


def test_only_a_silence_of_two_minutes_parts_the_track_and_the_mark_stays_as_it_thins_out():
    seen = skyview.Seen("abcdef", 0.0)
    quiet = flown(seen, 0.0, 3 * 3600) - 0.5
    again = quiet + skyview.SILENCE_SECONDS + 1
    due = flown(seen, again, again + 300)
    before, after = seen.parts()
    assert 0 <= quiet - before[-1][0] < skyview.POINT_SECONDS and after[0][0] == again and before[0][0] == 0.0
    gap = seen.gap
    due = flown(seen, due + skyview.SILENCE_SECONDS - 1, 9 * 3600)
    assert seen.gap > gap and len(seen.points) <= skyview.TRACK_POINTS
    before, after = seen.parts()
    assert before[-1][0] <= quiet and after[0][0] >= again and 0 <= due - 0.5 - after[-1][0] < skyview.POINT_SECONDS
    assert before + after == seen.route() and len(before) > 10 and len(after) > 10


def test_a_part_of_one_position_and_many_silences_are_kept_apart(monkeypatch):
    monkeypatch.setattr(skyview, "TRACK_POINTS", 8)
    seen = skyview.Seen("abcdef", 0.0)
    for index in range(40):
        seen.place((index * 200.0, 25.0 + index * 0.01, -77.0, None, None, None, None, None))
        assert len(seen.points) <= 8
    parts = seen.parts()
    assert all(len(part) == 1 for part in parts) and len(parts) == len(seen.route()) >= 5
    assert parts[0][0][0] == 0.0 and parts[-1][0][0] == 39 * 200.0


def test_an_aircraft_heard_again_after_a_long_silence_starts_afresh(view, later):
    viewer = view()
    viewer.step()
    hear(viewer, plane("4840d6", 25.3, -77.4, flight="KLM1023", alt_baro=38000, squawk="1200"))
    seen = viewer.find("4840d6")
    assert (seen.name, seen.alt_baro, seen.squawk, seen.category, len(seen.points)) == ("KLM1023", 38000, "1200",
                                                                                         "A3", 1)
    first, messages, placed = seen.first, seen.messages, seen.pos_time
    later(60)
    hear(viewer, plane("4840d6", flight="KLM1023", alt_baro=None, gs=None, squawk=None))
    assert (seen.alt_baro, seen.squawk, round(seen.gs)) == (38000, "1200", 451)
    later(skyview.modes.FORGET_SECONDS + 5)
    hear(viewer, plane("4840d6", flight="KLM99", alt_baro=None, gs=None, squawk=None))
    assert viewer.find("4840d6") is seen and seen.name == "KLM99" and seen.category == "A3"
    assert (seen.alt_baro, seen.alt_geom, seen.squawk, seen.gs, seen.track, seen.rate) == (None,) * 6
    assert seen.first == first and seen.messages == messages + 4 and seen.last > first + 300
    assert seen.pos_time == placed and len(seen.points) == 1
    assert viewer.planes("near", HERE)[0][1] is None


def test_an_aircraft_that_stands_still_has_a_speed_of_zero_and_no_heading(view, tmp_path):
    viewer = view()
    viewer.step()
    hear(viewer, plane("a1b2c3", 25.2, -77.5, gs=0.0, track=0.0))
    seen = viewer.find("a1b2c3")
    assert (seen.gs, seen.track, seen.heading) == (0.0, None, None)
    hear(viewer, plane("a1b2c3", gs=96.0, track=270.0))
    assert round(seen.gs) == 96 and round(seen.track) == 270
    send(viewer, *lines(adsb_frame(0xA1B2C3, velocity_me(0, 0.0))))
    assert (seen.gs, seen.track, seen.heading) == (0.0, None, None)
    viewer.capture_start()
    moved(viewer, "a1b2c3", [(25.2001, -77.5)], alt=32000)
    name = viewer.capture_finish()
    rows = read_track(tmp_path / "aircraft" / (name + ".track.csv"))
    assert [(row["SpeedKt"], row["Heading"]) for row in rows] == [("", ""), ("0.0", "")]


def test_an_aircraft_another_program_lists_at_0_kt_without_a_track_has_no_heading(view, tmp_path):
    feed = tmp_path / "readsb" / "aircraft.json"
    sky_json(feed, [plane("4840d6", 25.3, -77.4, gs=96.0, track=270.0)], NOW)
    viewer = view(feeds=(feed,))
    viewer.step()
    seen = viewer.find("4840d6")
    assert (seen.gs, seen.track) == (96.0, 270.0)
    standing = plane("4840d6", 25.3, -77.4, gs=0)
    del standing["track"]
    for second, said, heading in ((1, standing, None), (2, plane("4840d6", 25.3, -77.4, gs=0, track=0.0), 0.0),
                                  (3, {"hex": "4840d6", "seen": 0.1}, None)):
        viewer._feed_at = 0.0
        sky_json(feed, [said], NOW + second)
        viewer.clock = lambda: NOW + second
        viewer.step()
        assert (seen.gs, seen.track, seen.heading) == (0, heading, heading)


def test_only_so_many_aircraft_are_kept_and_the_longest_unheard_go_first(view, monkeypatch):
    monkeypatch.setattr(skyview, "MAX_SEEN", 3)
    viewer = view()
    viewer.step()
    for index in range(5):
        hear(viewer, plane("a0000%d" % index, flight="", gs=None))
        time.sleep(0.01)
    assert sorted(viewer.seen) == ["a00002", "a00003", "a00004"]


def read_track(path):
    with open(path, newline="") as source:
        return list(csv.DictReader(source))


def test_a_capture_saves_what_is_in_view_and_then_everything_heard(view, tmp_path, monkeypatch):
    synced = []
    monkeypatch.setattr(wigle, "fsync", lambda fd: synced.append(fd))
    viewer = view()
    viewer.step()
    hear(viewer, plane("4840d6", 25.3, -77.4, flight="KLM1023", alt_baro=38000, gs=459.0, track=270.0, squawk="4521"))
    before = viewer.frames
    assert viewer.capture_start() == "" and viewer.capture_start() == ""
    folder = tmp_path / "aircraft"
    hidden = sorted(path.name for path in folder.iterdir())
    assert len(hidden) == 2 and all(name.startswith(".quartermaster_") and name.endswith(".part") for name in hidden)
    assert synced and viewer.status()["capture"]["points"] == 1 and viewer.status()["capture"]["frames"] == before
    moved(viewer, "4840d6", [(25.31, -77.41), (25.32, -77.42), (25.33, -77.43)], alt=37000)
    hear(viewer, plane("a1b2c3", flight="", gs=None, squawk=None))
    name = viewer.capture_finish()
    assert re.fullmatch(r"quartermaster_\d{8}-\d{6}", name) and viewer.capture is None
    assert sorted(path.name for path in folder.iterdir()) == [name + ".frames.avr", name + ".track.csv"]
    rows = read_track(folder / (name + ".track.csv"))
    assert list(rows[0]) == list(skyview.TRACK_COLUMNS) and len(rows) == 4
    first = rows[0]
    assert (first["ICAO"], first["Callsign"], first["AltitudeFt"], first["Squawk"]) == ("4840D6", "KLM1023",
                                                                                        "38000", "")
    assert float(first["Latitude"]) == pytest.approx(25.3, abs=1e-4)
    assert float(first["Longitude"]) == pytest.approx(-77.4, abs=1e-4) and len(first["Latitude"]) == 9
    assert first["SpeedKt"] == "" and first["Heading"] == "" and first["VerticalRateFpm"] == ""
    assert [row["AltitudeFt"] for row in rows[1:]] == ["37000"] * 3 and rows[3]["Squawk"] == "4521"
    assert rows[3]["SpeedKt"] == "459.0" and rows[3]["Heading"] == "270.0"
    assert float(rows[3]["Latitude"]) == pytest.approx(25.33, abs=1e-4)
    assert all(re.fullmatch(r"20\d\d-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z", row["Time"]) for row in rows)
    assert [row["Time"] for row in rows] == sorted(row["Time"] for row in rows)
    frames = (folder / (name + ".frames.avr")).read_text().splitlines()
    assert len(frames) == viewer.frames and all(re.fullmatch(r"@[0-9A-F]{12}([0-9A-F]{14}|[0-9A-F]{28});", line)
                                                for line in frames)
    ticks = [int(line[1:13], 16) for line in frames]
    assert ticks == sorted(ticks) and ticks[-1] < 60 * skyview.TICKS
    assert frames[0][13:-1] == frames_for(plane("4840d6", flight="KLM1023"))[0][1:29].decode().upper()
    assert all(stat_mode(path) == 0o600 for path in folder.iterdir())


def stat_mode(path):
    return os.stat(path).st_mode & 0o777


def test_the_times_of_a_capture_are_utc_from_the_gps(view, tmp_path):
    viewer = view(gps=Gps(dict(FIX, utc="2026-03-04T05:06:07Z")))
    viewer.step()
    hear(viewer, plane("4840d6", 25.3, -77.4))
    viewer.capture_start()
    name = viewer.capture_finish()
    assert name.startswith("quartermaster_20260304-0506")
    assert read_track(tmp_path / "aircraft" / (name + ".track.csv"))[0]["Time"].startswith("2026-03-04T05:06:0")


def test_the_time_the_gps_gave_is_kept_for_a_capture_started_after_its_fix_is_lost(view, tmp_path):
    gps = Gps(dict(FIX, utc="2026-03-04T05:06:07Z"))
    viewer = view(gps=gps)
    viewer.step()
    gps.fix = NO_FIX
    hear(viewer, plane("4840d6", 25.3, -77.4))
    assert viewer.capture_start() == "" and viewer.capture.timed
    name = viewer.capture_finish()
    assert name.startswith("quartermaster_20260304-0506")
    assert read_track(tmp_path / "aircraft" / (name + ".track.csv"))[0]["Time"].startswith("2026-03-04T05:06:")


def test_the_time_the_gps_gave_before_the_viewer_opened_is_used_too(view, tmp_path):
    gps = Gps(NO_FIX, said=("2026-03-04T05:06:07Z", time.monotonic() - 600))
    viewer = view(gps=gps)
    viewer.step()
    hear(viewer, plane("4840d6", 25.3, -77.4))
    assert viewer.capture_start() == "" and viewer.capture.timed
    name = viewer.capture_finish()
    assert name.startswith("quartermaster_20260304-0516")
    assert read_track(tmp_path / "aircraft" / (name + ".track.csv"))[0]["Time"].startswith("2026-03-04T05:16:")
    for said in (None, ("no time", time.monotonic())):
        unsure = view(gps=Gps(NO_FIX, said=said))
        unsure.step()
        assert unsure.capture_start() == "" and not unsure.capture.timed
        assert unsure.capture.stem.startswith("quartermaster_20261118-1106")


def test_a_capture_begun_on_the_system_clock_is_moved_to_the_gps_time_once_the_gps_tells_it(view, tmp_path):
    gps = Gps(NO_FIX)
    viewer = view(gps=gps)
    viewer.step()
    hear(viewer, plane("4840d6", 25.3, -77.4, flight="KLM1023"))
    assert viewer.capture_start() == "" and not viewer.capture.timed
    began = viewer.capture.stem
    assert began.startswith("quartermaster_20261118-1106")
    moved(viewer, "4840d6", [(25.31, -77.41)])
    folder = tmp_path / "aircraft"
    viewer.capture.sync(force=True)
    early = (folder / ("." + began + ".frames.avr.part")).read_text().splitlines()
    times = [row["Time"] for row in read_track(folder / ("." + began + ".track.csv.part"))]
    assert len(early) == viewer.frames and len(times) == 2 and times[0].startswith("2026-11-18T11:06:")
    gps.fix = dict(FIX, utc="2026-03-04T05:06:07Z")
    viewer.step()
    capture = viewer.capture
    assert capture.timed and capture.stem.startswith("quartermaster_20260304-050")
    assert sorted(path.name for path in folder.iterdir()) == ["." + capture.stem + ".frames.avr.part",
                                                               "." + capture.stem + ".track.csv.part"]
    moved(viewer, "4840d6", [(25.32, -77.42)])
    viewer.step()
    name = viewer.capture_finish()
    assert name == capture.stem and sorted(path.name for path in folder.iterdir()) == [name + ".frames.avr",
                                                                                      name + ".track.csv"]
    rows = read_track(folder / (name + ".track.csv"))
    assert [round(float(row["Latitude"]), 3) for row in rows] == [25.3, 25.31, 25.32]
    assert all(row["Time"].startswith("2026-03-04T05:0") for row in rows)
    assert [row["Time"][19:] for row in rows[:2]] == [time[19:] for time in times]
    assert [row["Time"] for row in rows] == sorted(row["Time"] for row in rows)
    frames = (folder / (name + ".frames.avr")).read_text().splitlines()
    ticks = [int(line[1:13], 16) for line in frames]
    assert frames[:len(early)] == early and len(frames) == viewer.frames
    assert ticks == sorted(ticks) and ticks[-1] < 60 * skyview.TICKS
    assert time.strftime("%Y%m%d-%H%M%S", time.gmtime(skyview.captures(folder)[0]["epoch"])) == name[14:]


def test_a_capture_keeps_its_name_when_the_gps_agrees_with_the_system_clock(view, tmp_path):
    gps = Gps(NO_FIX)
    viewer = view(gps=gps)
    viewer.step()
    assert viewer.capture_start() == "" and not viewer.capture.timed
    began = viewer.capture.stem
    gps.fix = dict(FIX, utc=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(NOW + 1)))
    viewer.step()
    assert viewer.capture.timed and viewer.capture.stem == began
    gps.fix = dict(FIX, utc="2026-03-04T05:06:07Z")
    viewer.step()
    hear(viewer, plane("4840d6", 25.3, -77.4))
    assert viewer.capture_finish() == began


def test_a_capture_that_cannot_be_moved_to_the_gps_time_goes_on_as_it_was(view, tmp_path, monkeypatch):
    gps = Gps(NO_FIX)
    viewer = view(gps=gps)
    viewer.step()
    hear(viewer, plane("4840d6", 25.3, -77.4))
    viewer.capture_start()
    began = viewer.capture.stem

    def full(line, seconds):
        raise OSError(28, "No space left on device")
    monkeypatch.setattr(skyview, "moved_row", full)
    gps.fix = dict(FIX, utc="2026-03-04T05:06:07Z")
    viewer.step()
    assert viewer.capture is not None and viewer.capture.stem == began and viewer.capture.timed and not viewer.note
    hear(viewer, plane("a1b2c3", 25.2, -77.5))
    assert viewer.capture_finish() == began
    folder = tmp_path / "aircraft"
    assert sorted(path.name for path in folder.iterdir()) == [began + ".frames.avr", began + ".track.csv"]
    assert len(read_track(folder / (began + ".track.csv"))) == 2
    assert len((folder / (began + ".frames.avr")).read_text().splitlines()) == viewer.frames


def test_a_capture_does_not_start_on_a_clock_that_was_never_set(view, tmp_path):
    gps = Gps(NO_FIX)
    viewer = view(gps=gps)
    viewer.clock = lambda: 400 * 86400.0
    viewer.step()
    assert viewer.capture_start() == "The time is not known yet. Wait for a GPS fix." and viewer.capture is None
    assert list((tmp_path / "aircraft").iterdir()) == []
    gps.fix = dict(FIX, utc="2026-03-04T05:06:07Z")
    assert viewer.capture_start() == "" and viewer.capture.timed
    assert viewer.capture.stem.startswith("quartermaster_20260304-0506")


def test_a_second_capture_gets_its_own_name_and_only_new_frames(view, tmp_path):
    viewer = view(epoch=lambda fix: NOW + 0.5)
    viewer.step()
    hear(viewer, plane("4840d6", 25.3, -77.4))
    viewer.capture_start()
    first = viewer.capture_finish()
    viewer.capture_start()
    hear(viewer, plane("a1b2c3", flight="", gs=None))
    second = viewer.capture_finish()
    folder = tmp_path / "aircraft"
    assert first != second and second.startswith(first[:29])
    assert len((folder / (second + ".frames.avr")).read_text().splitlines()) == 6
    assert len(read_track(folder / (second + ".track.csv"))) == 1
    assert [entry["stem"] for entry in skyview.captures(folder)] == sorted([first, second], reverse=True)


def test_a_capture_with_nothing_heard_leaves_no_file(view, tmp_path):
    viewer = view()
    viewer.step()
    assert viewer.capture_start() == "" and viewer.capture_finish() == ""
    assert list((tmp_path / "aircraft").iterdir()) == [] and skyview.captures(tmp_path / "aircraft") == []


def test_the_frames_kept_in_memory_are_bounded_and_go_to_disk_while_a_capture_runs(view, tmp_path, monkeypatch):
    monkeypatch.setattr(skyview, "RAW_KEEP", 5)
    viewer = view()
    viewer.step()
    assert viewer._raw.maxlen == 5
    for _ in range(4):
        send(viewer, *lines(KLM, KLM, KLM))
    assert len(viewer._raw) == 5
    viewer.capture_start()
    assert len(viewer._raw) == 0
    for _ in range(4):
        send(viewer, *lines(KLM, KLM, KLM))
    assert len(viewer._raw) == 0 and viewer.status()["capture"]["frames"] == 17
    name = viewer.capture_finish()
    assert len((tmp_path / "aircraft" / (name + ".frames.avr")).read_text().splitlines()) == 17
    send(viewer, *lines(KLM))
    assert len(viewer._raw) == 1


def test_a_capture_is_put_on_disk_every_few_seconds(view, monkeypatch, later):
    viewer = view()
    viewer.step()
    viewer.capture_start()
    synced = []
    monkeypatch.setattr(wigle, "fsync", lambda fd: synced.append(fd))
    send(viewer, *lines(KLM))
    assert synced == []
    later(wigle.SYNC_SECONDS + 0.5)
    viewer.step()
    assert len(synced) == 2
    viewer.step()
    assert len(synced) == 2


def test_a_capture_does_not_start_on_full_storage_and_ends_before_it_fills_it(view, tmp_path, monkeypatch):
    usage = shutil.disk_usage(tmp_path)
    free = [skyview.MIN_FREE_BYTES - 1]
    monkeypatch.setattr(shutil, "disk_usage", lambda path: usage._replace(free=free[0]))
    viewer = view()
    viewer.step()
    assert viewer.capture_start() == "Storage is almost full (under 32 MB free)" and viewer.capture is None
    free[0] = skyview.MIN_FREE_BYTES
    assert viewer.capture_start() == ""
    hear(viewer, plane("4840d6", 25.3, -77.4))
    free[0] = skyview.MIN_FREE_BYTES // 2 - 1
    viewer.step()
    assert viewer.capture is None and viewer.note == skyview.FULL
    assert len(skyview.captures(tmp_path / "aircraft")[0]["files"]) == 2


def test_a_capture_ends_at_its_size_limit(view, tmp_path, monkeypatch):
    monkeypatch.setattr(skyview, "CAPTURE_BYTES", 200)
    viewer = view()
    viewer.step()
    viewer.capture_start()
    send(viewer, *lines(*[KLM] * 6))
    assert viewer.capture is None and viewer.note == skyview.LARGE and skyview.captures(tmp_path / "aircraft")


def test_a_capture_that_cannot_be_written_ends_and_says_so(view, tmp_path, monkeypatch):
    viewer = view()
    viewer.step()
    viewer.capture_start()

    def refuse(fd):
        raise OSError(5, "Input/output error")
    monkeypatch.setattr(wigle, "fsync", refuse)
    monkeypatch.setattr(skyview.Capture, "sync_every", 0.0, raising=False)
    viewer.capture.sync_every = 0.0
    send(viewer, *lines(KLM))
    assert viewer.capture is None and viewer.note == skyview.BROKEN


def test_a_capture_a_power_cut_ended_is_finished_when_the_viewer_opens_again(view, tmp_path):
    viewer = view()
    viewer.step()
    hear(viewer, plane("4840d6", 25.3, -77.4))
    viewer.capture_start()
    viewer.capture.sync(force=True)
    stem = viewer.capture.stem
    folder = tmp_path / "aircraft"
    for path in folder.iterdir():
        with open(path, "a") as cut:
            cut.write("@0000000000FF8D48" if "frames" in path.name else "2026-11-26T10:00:00.000Z,4840D6,KL")
    alone = folder / ".quartermaster_20250101-000000.track.csv.part"
    alone.write_text(",".join(skyview.TRACK_COLUMNS) + "\n")
    (folder / ".quartermaster_20250101-000000.track.csv.part.tmp").write_text("half a copy")
    stranger = folder / ".quartermaster_notes.part"
    stranger.write_text("mine\n")
    assert skyview.recover(folder, live=(viewer.capture.track, viewer.capture.frames)) == []
    viewer.capture._close()
    viewer.capture = None
    again = view()
    again.step()
    assert sorted(path.name for path in folder.iterdir()) == [".quartermaster_notes.part", stem + ".frames.avr",
                                                               stem + ".track.csv"]
    assert len(read_track(folder / (stem + ".track.csv"))) == 1
    frames = (folder / (stem + ".frames.avr")).read_text()
    assert frames.endswith(";\n") and len(frames.splitlines()) == viewer.frames
    assert all(re.fullmatch(r"@[0-9A-F]{12}([0-9A-F]{14}|[0-9A-F]{28});", line) for line in frames.splitlines())


def test_captures_are_listed_newest_first_and_deleted_whole(tmp_path):
    folder = tmp_path / "aircraft"
    folder.mkdir()
    for name in ("quartermaster_20261126-101500.track.csv", "quartermaster_20261126-101500.frames.avr",
                 "quartermaster_20261127-080000.track.csv", "notes.txt",
                 ".quartermaster_20261127-090000.track.csv.part"):
        (folder / name).write_text("x" * 10)
    found = skyview.captures(folder)
    assert [entry["stem"] for entry in found] == ["quartermaster_20261127-080000", "quartermaster_20261126-101500"]
    assert (found[0]["track"], found[0]["frames"], found[0]["size"]) == (True, False, 10)
    assert (found[1]["track"], found[1]["frames"], found[1]["size"]) == (True, True, 20)
    assert time.strftime("%Y-%m-%d %H:%M", time.gmtime(found[1]["epoch"])) == "2026-11-26 10:15"
    assert skyview.delete(found[1])
    assert sorted(path.name for path in folder.iterdir()) == [".quartermaster_20261127-090000.track.csv.part",
                                                               "notes.txt", "quartermaster_20261127-080000.track.csv"]
    assert skyview.captures(tmp_path / "nowhere") == []


def test_closing_the_viewer_ends_rtl_adsb_and_saves_a_capture_still_running(view, tmp_path):
    viewer = view()
    viewer.step()
    hear(viewer, plane("4840d6", 25.3, -77.4))
    viewer.capture_start()
    process = viewer.rtl.processes[0]
    name = viewer.stop()
    assert process.signals == [signal.SIGTERM] and viewer.proc is None
    assert name and len(skyview.captures(tmp_path / "aircraft")[0]["files"]) == 2 and viewer.stop() == ""


@pytest.mark.parametrize("said, state, words", [
    ("usb_claim_interface error -6\n", adsb.BUSY, "In use by another program"),
    ("usb_open error -3\n", adsb.DENIED, "No permission yet. Restart the Cardputer once."),
    ("Kernel driver is active, or device is claimed\n", adsb.DRIVER, "Restart the Cardputer once to use it"),
    ("No supported devices found.\n", adsb.NO_DONGLE, "Not plugged in"),
    ("Segmentation fault\n", adsb.FAILED, "The radio stopped. Trying again soon.")])
def test_why_the_viewer_cannot_listen_is_said_and_it_tries_again(view, later, said, state, words):
    viewer = view()
    viewer.step()
    viewer.rtl.processes[0].exit(said)
    viewer.step()
    assert viewer.status()["state"] == state and viewer.status()["detail"] == words
    assert not viewer.status()["listening"]
    viewer.step()
    assert len(viewer.rtl.started) == 1
    later(skyview.RETRY_SECONDS + 1)
    viewer.step()
    assert len(viewer.rtl.started) == 2 and viewer.status()["listening"]


def test_without_the_stick_or_rtl_adsb_nothing_starts(view):
    gone, bare = view(sdr=False), view(tool=False)
    gone.step()
    bare.step()
    assert (gone.status()["state"], bare.status()["state"]) == (adsb.NO_DONGLE, adsb.NO_TOOLS)
    assert gone.rtl.started == [] and bare.rtl.started == []
    assert bare.status()["detail"] == "rtl-sdr is missing. Reinstall Quartermaster"


def test_during_a_wardrive_the_viewer_reads_the_frames_the_wardrive_hears_and_starts_nothing(view, rtl):
    rx = rtl()
    rx._tick()
    drive = SimpleNamespace(aircraft=rx, _sky_waiting=False)
    viewer = view(drive=lambda: drive)
    viewer.step()
    assert viewer.rtl.started == [] and len(rx.rtl.started) == 1 and rx.tap == viewer.take
    assert viewer.status()["shared"] and viewer.status()["detail"] == "Listening with the wardrive"
    heard_by(rx, plane("4840d6", 25.3, -77.4, flight="KLM1023"), plane("a1b2c3", flight="", gs=None))
    assert wait_until(lambda: viewer.step() or len(viewer.seen) == 2)
    rx._tick()
    assert len(rx.sky) == 2 and sorted(viewer.seen) == sorted(rx.sky.planes)
    assert viewer.find("4840d6").name == "KLM1023" and round(viewer.find("4840d6").lat, 3) == 25.3
    viewer.capture_start()
    heard_by(rx, plane("c0ffee", 25.2, -77.6))
    assert wait_until(lambda: viewer.step() or len(viewer.seen) == 3)
    assert viewer.status()["capture"]["frames"] == viewer.frames
    assert viewer.stop() and rx.tap is None and rx.proc is not None and rx.proc.poll() is None
    heard_by(rx, plane("d00d11", 25.2, -77.6))
    rx._tick()
    assert len(rx.sky) == 4 and len(viewer.seen) == 3


def test_a_viewer_that_fails_is_let_go_and_the_wardrive_hears_on(rtl, noted):
    rx = rtl()
    rx._tick()

    def broken(lines, now):
        raise RuntimeError("viewer")
    rx.tap = broken
    heard_by(rx, plane("4840d6", 25.3, -77.4))
    rx._tick()
    assert len(rx.sky) == 1 and rx.tap is None and "Aircraft viewer" in noted.read_text()
    heard_by(rx, plane("a1b2c3", 25.3, -77.4))
    rx._tick()
    assert len(rx.sky) == 2


def test_a_wardrive_that_starts_takes_the_stick_and_one_without_aircraft_shows_none(view, rtl):
    drive = []
    viewer = view(drive=lambda: drive[0] if drive else None)
    viewer.step()
    process = viewer.rtl.processes[0]
    assert process.poll() is None
    drive.append(SimpleNamespace(aircraft=None, _sky_waiting=True))
    viewer.step()
    assert process.signals == [signal.SIGTERM] and viewer.status()["state"] == skyview.STARTING
    drive[0]._sky_waiting = False
    viewer.step()
    assert viewer.status()["detail"] == "This wardrive does not listen for aircraft"
    assert not viewer.status()["listening"] and len(viewer.rtl.started) == 1
    rx = rtl()
    rx._tick()
    drive[0].aircraft = rx
    viewer.step()
    assert rx.tap == viewer.take and viewer.status()["listening"]
    drive.clear()
    viewer.step()
    assert rx.tap is None and len(viewer.rtl.started) == 2 and not viewer.status()["shared"]


def test_where_another_program_reads_the_stick_its_aircraft_are_shown_without_raw_frames(view, tmp_path):
    feed = tmp_path / "readsb" / "aircraft.json"
    sky_json(feed, [plane("4840d6", 25.3, -77.4, flight="KLM1023 ", alt_geom=38600, baro_rate=-640),
                    plane("a1b2c3", flight="", category="A0", squawk=""), plane("zzzzzz"), "junk"], NOW)
    viewer = view(feeds=(feed,))
    viewer.step()
    status = viewer.status()
    assert viewer.rtl.started == [] and status["state"] == adsb.SYSTEM and not status["raw"] and status["listening"]
    found = {row[0].hex: row for row in viewer.planes("near", HERE)}
    assert sorted(found) == ["4840d6", "a1b2c3"]
    klm, km, _ = found["4840d6"]
    assert (klm.name, klm.alt_baro, klm.alt_geom, klm.rate, klm.rssi, klm.category) == ("KLM1023", 32000, 38600,
                                                                                        -640, -18.4, "A3")
    assert km is not None and len(klm.points) == 1 and klm.messages == 80
    bare = found["a1b2c3"][0]
    assert (bare.name, bare.category, bare.squawk, bare.lat) == ("A1B2C3", None, None, None)
    viewer.capture_start()
    viewer._feed_at = 0.0
    sky_json(feed, [plane("4840d6", 25.31, -77.41, seen_pos=0.1)], NOW + 1)
    viewer.clock = lambda: NOW + 1
    viewer.step()
    name = viewer.capture_finish()
    folder = tmp_path / "aircraft"
    assert [path.name for path in folder.iterdir()] == [name + ".track.csv"]
    assert [row["Latitude"] for row in read_track(folder / (name + ".track.csv"))] == ["25.300000", "25.310000"]
    assert not viewer.saved[0].with_name(name + ".frames.avr").exists()


def test_a_position_another_program_still_lists_is_taken_once_however_late_its_file_is_read(view, tmp_path):
    feed = tmp_path / "readsb" / "aircraft.json"
    mono = [5000.0]
    sky_json(feed, [plane("4840d6", 25.3, -77.4, seen=1.0, seen_pos=1.0)], NOW)
    viewer = view(feeds=(feed,), mono=lambda: mono[0])
    viewer.clock = lambda: NOW + mono[0] - 5000.0

    def read(lag, **said):
        """The program wrote its file `lag` seconds before the viewer looks at it now."""
        sky_json(feed, [plane("4840d6", **said)], viewer.clock() - lag)
        viewer.step()
    viewer.step()
    seen = viewer.find("4840d6")
    assert seen.pos_time == pytest.approx(4999.0) and viewer.capture_start() == ""
    for index, lag in enumerate((0.1, 0.3, 0.6, 0.9, 0.95), 1):
        mono[0] = 5000.0 + 2 * index + lag
        age = round(1.0 + 2 * index + (0.04 if index % 2 else -0.04), 1)
        read(lag, lat=25.3, lon=-77.4, seen=age, seen_pos=age)
        assert seen.pos_time == pytest.approx(4999.0) and seen.last == pytest.approx(4999.0, abs=0.11)
    assert viewer.status()["capture"]["points"] == 1 and len(viewer.track("4840d6")) == 1
    mono[0] = 5024.0
    read(4.0, lat=25.31, lon=-77.41, seen=0.2, seen_pos=0.5)
    assert seen.pos_time == pytest.approx(5019.5) and seen.last == pytest.approx(5019.8)
    mono[0] = 5030.5
    read(0.5, lat=25.31, lon=-77.41, seen=0.1, seen_pos=0.1)
    assert seen.pos_time == pytest.approx(5029.9)
    mono[0] = 5033.0
    read(1.0, lat=25.32, lon=-77.42, seen=1.9, seen_pos=1.9)
    assert seen.pos_time == pytest.approx(5030.1) and viewer.status()["capture"]["points"] == 4
    name = viewer.capture_finish()
    rows = read_track(tmp_path / "aircraft" / (name + ".track.csv"))
    assert [row["Latitude"] for row in rows] == ["25.300000", "25.310000", "25.310000", "25.320000"]
    assert [float(row["Time"][17:23]) for row in rows] == pytest.approx([39.0, 59.5, 9.9, 10.1], abs=0.002)


def test_an_aircraft_another_program_hears_again_after_a_long_silence_starts_afresh(view, tmp_path, later):
    feed = tmp_path / "readsb" / "aircraft.json"
    sky_json(feed, [plane("4840d6", 25.3, -77.4, flight="KLM1023", squawk="7700", alt_baro=38000, alt_geom=38600,
                          gs=459.0, track=270.0, baro_rate=-640)], NOW)
    viewer = view(feeds=(feed,))
    viewer.step()
    seen = viewer.find("4840d6")
    assert (seen.name, seen.squawk, seen.alt_baro, seen.rssi, seen.messages) == ("KLM1023", "7700", 38000, -18.4, 80)
    first, placed = seen.first, seen.pos_time
    later(60)
    viewer.clock = lambda: NOW + 60
    sky_json(feed, [{"hex": "4840d6", "seen": 0.2, "messages": 95}], NOW + 60)
    viewer.step()
    assert (seen.name, seen.squawk, seen.alt_baro, seen.gs, seen.track, seen.rssi) == ("KLM1023", "7700", 38000,
                                                                                         459.0, 270.0, -18.4)
    assert seen.messages == 95 and seen.last > first + 59
    later(6 * 3600)
    viewer.clock = lambda: NOW + 60 + 6 * 3600
    sky_json(feed, [{"hex": "4840d6", "seen": 0.1}], NOW + 60 + 6 * 3600)
    viewer.step()
    assert viewer.find("4840d6") is seen and seen.name == "4840D6" and seen.messages == 0
    assert [getattr(seen, key) for key in skyview.SAID + ("rssi",)] == [None] * 12
    assert seen.category == "A3" and seen.first == first and seen.pos_time == placed and len(seen.points) == 1
    assert seen.last > first + 6 * 3600 and viewer.planes("near", HERE)[0][1] is None
    sky_json(feed, [plane("4840d6", flight="KLM99", squawk=None, alt_baro=None, gs=None, track=None, rssi=-30.1,
                          messages=7)], NOW + 62 + 6 * 3600)
    later(2)
    viewer.clock = lambda: NOW + 62 + 6 * 3600
    viewer.step()
    assert (seen.name, seen.squawk, seen.alt_baro, seen.gs, seen.rssi, seen.messages) == ("KLM99", None, None, None,
                                                                                          -30.1, 7)


def test_text_from_another_program_is_only_taken_when_an_aircraft_can_send_it(view, tmp_path):
    feed = tmp_path / "readsb" / "aircraft.json"
    sky_json(feed, [plane("4840d6", 25.3, -77.4, flight='KL,"1\n', squawk="12a4", category="Z9"),
                    plane("a1b2c3", 25.2, -77.5, flight="ÉZY12", squawk="77777", category="A33"),
                    plane("c0ffee", 25.1, -77.6, flight="=1+1", squawk=7700, category=3)], NOW)
    viewer = view(feeds=(feed,))
    viewer.step()
    assert len(viewer.seen) == 3 and viewer.capture_start() == ""
    for seen in viewer.seen.values():
        assert (seen.flight, seen.squawk, seen.category, seen.name) == (None, None, None, seen.hex.upper())
    name = viewer.capture_finish()
    saved = (tmp_path / "aircraft" / (name + ".track.csv")).read_text(encoding="ascii").splitlines()
    assert [line.count(",") for line in saved] == [9] * 4 and all(line.split(",")[2] == "" for line in saved[1:])


def test_the_avr_line_and_the_track_row_have_their_fixed_shape():
    assert skyview.avr_line(KLM, 12_000_000) == "@000000B71B00" + KLM + ";\n"
    assert skyview.avr_line("02E19", -5).startswith("@000000000000")
    assert skyview.frame_of(b"*8d4840d6202cc371c32ce0576098;\r\n")[0] == KLM
    assert skyview.frame_of(b"*8d4840d6202cc371c32ce05760;") is None and skyview.frame_of(b"8d48;") is None
    assert skyview.frame_of(b"*zz4840d6202cc371c32ce0576098;") is None
    row = skyview.track_row(NOW + 0.25, "4840d6", "KLM1023 ",
                            (0.0, 52.2572, 3.91937, 38000, 159.2, 182.9, -832, "1200"))
    assert row.endswith(",4840D6,KLM1023,52.257200,3.919370,38000,159.2,182.9,-832,1200\n") and ".250Z," in row
    assert skyview.track_row(NOW, "abcdef", None, (0.0, 1.5, -2.5, None, None, None, None, None)).endswith(
        ",ABCDEF,,1.500000,-2.500000,,,,,\n")
    assert skyview.moved_row(row, -86400 * 259 - 3) == row.replace("2026-11-18T11:06:40", "2026-03-04T11:06:37")
    header = ",".join(skyview.TRACK_COLUMNS) + "\n"
    assert skyview.moved_row(header, 5) == header and skyview.moved_row("\n", 5) == "\n"


def test_a_capture_started_before_the_first_look_is_not_taken_for_one_left_behind(view, tmp_path):
    viewer = view()
    assert viewer.capture_start() == ""
    viewer.step()
    hear(viewer, plane("4840d6", 25.3, -77.4))
    name = viewer.capture_finish()
    assert name and len(skyview.captures(tmp_path / "aircraft")[0]["files"]) == 2
