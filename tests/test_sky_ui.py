import signal

import pytest

from quartermaster import adsb, skyview
from quartermaster.ui import gfx, screens, sky_screens
from tests.fakes import FIX, NO_FIX, Display, FakeRtlAdsb, SkyHardware, frames_for, plane
from tests.test_adsb import wait_until
from tests.test_ui import hear as drive_hears, placed, press, screen_text, sky_app

SIZES = {0: None, 128: "st7735-128", 240: "st7789-240"}


def with_viewer(app, tmp_path):
    fake = FakeRtlAdsb()
    app.viewer_factory = lambda: skyview.Viewer(
        tmp_path, app.gps, drive=lambda: app.drive if app.wardriving else None, launcher=fake,
        which=lambda name: "/usr/bin/rtl_adsb" if name == "rtl_adsb" else None,
        dongles=lambda: [{"usb_id": "0bda:2838"}],
        feeds=(), out=tmp_path / "view", cpus=lambda: 1, kill=fake.kill, tools=lambda name: None, threads=False)
    app.view_rtl = fake
    return app


def sized(size, monkeypatch, tmp_path):
    if size:
        monkeypatch.setattr(Display, "profile", SIZES[size], raising=False)
        monkeypatch.setattr(Display, "width", size, raising=False)
        monkeypatch.setattr(Display, "height", size, raising=False)
    made = with_viewer(sky_app(tmp_path), tmp_path)
    yield made
    made.home()
    made.view_rtl.close()
    made.rtl.close()


@pytest.fixture(params=[0, 128, 240])
def app(request, monkeypatch, tmp_path):
    yield from sized(request.param, monkeypatch, tmp_path)


@pytest.fixture(params=[128, 240])
def hat(request, monkeypatch, tmp_path):
    yield from sized(request.param, monkeypatch, tmp_path)


def act(app, label, key):
    """Choose something the way the device at hand does: a letter key, or the K3 actions list of a square screen."""
    if not app.square:
        return press(app, key)
    press(app, "TAB")
    labels = [name for name, _ in app.square.actions]
    assert label in labels, labels
    return press(app, *["DOWN"] * labels.index(label), "ENTER")


def sky_hears(app, *planes):
    viewer, fake = app.sky, app.view_rtl
    raw = [line for report in planes for line in frames_for(report)]
    wanted = viewer.frames + len(raw)
    viewer.step()
    fake.lines(*raw)
    assert wait_until(lambda: viewer.step() or viewer.frames >= wanted)


def open_viewer(app):
    press(app, "H")
    act(app, "Aircraft", "A")
    assert isinstance(app.screen, sky_screens.AircraftList)
    app.sky.step()


def test_the_hardware_screen_opens_the_viewer_only_when_the_stick_can_listen(tmp_path, drawn):
    for name, hw, why in (("gone", SkyHardware(sdr=False), "No RTL-SDR stick is plugged in"),
                          ("bare", SkyHardware(decoder=False), "rtl-sdr is missing. Reinstall Quartermaster"),
                          ("busy", SkyHardware(sky_use=adsb.BUSY), "In use by another program")):
        blocked = with_viewer(sky_app(tmp_path / name, hw=hw, adsb=True), tmp_path / name)
        text = screen_text(blocked, drawn) and press(blocked, "H") and screen_text(blocked, drawn)
        assert ("A", "Aircraft") not in blocked.screen.hints(blocked) and "A: viewer" not in text
        press(blocked, "A")
        assert isinstance(blocked.screen, screens.Hardware) and blocked.toast_text == why and blocked.sky is None
        assert blocked.view_rtl.started == []
    for name, hw in (("ready", SkyHardware()), ("system", SkyHardware(decoder=False, sky_use=adsb.SYSTEM))):
        ready = with_viewer(sky_app(tmp_path / name, hw=hw), tmp_path / name)
        press(ready, "H")
        assert ready.screen.hints(ready)[0] == ("A", "Aircraft") and "A: viewer" in screen_text(ready, drawn)
        press(ready, "A")
        assert isinstance(ready.screen, sky_screens.AircraftList) and ready.sky is not None
        press(ready, "ESC")
        assert ready.sky is None and isinstance(ready.screen, screens.Hardware)
        ready.view_rtl.close()


def test_the_viewer_lists_aircraft_and_shows_one_with_its_route(app, drawn):
    open_viewer(app)
    assert len(app.view_rtl.started) == 1
    text = screen_text(app, drawn)
    assert "Listening for aircraft" in text and "AIRCRAFT" in text
    lat, lon = FIX["latitude"], FIX["longitude"]
    sky_hears(app, plane("4840d6", lat + 0.3, lon + 0.3, flight="KLM1023", alt_baro=38000, gs=459.0, track=270.0),
              plane("a1b2c3", lat + 0.05, lon, flight="TST2C3", alt_baro=12000),
              plane("c0ffee", flight="", gs=None, squawk=None))
    text = screen_text(app, drawn)
    assert "TST2C3" in text and "KLM1023" in text and "C0FFEE" in text and app.screen.index == 0
    assert text.index("TST2C3") < text.index("KLM1023") < text.index("C0FFEE")
    if app.square:
        assert "12,000 ft  451 kt" in text and "5.6 km N" in text
    else:
        assert "3 aircraft · nearest first" in text and "38000ft" in text and "459kt" in text and "Listening" in text
        assert "5.6 km N" in text and "km NE" in text and "—" in text
    img = app.compose()
    assert img.size == ((app.display.width, app.display.height) if app.square else (gfx.W, gfx.H))
    press(app, "DOWN")
    assert app.screen.chosen == "4840d6"
    sky_hears(app, plane("d00d11", lat + 0.01, lon, flight="NEAR1"))
    screen_text(app, drawn)
    assert app.screen.index == 2 and app.screen.chosen == "4840d6"
    act(app, "Sort", "TAB")
    assert app.screen.order == "latest" and screen_text(app, drawn) and app.screen.chosen == "4840d6"
    act(app, "Sort", "TAB")
    press(app, "ENTER")
    assert isinstance(app.screen, sky_screens.AircraftDetail) and app.screen.title == "AIRCRAFT KLM1023"
    pages = []
    for _ in range(8):
        pages.append(screen_text(app, drawn).replace("\n", " "))
        press(app, *["DOWN"] * 4)
    text = " ".join(pages)
    for words in ("4840D6", "KLM1023", "1200", "Large aircraft (A3)", "38,000 ft", "459 kt", "270° W", "Level",
                  "From you", "km NE", "First heard", "Last heard", "Messages", "position heard"):
        assert words in text, words
    for label in ("Altitude GNSS", "Signal"):
        assert "%s: Unknown" % label in text or (label in text and "Unknown" in text)
    press(app, "ENTER")
    assert isinstance(app.screen, sky_screens.AircraftRoute) and app.screen.title == "ROUTE KLM1023"
    text = screen_text(app, drawn)
    assert "Rings 25 / 50 km" in text and "N" in text
    assert app.compose().getcolors(100000) is not None
    press(app, "DOWN", "UP", "ESC", "ESC")
    assert isinstance(app.screen, sky_screens.AircraftList)
    press(app, "DOWN", "ENTER")
    assert app.screen.title == "AIRCRAFT C0FFEE"
    text = " ".join(screen_text(app, drawn).replace("\n", " ") for _ in press(app, "UP") and range(1))
    assert "Unknown" in text and "C0FFEE" in text
    press(app, "ENTER")
    assert "No position heard yet" in screen_text(app, drawn).replace(".", "")
    process = app.view_rtl.processes[0]
    press(app, "ESC", "ESC", "ESC")
    assert isinstance(app.screen, screens.Hardware) and app.sky is None and process.signals == [signal.SIGTERM]


def test_the_route_is_drawn_from_the_positions_heard_with_the_user_in_the_middle(app, monkeypatch, drawn):
    monkeypatch.setattr(skyview, "POINT_SECONDS", 0.0)
    open_viewer(app)
    lat, lon = FIX["latitude"], FIX["longitude"]
    for index in range(8):
        sky_hears(app, plane("4840d6", lat + 0.02 + index * 0.01, lon - 0.05 + index * 0.02, flight="KLM1023"))
    assert len(app.sky.track("4840d6")) >= 6
    press(app, "ENTER", "ENTER")
    picture = app.compose()
    colours = {colour for _, colour in picture.getcolors(100000)}
    assert gfx.GOLD in colours and gfx.GREEN in colours
    text = screen_text(app, drawn)
    assert "Rings 10 / 20 km" in text and "no GPS" not in text
    if app.square:
        assert "↓ positions · K2 back" in text and "32,000 ft" not in text
        press(app, "DOWN")
        text = screen_text(app, drawn)
        assert "Rings" not in text and "↕ scroll · K2 back" in text and app.screen.top == 1
        press(app, *["DOWN"] * 20)
        assert screen_text(app, drawn) and 1 <= app.screen.top <= len(app.sky.track("4840d6"))
        press(app, *["UP"] * 20)
        assert "Rings 10 / 20 km" in screen_text(app, drawn) and app.screen.top == 0
        press(app, "DOWN")
        text = screen_text(app, drawn)
    assert "%d positions" % len(app.sky.track("4840d6")) in text
    assert ("32,000 ft" if app.square else "32000ft") in text and text.count(" km") >= 3
    app.gps.fix = NO_FIX
    if app.square:
        text = screen_text(app, drawn)
        assert " km" not in text and "—" in text and "32,000 ft" in text
        press(app, "UP")
    text = screen_text(app, drawn)
    assert ("no GPS" in text) if app.square else ("No GPS fix: the track is centred." in text)
    assert gfx.GREEN not in {colour for _, colour in app.compose().getcolors(100000)} or not app.square


def test_one_aircraft_says_how_many_positions_were_heard_also_when_the_route_keeps_fewer(app, drawn):
    open_viewer(app)
    lat, lon = FIX["latitude"], FIX["longitude"]
    for index in range(4):
        sky_hears(app, plane("4840d6", lat + 0.02 + index * 0.001, lon, flight="KLM1023"))
    seen = app.sky.find("4840d6")
    heard, kept = seen.heard, len(app.sky.track("4840d6"))
    assert heard >= kept >= 1
    for _ in range(5):
        seen.place(seen.newest)
    assert seen.heard == heard + 5 and len(app.sky.track("4840d6")) == kept
    press(app, "ENTER")
    rows = {label: value for label, value, *_ in app.screen.rows(app)}
    assert rows["Route"] == "%d positions heard" % (heard + 5)
    press(app, "ENTER")
    text = screen_text(app, drawn)
    if app.square:
        press(app, "DOWN")
        text = screen_text(app, drawn)
    assert ("1 position" if kept == 1 else "%d positions" % kept) in text


def test_a_route_over_the_180th_meridian_is_drawn_in_one_piece():
    here = (-17.0, 179.95)
    points = [(float(index), -17.0, (179.9 + index * 0.02 + 180) % 360 - 180, 30000, 400.0, 90.0, 0, None)
              for index in range(11)]
    assert points[0][2] > 179 and points[-1][2] < -179
    for middle in (here, None):
        canvas = gfx.Canvas()
        assert sky_screens.draw_route(canvas, (4, 20, 134, 150), [points], middle, gfx.font("mono", 8)) == 20
    assert sky_screens.turned(359.0) == -1.0 and sky_screens.turned(-359.0) == 1.0 and sky_screens.turned(10) == 10


def gold_lines(parts, here=(25.0, -77.0)):
    """How many positions each gold line of a route joins, and the single positions drawn as a dot."""
    canvas, lines, dots = gfx.Canvas(), [], []
    line, point = canvas.draw.line, canvas.draw.point
    canvas.draw.line = lambda xy, **how: lines.append((len(xy), how.get("fill"))) or line(xy, **how)
    canvas.draw.point = lambda xy, **how: dots.append(how.get("fill")) or point(xy, **how)
    sky_screens.draw_route(canvas, (4, 20, 134, 150), parts, here, gfx.font("mono", 8))
    return [count for count, fill in lines if fill == gfx.GOLD], dots.count(gfx.GOLD)


def flight(times):
    seen = skyview.Seen("4840d6", 0.0)
    for index, when in enumerate(times):
        seen.place((float(when), 25.0 + 0.0001 * index, -77.0, 30000, 400.0, 0.0, 0, None))
    return seen


def test_no_line_is_drawn_over_a_time_in_which_the_aircraft_was_not_heard():
    seen = flight((0, 10, 20, 500, 510, 900))
    assert [len(part) for part in seen.parts()] == [3, 2, 1]
    assert gold_lines(seen.parts()) == ([3, 2], 1)
    assert gold_lines(flight((0, 10, 20)).parts(), None) == ([3], 0)
    assert gold_lines([]) == ([], 0)


def test_a_track_heard_for_hours_without_a_silence_is_drawn_as_one_line():
    """The positions kept grow further apart as the track is thinned out. That is not a silence."""
    for hours in (4.5, 9):
        seen = flight(range(0, int(hours * 3600), 2))
        assert seen.gap > skyview.SILENCE_SECONDS / 2 and len(seen.route()) <= skyview.TRACK_POINTS + 1
        assert gold_lines(seen.parts()) == ([len(seen.route())], 0)
    assert seen.gap > skyview.SILENCE_SECONDS
    seen.place((9 * 3600.0 + skyview.SILENCE_SECONDS, 27.0, -77.0, 30000, 400.0, 0.0, 0, None))
    assert gold_lines(seen.parts()) == ([len(seen.route()) - 1], 1)


def test_a_wardrive_whose_receiver_only_reports_its_status_still_opens_the_viewer(tmp_path, drawn):
    """The Hardware screen asks a receiver for its status, which is all a stand-in for one has to give."""
    class Listening:
        count = 23

        def __init__(self, *args, **kwargs):
            pass

        def start(self, epoch):
            pass

        def stop(self):
            pass

        def finish(self):
            return None

        def status(self):
            return {"state": adsb.RUNNING, "aircraft": 23, "detail": adsb.WORDS[adsb.RUNNING]}
    app = with_viewer(sky_app(tmp_path, adsb=True), tmp_path)
    made = []

    def wardrive(cfg, gps):
        from quartermaster.engine import Wardrive
        from tests.fakes import ADAPTERS, Fleet, NoBle
        made.append(Wardrive(cfg, gps, root=tmp_path, adapters=ADAPTERS, ble=NoBle(), fleet=Fleet,
                             clock=lambda: 1795000000, receiver=Listening, sdr=lambda: [{"usb_id": "0bda:2838"}]))
        return made[-1]
    app.wardrive_factory = wardrive
    press(app, "W", "ESC", "H")
    assert isinstance(made[0].aircraft, Listening)
    assert screens.sky_ready(app) == (True, adsb.WORDS[adsb.RUNNING])
    assert "23 aircraft · A: viewer" in screen_text(app, drawn) and ("A", "Aircraft") in app.screen.hints(app)
    app.stop_wardrive()


def test_a_capture_is_saved_from_the_viewer_and_listed_and_deleted(app, tmp_path, drawn):
    open_viewer(app)
    folder = tmp_path / "aircraft"
    act(app, "Save capture", "S")
    act(app, "End capture", "S")
    assert app.toast_text == "Nothing was heard, so nothing was saved" and list(folder.iterdir()) == []
    sky_hears(app, plane("4840d6", 25.3, -77.4, flight="KLM1023"))
    act(app, "Save capture", "S")
    assert app.sky.capture is not None and app.toast_text == "Capture started. It saves until you end it or go back."
    assert ("S", "End capture") in app.screen.hints(app)
    sky_hears(app, plane("a1b2c3", 25.2, -77.5))
    text = screen_text(app, drawn)
    assert "REC" in text
    act(app, "End capture", "S")
    assert app.sky.capture is None and app.toast_text == "Capture saved in quartermaster/aircraft"
    assert sorted(path.suffix for path in folder.iterdir()) == [".avr", ".csv"]
    act(app, "Captures", "L")
    assert isinstance(app.screen, sky_screens.Captures)
    text = screen_text(app, drawn)
    assert "track + frames" in text and "KB" in text
    act(app, "Delete", "D")
    assert isinstance(app.screen, screens.Confirm) and app.screen.title == "Delete this capture?"
    press(app, "ENTER")
    assert len(list(folder.iterdir())) == 2
    act(app, "Delete", "D")
    press(app, "RIGHT", "ENTER")
    assert list(folder.iterdir()) == [] and app.toast_text == "Capture deleted"
    assert "No captures saved yet" in screen_text(app, drawn).replace("\n", " ")
    press(app, "ESC")
    act(app, "Save capture", "S")
    sky_hears(app, plane("d00d11", 25.2, -77.5))
    press(app, "ESC")
    assert app.sky is None and app.toast_text == "Capture saved in quartermaster/aircraft"
    assert len(list(folder.iterdir())) == 2


def test_a_capture_that_has_heard_nothing_yet_shows_that_it_runs(app, monkeypatch):
    open_viewer(app)
    assert not any("REC" in text for text in placed(app, monkeypatch))
    act(app, "Save capture", "S")
    assert app.sky.capture is not None
    app.toast_until = 0
    found = placed(app, monkeypatch)
    mark = next(text for text in found if text.startswith("REC"))
    assert found[mark][1] == gfx.RED and not app.sky.seen
    if app.square:
        row = app.square.small.size + 3
        assert mark == "REC" and found[mark][0] == app.square.bottom - row
        above = [height for text, (height, _) in found.items() if app.square.top <= height < found[mark][0]]
        assert len(above) >= 3 and max(above) + row <= found[mark][0]
    act(app, "End capture", "S")
    assert not any("REC" in text for text in placed(app, monkeypatch))


def test_the_rings_of_a_square_screen_are_always_told_in_full_also_without_a_gps_fix(hat, drawn):
    open_viewer(hat)
    viewer, lat, lon = hat.sky, FIX["latitude"], FIX["longitude"]
    viewer.seen["4840d6"] = skyview.Seen("4840d6", viewer.mono())
    hat.push(sky_screens.AircraftRoute(viewer, "4840d6"))
    for ring in sky_screens.RINGS:
        seen = viewer.seen["4840d6"] = skyview.Seen("4840d6", viewer.mono())
        for index, side in enumerate((-1, 1)):
            seen.place((viewer.mono() + 2 * index, lat + side * 0.8 * ring / 110.57, lon, 30000, 400.0, 0.0, 0, None))
        rings = "%g / %d km" % (ring / 2, ring)
        hat.gps.fix = FIX
        assert "Rings " + rings in screen_text(hat, drawn).split("\n")
        hat.gps.fix = NO_FIX
        label = next(text for text in screen_text(hat, drawn).split("\n") if "no GPS" in text)
        assert label in ("Rings " + rings + " · no GPS", rings + " · no GPS"), label
        assert gfx.Canvas().text_width(label, hat.square.small) <= hat.square.size - 8
    assert label == ("Rings 500 / 1000 km · no GPS" if hat.square.large else "500 / 1000 km · no GPS")


def test_during_a_wardrive_the_viewer_shares_the_wardrives_receiver(app, drawn):
    press(app, "ENTER" if app.square else "W")
    assert app.wardriving
    drive_hears(app, plane("a1b2c3", 25.2, -77.7))
    press(app, "ESC", "H")
    assert ("A", "Aircraft") in app.screen.hints(app)
    act(app, "Aircraft", "A")
    app.sky.step()
    assert app.view_rtl.started == [] and len(app.rtl.started) == 1 and app.drive.aircraft.tap == app.sky.take
    app.rtl.air(plane("4840d6", 25.3, -77.4, flight="KLM1023"))
    assert wait_until(lambda: app.sky.step() or "4840d6" in app.sky.seen)
    assert "KLM1023" in screen_text(app, drawn)
    assert wait_until(lambda: app.drive.status()["aircraft"] == 2)
    receiver = app.drive.aircraft
    press(app, "ESC")
    assert receiver.tap is None and app.wardriving and receiver.proc.poll() is None
    app.stop_wardrive()


def test_starting_a_wardrive_closes_the_viewer_and_frees_the_stick_first(tmp_path):
    app = with_viewer(sky_app(tmp_path), tmp_path)
    press(app, "H", "A")
    app.sky.step()
    process = app.view_rtl.processes[0]
    order = []
    receive = process.receive
    process.receive = lambda sig: order.append("viewer ended") or receive(sig)
    factory = app.wardrive_factory
    app.wardrive_factory = lambda cfg, gps: order.append("wardrive made") or factory(cfg, gps)
    assert app.start_wardrive()
    assert order == ["viewer ended", "wardrive made"] and app.sky is None
    assert isinstance(app.screen, screens.Wardrive) and len(app.stack) == 2
    app.stop_wardrive()
    app.view_rtl.close()
    app.rtl.close()


def test_a_wardrive_that_does_not_listen_for_aircraft_keeps_the_viewer_shut(tmp_path):
    app = with_viewer(sky_app(tmp_path, adsb=False), tmp_path)
    press(app, "W", "ESC", "H", "A")
    assert isinstance(app.screen, screens.Hardware) and app.toast_text == "This wardrive does not listen for aircraft"
    assert app.view_rtl.started == []
    app.stop_wardrive()


def test_a_capture_that_ends_by_itself_is_said_once(tmp_path, drawn, monkeypatch):
    app = with_viewer(sky_app(tmp_path), tmp_path)
    monkeypatch.setattr(skyview, "CAPTURE_BYTES", 100)
    press(app, "H", "A", "S")
    sky_hears(app, plane("4840d6", 25.3, -77.4))
    app.toast_text = ""
    screen_text(app, drawn)
    assert app.toast_text == skyview.LARGE
    app.toast_text = ""
    screen_text(app, drawn)
    assert app.toast_text == ""
    press(app, "ESC")
    app.view_rtl.close()


def test_the_rows_of_an_aircraft_say_unknown_for_what_was_not_heard():
    seen = skyview.Seen("abcdef", 100.0)
    rows = dict(sky_screens.detail_rows(seen, None, 130.0, 0))
    assert rows["ICAO"] == "ABCDEF" and rows["First heard"] == "30s ago" and rows["Route"] == "No position heard yet"
    for label in ("Callsign", "Squawk", "Category", "Altitude baro", "Altitude GNSS", "Ground speed", "Heading",
                  "Vertical rate", "Position", "Messages", "Signal"):
        assert rows[label] == "Unknown", label
    assert "Airspeed" not in rows and "From you" not in rows
    seen.lat, seen.lon, seen.pos_time = 52.2572, 3.91937, 100.0
    seen.squawk, seen.tas, seen.mag_heading, seen.baro_rate = "7700", 375, 244.0, -2304
    seen.rssi, seen.category = -18.4, "B6"
    rows = dict(sky_screens.detail_rows(seen, (52.0, 4.0), 130.0, 3))
    assert rows["Position"] == "52.25720, 3.91937 (30s ago)" and rows["From you"].endswith("km N")
    assert rows["Squawk"] == "7700 (emergency)" and rows["Airspeed"] == "375 kt true"
    assert rows["Heading"] == "244° magnetic" and rows["Vertical rate"] == "-2304 ft/min"
    assert rows["Signal"] == "-18.4 dBFS" and rows["Category"] == "Drone (B6)" and rows["Route"] == "3 positions heard"
    assert dict(sky_screens.detail_rows(seen, None, 130.0, 3))["From you"] == "Unknown: no GPS fix"
    still = skyview.Seen("abcdef", 100.0)
    still.gs = 0.0
    rows = dict(sky_screens.detail_rows(still, None, 130.0, 0))
    assert rows["Ground speed"] == "0 kt" and rows["Heading"] == "Unknown"
    assert sky_screens.ring_km(0.2) == 1 and sky_screens.ring_km(37) == 50 and sky_screens.ring_km(9999) == 1000
    assert sky_screens.far(4.25, 10) == "4.2 km N" and sky_screens.far(42.4) == "42 km" and sky_screens.far(None) == "—"
