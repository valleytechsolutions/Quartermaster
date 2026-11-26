import contextlib
from pathlib import Path
import shutil
import time

import pytest

from quartermaster import power
from quartermaster.ui import app as app_module, gfx, screens
from quartermaster.ui.gfx import AMBER, GREEN, MUTED, RED, TEXT, W, Canvas, font
from tests.fakes import ADAPTERS, FIX, NO_FIX, Hardware, networks
from tests.test_ui import KEYS, LOST, losing_rows, make_app, placed, press, screen_text


def supply(root, name, **files):
    folder = root / name
    folder.mkdir(parents=True)
    for file, content in files.items():
        (folder / file).write_text(content)
    return folder


def test_the_battery_is_read_the_way_the_system_reports_it(tmp_path):
    gauge = supply(tmp_path, "bq27220-0", type="Battery\n", capacity="81\n", status="Charging\n", present="1\n",
                   charge_now="1051000\n", charge_full="1685000\n", current_now="135000\n")
    assert power.battery(tmp_path) == {"percent": 81, "charging": True}
    for status in ("Discharging", "Not charging", "Full", "Unknown", "charging", ""):
        (gauge / "status").write_text(status + "\n")
        assert power.battery(tmp_path) == {"percent": 81, "charging": False}
    (gauge / "status").unlink()
    for level in ("0", "7", "100", "050"):
        (gauge / "capacity").write_text(level)
        assert power.battery(tmp_path) == {"percent": int(level), "charging": False}


def test_a_battery_that_cannot_be_read_is_no_battery(tmp_path):
    assert power.battery(tmp_path / "missing") is None and power.battery(tmp_path) is None
    (tmp_path / "stray").write_text("Battery")
    supply(tmp_path, "ac", type="Mains\n", online="1\n", capacity="55\n")
    supply(tmp_path, "usb", type="USB\n", capacity="55\n")
    assert power.battery(tmp_path) is None
    gauge = supply(tmp_path, "bq27220-0", type="Battery\n", status="Charging\n")
    assert power.battery(tmp_path) is None
    for level in ("", "\n", "-5", "101", "1000", "1e2", "inf", "nan", "8 1", "81%", "0x51", "²", "٣", "８１"):
        (gauge / "capacity").write_text(level, encoding="utf-8")
        assert power.battery(tmp_path) is None, level
    (gauge / "capacity").write_bytes(b"\xff\xfe81\n")
    assert power.battery(tmp_path) is None
    (gauge / "capacity").unlink()
    (gauge / "capacity").mkdir()
    assert power.battery(tmp_path) is None
    (gauge / "capacity").rmdir()
    (gauge / "capacity").write_text("81\n")
    (gauge / "present").write_text("0\n")
    assert power.battery(tmp_path) is None
    (gauge / "present").write_text("1\n")
    (gauge / "status").write_bytes(b"\xff\xfeCharging\n")
    assert power.battery(tmp_path) == {"percent": 81, "charging": False}


@contextlib.contextmanager
def unreadable(monkeypatch, path, error):
    """A read of this file fails, in one of the ways a read of the fuel gauge can."""
    real = Path.read_text

    def read_text(self, *args, **kwargs):
        if self == path:
            raise error
        return real(self, *args, **kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(Path, "read_text", read_text)
        yield


def test_a_reading_with_a_part_that_could_not_be_read_is_no_reading(tmp_path, monkeypatch):
    gauge = supply(tmp_path, "bq27220-0", type="Battery\n", capacity="15\n", status="Charging\n", present="1\n")
    again = TypeError("can't concat NoneType to bytes")
    for file in ("capacity", "status", "present"):
        for error in (OSError(5, "Input/output error"), OSError(121, "Remote I/O error"), again):
            with unreadable(monkeypatch, gauge / file, error):
                assert power.battery(tmp_path) is None, (file, error)
            assert power.battery(tmp_path) == {"percent": 15, "charging": True}
    (gauge / "status").unlink()
    (gauge / "status").mkdir()
    assert power.battery(tmp_path) is None
    (gauge / "status").rmdir()
    with unreadable(monkeypatch, gauge / "type", again):
        assert power.battery(tmp_path) is None
    with unreadable(monkeypatch, gauge / "scope", again):
        assert power.battery(tmp_path) == {"percent": 15, "charging": False}


def test_a_missed_read_of_the_first_battery_does_not_show_the_second(tmp_path, monkeypatch):
    first = supply(tmp_path, "BAT0", type="Battery\n", capacity="60\n", status="Discharging\n", present="1\n")
    supply(tmp_path, "BAT1", type="Battery\n", capacity="2\n", status="Discharging\n", present="1\n")
    assert power.battery(tmp_path) == {"percent": 60, "charging": False}
    with unreadable(monkeypatch, first / "capacity", OSError(5, "Input/output error")):
        assert power.battery(tmp_path) is None
    (first / "present").write_text("0\n")
    assert power.battery(tmp_path) == {"percent": 2, "charging": False}
    (first / "present").write_text("1\n")
    (first / "capacity").unlink()
    assert power.battery(tmp_path) == {"percent": 2, "charging": False}


def test_the_battery_of_a_wireless_keyboard_is_not_the_devices_own(tmp_path, monkeypatch):
    mouse = supply(tmp_path, "hidpp_battery_0", type="Battery\n", scope="Device\n", capacity="12\n",
                   status="Discharging\n")
    assert power.battery(tmp_path) is None
    mains = supply(tmp_path, "ac", type="Mains\n", online="1\n")
    supply(tmp_path, "zz-gauge", type="Battery\n", capacity="64\n", status="Discharging\n")
    assert power.battery(tmp_path) == {"percent": 64, "charging": False}
    for path in (mouse / "capacity", mouse / "status", mouse / "present", mains / "type", mains / "capacity"):
        with unreadable(monkeypatch, path, OSError(5, "Input/output error")):
            assert power.battery(tmp_path) == {"percent": 64, "charging": False}, path
    supply(tmp_path, "bq27220-0", type="Battery\n", scope="System\n", capacity="81\n", status="Charging\n")
    assert power.battery(tmp_path) == {"percent": 81, "charging": True}


def quiet_hardware(monkeypatch):
    for name in ("wifi_adapters", "bluetooth_adapters", "gps_candidates", "stalled_usb_adapters"):
        monkeypatch.setattr(app_module.hardware, name, lambda: [])


def test_the_hardware_watch_keeps_the_battery_level_fresh(monkeypatch, later):
    levels = iter([None, {"percent": 50, "charging": False}, None, {"percent": 0, "charging": True}, None, None])
    monkeypatch.setattr(power, "battery", lambda: next(levels))
    quiet_hardware(monkeypatch)
    watch = app_module.HardwareWatch()
    assert watch.battery is None
    seen = []
    for _ in range(4):
        watch.refresh()
        seen.append(watch.battery)
    assert seen == [None, {"percent": 50, "charging": False}, {"percent": 50, "charging": False},
                    {"percent": 0, "charging": True}]
    later(59)
    watch.refresh()
    assert watch.battery == {"percent": 0, "charging": True}
    later(2)
    watch.refresh()
    assert watch.battery is None

    def unplugged():
        raise OSError("gone")
    monkeypatch.setattr(app_module.hardware, "wifi_adapters", unplugged)
    monkeypatch.setattr(power, "battery", lambda: {"percent": 48, "charging": False})
    watch.refresh()
    assert watch.battery == {"percent": 48, "charging": False}


def test_the_hardware_watch_reads_at_the_start_and_then_keeps_reading(monkeypatch):
    reads = []

    def read():
        reads.append(len(reads))
        return {"percent": 50, "charging": False}
    monkeypatch.setattr(power, "battery", read)
    quiet_hardware(monkeypatch)
    watch = app_module.HardwareWatch(interval=0.01)
    watch.start()
    assert watch.battery == {"percent": 50, "charging": False}
    deadline = time.monotonic() + 5
    while len(reads) < 4 and time.monotonic() < deadline:
        time.sleep(0.01)
    watch.stop()
    assert len(reads) >= 4


def test_a_check_that_fails_is_noted_and_the_next_one_still_runs(monkeypatch, noted):
    reads = []

    def read():
        reads.append(len(reads))
        if len(reads) in (1, 3):
            raise ValueError("no gauge")
        return {"percent": 50, "charging": False}
    monkeypatch.setattr(power, "battery", read)
    quiet_hardware(monkeypatch)
    assert app_module.HardwareWatch().interval == 5
    watch = app_module.HardwareWatch(interval=0.01)
    watch.start()
    assert reads == [0] or watch.battery
    deadline = time.monotonic() + 5
    while len(reads) < 5 and time.monotonic() < deadline:
        time.sleep(0.01)
    watch.stop()
    watch._thread.join(5)
    assert len(reads) >= 5 and watch.battery == {"percent": 50, "charging": False} and not watch._thread.is_alive()
    assert noted.read_text().count("Hardware check") == 1 and "ValueError: no gauge" in noted.read_text()


def test_the_watch_runs_for_as_long_as_the_app_and_recon_stops_with_it(tmp_path):
    class Watched(Hardware):
        started = stopped = 0

        def start(self):
            self.started += 1

        def stop(self):
            self.stopped += 1
    app = make_app(tmp_path, keys=KEYS, hw=Watched())
    press(app, "R", "SPACE")
    assert app.recon_running and (app.hw.started, app.hw.stopped) == (0, 0)
    seen = []

    def show(image):
        seen.append((app.hw.started, app.hw.stopped, app.recon_running))
        app.quit()
    app.display.show = show
    app.run()
    assert seen == [(1, 0, True)] and (app.hw.started, app.hw.stopped) == (1, 1) and not app.recon_running


def bar(app):
    """The status bar as drawn: the picture and every text in it."""
    picture = app.compose()
    return picture, picture.crop((0, 0, W, gfx.STATUS_H))


def fill(picture, percent_of_width):
    """The colour inside the battery at a point along its 16 pixels of charge."""
    return picture.getpixel((W - 5 - 22 + 2 + min(15, int(16 * percent_of_width / 100)), 8))


def test_every_screen_shows_the_battery_at_the_top(tmp_path, drawn):
    app = make_app(tmp_path, keys=KEYS)
    assert "87%" in screen_text(app, drawn)
    for letter in "LUHSKA":
        press(app, letter)
        assert "87%" in screen_text(app, drawn), letter
        press(app, "ESC")
    press(app, "R")
    assert "87%" in screen_text(app, drawn)
    press(app, "ESC", "W")
    assert app.wardriving and "87%" in screen_text(app, drawn)
    picture, _ = bar(app)
    assert fill(picture, 0) == TEXT and fill(picture, 80) == TEXT and fill(picture, 95) == gfx.SURFACE
    assert picture.getpixel((W - 5 - 22, 8)) == MUTED and picture.getpixel((W - 5 - 1, 8)) == MUTED


def test_the_gauge_turns_amber_then_red_and_green_while_charging(tmp_path, drawn, monkeypatch):
    app = make_app(tmp_path)
    for level, color in ((100, TEXT), (21, TEXT), (20, AMBER), (11, AMBER), (10, RED), (1, RED)):
        app.hw.battery = {"percent": level, "charging": False}
        assert "%d%%" % level in screen_text(app, drawn)
        picture, _ = bar(app)
        assert fill(picture, 0) == color, level
        assert picture.getpixel((W - 5 - 22, 8)) == (MUTED if color == TEXT else color)
    app.hw.battery = {"percent": 100, "charging": False}
    assert fill(bar(app)[0], 99) == TEXT
    app.hw.battery = {"percent": 50, "charging": False}
    picture, _ = bar(app)
    assert fill(picture, 45) == TEXT and fill(picture, 55) == gfx.SURFACE
    app.hw.battery = {"percent": 0, "charging": False}
    assert "0%" in screen_text(app, drawn) and fill(bar(app)[0], 0) == gfx.SURFACE
    bolts = []
    monkeypatch.setattr(app_module.icons, "draw", lambda canvas, name, x, y, size, color:
                        bolts.append(color) if name == "bolt" else None)
    bar(app)
    assert bolts == []
    for level in (100, 15, 3):
        app.hw.battery = {"percent": level, "charging": True}
        picture, _ = bar(app)
        assert fill(picture, 0) == GREEN and picture.getpixel((W - 5 - 22, 8)) == GREEN
        assert bolts.pop() == GREEN and bolts == []


def test_the_tips_say_what_happens_when_the_battery_is_almost_empty(tmp_path, drawn):
    app = make_app(tmp_path)
    press(app, "A", "T")
    pages = []
    for _ in range(4):
        pages.append(screen_text(app, drawn).replace("\n", " "))
        press(app, "PAGEDOWN")
    assert "almost empty, a running wardrive is saved and Quartermaster closes" in " ".join(pages)


def test_a_device_without_a_battery_shows_no_gauge(tmp_path, drawn):
    app = make_app(tmp_path, hw=Hardware(battery=None))
    text = screen_text(app, drawn)
    assert "%" not in text and "GPS 14" in text
    small = font("monob", 9)
    assert app.indicators(Canvas())[0][0] == int(Canvas().text_width("GPS 14", small))


def bar_text(app, drawn, monkeypatch):
    """What the status bar says, and whether it shows the bolt."""
    bolts = []
    with monkeypatch.context() as patch:
        patch.setattr(app_module.icons, "draw", lambda canvas, name, x, y, size, color:
                      bolts.append(name) if name == "bolt" else None)
        text = screen_text(app, drawn).split("\n")
    return text, bool(bolts)


def test_where_room_is_short_the_bolt_and_the_number_make_way_and_never_the_title(tmp_path, drawn, monkeypatch):
    app = make_app(tmp_path, keys=KEYS)
    press(app, "W", "ESC")
    assert isinstance(app.screen, screens.Home) and app.wardriving

    def state(title, waiting, level, charging):
        app.screen.title = title
        app.upload_status = lambda: {"uploading": False, "waiting": waiting, "keys": 2, "files": []}
        app.hw.battery = {"percent": level, "charging": charging}
        return bar_text(app, drawn, monkeypatch)
    text, bolt = state("QUARTERMASTER", 0, 100, True)
    assert "QUARTERMASTER" in text and "100%" in text and bolt
    text, bolt = state("QUARTERMASTER", 7, 100, True)
    assert "QUARTERMASTER" in text and "100%" in text and "7" in text and "GPS 14" in text and not bolt
    canvas, title = Canvas(), font("bold", 11)
    widest = int(canvas.text_width("100%", font("monob", 9))) + 3

    def room():
        others = sum(width + 8 for width, _ in app.indicators(canvas))
        return W - 29 - others - canvas.text_width("QUARTERMASTER", title)
    app.gps.fix = dict(app.gps.fix, satellites=9)
    text, bolt = state("QUARTERMASTER", 7, 100, True)
    assert 60 < room() < 70 and "GPS 9" in text and "100%" in text and bolt == (room() >= 41 + widest)
    text, bolt = state("QUARTERMASTER", 150, 100, False)
    assert 50 < room() < 57 and "QUARTERMASTER" in text and ("100%" in text) == (room() >= 30 + widest)
    app.gps.fix = dict(app.gps.fix, satellites=14)
    for level in (100, 99, 9):
        text, bolt = state("QUARTERMASTER", 15, level, False)
        assert "QUARTERMASTER" in text and "%d%%" % level in text
        text, bolt = state("QUARTERMASTER", 150, level, True)
        assert "QUARTERMASTER" in text and "%d%%" % level not in text and bolt
        text, bolt = state("IGNORED NETWORKS", 150, level, True)
        assert "IGNORED NETWORKS" in text and "%d%%" % level not in text and not bolt
    text, bolt = state("IGNORED NETWORKS AND MORE", 150, 100, True)
    shortened = [line for line in text if line.endswith("…")]
    assert len(shortened) == 1 and shortened[0].startswith("IGNORED NETW") and "100%" not in text and not bolt
    left = W - 5 - sum(width + 8 for width, _ in app.indicators(canvas)) - 22
    assert left - 4 - 28 - canvas.text_width("…", title) < canvas.text_width(shortened[0], title) <= left - 4 - 28
    app.hw.battery = None
    app.screen.title = "IGNORED NETWORKS AND MORE AND MORE"
    text = screen_text(app, drawn).split("\n")
    assert [line for line in text if line.endswith("…")][0].startswith("IGNORED NETWORKS A")


def marks(app, monkeypatch):
    """The words and the icons of the status bar, in the order they are drawn."""
    words, icons, put = [], [], gfx.Canvas.put

    def record(canvas, xy, text, *args, **kwargs):
        if xy[1] < gfx.STATUS_H:
            words.append(text)
        return put(canvas, xy, text, *args, **kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(gfx.Canvas, "put", record)
        patch.setattr(app_module.icons, "draw", lambda canvas, name, x, y, size, color:
                      icons.append(name) if y < gfx.STATUS_H else None)
        app.compose()
    return words, icons


def test_the_marks_beside_the_battery_show_what_is_going_on_and_no_more(tmp_path, monkeypatch):
    app = make_app(tmp_path)
    press(app, "A")

    def bar(waiting=0, uploading=False, keys=0, **fix):
        app.gps.fix = fix
        app.upload_status = lambda: {"uploading": uploading, "waiting": waiting, "keys": keys, "files": []}
        return marks(app, monkeypatch)
    assert bar(source="none") == (["87%", "NO GPS", "ABOUT"], [])
    assert bar(source="usb") == (["87%", "GPS --", "ABOUT"], [])
    assert bar(3, valid=True, satellites=0) == (["87%", "GPS OK", "ABOUT"], [])
    assert bar(3, keys=1, valid=True, satellites=11) == (["87%", "GPS 11", "3", "ABOUT"], ["cloud"])
    assert bar(uploading=True, valid=True) == (["87%", "GPS OK", "0", "ABOUT"], ["cloud"])
    app.gps.fix = dict(FIX)
    press(app, "ESC", "R", "SPACE")
    assert bar(**FIX) == (["87%", "GPS 14", "", "RECON"], ["eye"])
    press(app, "ESC", "W", "ENTER")
    app.hw.battery = dict(app.hw.battery, charging=True)
    words, icons = bar(**FIX)
    assert words[:2] + words[3:] == ["87%", "GPS 14", "WARDRIVE"] and words[2].startswith("00:00:0")
    assert icons == ["bolt"]


def discharge(app, level, charging=False):
    app.toast_text = ""
    app.hw.battery = {"percent": level, "charging": charging}
    app.step(0)
    return app.toast_text


def test_a_battery_running_down_is_announced_once_per_level(tmp_path):
    app = make_app(tmp_path, keys=KEYS)
    assert [discharge(app, level) for level in (87, 50, 25, 21)] == [""] * 4
    assert discharge(app, 20) == "Battery at 20%. Plug in to keep going." and app.toast_color == AMBER
    assert [discharge(app, level) for level in (19, 20, 21, 22, 24, 20, 11)] == [""] * 7
    assert discharge(app, 10) == "Battery at 10%. Plug in soon." and app.toast_color == RED
    assert [discharge(app, level) for level in (9, 12, 4, 24, 4)] == [""] * 5
    assert discharge(app, 25) == "" and discharge(app, 19) == "Battery at 19%. Plug in to keep going."
    assert discharge(app, 8) == "Battery at 8%. Plug in soon."
    app.hw.battery = None
    app.step(0)
    assert discharge(app, 7) == ""


def test_a_charger_that_comes_and_goes_does_not_repeat_the_notice(tmp_path):
    app = make_app(tmp_path, keys=KEYS)
    assert discharge(app, 30) == "" and discharge(app, 18) == "Battery at 18%. Plug in to keep going."
    for _ in range(3):
        assert discharge(app, 18, charging=True) == "" and discharge(app, 18) == ""
    assert discharge(app, 9, charging=True) == "" and discharge(app, 9) == "Battery at 9%. Plug in soon."
    assert discharge(app, 9, charging=True) == "" and discharge(app, 9) == ""
    assert discharge(app, 60, charging=True) == "" and discharge(app, 60) == ""
    assert discharge(app, 20) == "Battery at 20%. Plug in to keep going."


def test_a_battery_that_is_already_low_at_the_start_shows_in_the_gauge_only(tmp_path, drawn):
    app = make_app(tmp_path, keys=KEYS, hw=Hardware(battery={"percent": 15, "charging": False}))
    app.step(0)
    assert app.toast_text == "" and "15%" in screen_text(app, drawn)
    assert discharge(app, 14) == "" and discharge(app, 10) == "Battery at 10%. Plug in soon."
    plugged = make_app(tmp_path / "plugged", keys=KEYS, hw=Hardware(battery={"percent": 15, "charging": True}))
    assert discharge(plugged, 15, charging=True) == "" and discharge(plugged, 15) == ""
    assert discharge(plugged, 10) == "Battery at 10%. Plug in soon."
    splash = make_app(tmp_path / "splash", splash=True, hw=Hardware(battery={"percent": 60, "charging": False}))
    assert discharge(splash, 60) == "" and discharge(splash, 5) == "" and isinstance(splash.screen, screens.Splash)


def test_the_first_reading_is_the_only_one_that_is_never_announced(tmp_path):
    app = make_app(tmp_path, keys=KEYS, hw=Hardware(battery={"percent": 22, "charging": True}))
    assert discharge(app, 22, charging=True) == "" and discharge(app, 19, charging=True) == ""
    assert discharge(app, 19) == "Battery at 19%. Plug in to keep going."
    low = make_app(tmp_path / "low", keys=KEYS, hw=Hardware(battery={"percent": 11, "charging": True}))
    assert discharge(low, 11, charging=True) == "" and discharge(low, 10) == "Battery at 10%. Plug in soon."
    assert discharge(low, 30, charging=True) == "" and discharge(low, 9) == ""
    assert discharge(low, 30) == "" and discharge(low, 9) == "Battery at 9%. Plug in soon."


def test_almost_empty_a_wardrive_is_saved_and_quartermaster_closes(tmp_path, later):
    app = make_app(tmp_path, keys=KEYS)
    press(app, "W")
    assert discharge(app, 5) == "Battery at 5%. Plug in soon." and discharge(app, 3) == ""
    later(14)
    assert discharge(app, 3) == "" and discharge(app, 0) == "" and app.wardriving and app.running
    later(1.5)
    assert discharge(app, 3) == "Battery almost empty. Wardrive saved. Closing now." and app.toast_color == RED
    assert not app.wardriving and isinstance(app.screen, screens.Summary) and app.running
    assert len(list((tmp_path / "logs").glob("*.summary.csv"))) == 1
    later(9)
    assert [discharge(app, level) for level in (2, 4, 9)] == [""] * 3 and app.running
    app.hw.battery = None
    app.step(0)
    assert app.running
    later(1.5)
    app.step(0)
    assert not app.running


def test_the_wait_before_closing_starts_again_when_the_level_comes_back_up(tmp_path, later):
    app = make_app(tmp_path, keys=KEYS)
    press(app, "W")
    assert discharge(app, 3) == "Battery at 3%. Plug in soon."
    later(10)
    assert discharge(app, 4) == ""
    later(10)
    assert discharge(app, 3) == "Battery 3%. Wardrive saved. Still recording."
    later(14)
    assert discharge(app, 3) == "" and app.wardriving
    later(1.5)
    assert discharge(app, 3) == "Battery almost empty. Wardrive saved. Closing now."


def test_a_charger_that_comes_and_goes_does_not_put_off_the_save(tmp_path, later):
    app = make_app(tmp_path, keys=KEYS)
    press(app, "W")
    assert discharge(app, 2) == "Battery at 2%. Plug in soon."
    for seconds, charging in ((5, True), (5, False), (5, True), (5.5, True)):
        later(seconds)
        assert discharge(app, 2, charging) == "" and app.wardriving
    later(0.5)
    assert discharge(app, 2) == "Battery almost empty. Wardrive saved. Closing now." and not app.wardriving


def test_a_charger_that_holds_ends_the_wait_before_closing(tmp_path, later):
    app = make_app(tmp_path, keys=KEYS)
    press(app, "W")
    assert discharge(app, 2) == "Battery at 2%. Plug in soon."
    later(5)
    assert discharge(app, 2, charging=True) == ""
    later(14)
    assert discharge(app, 2, charging=True) == ""
    later(1.5)
    assert discharge(app, 2, charging=True) == "" and app.wardriving
    later(3)
    assert discharge(app, 2) == ""
    later(14)
    assert discharge(app, 2) == "" and app.wardriving
    later(1.5)
    assert discharge(app, 2) == "Battery almost empty. Wardrive saved. Closing now."


def test_plugging_in_keeps_quartermaster_open(tmp_path, later):
    app = make_app(tmp_path, keys=KEYS)
    press(app, "R", "SPACE")
    assert app.recon_running and discharge(app, 2) == "Battery at 2%. Plug in soon."
    later(15.5)
    assert discharge(app, 2) == "Battery almost empty. Closing now." and not app.recon_running and app.running
    later(10.5)
    assert discharge(app, 2, charging=True) == "Charging. Quartermaster stays open." and app.toast_color == GREEN
    assert discharge(app, 2, charging=True) == "" and app._closing is None and app.running
    assert discharge(app, 3) == ""
    later(15.5)
    assert discharge(app, 3) == "Battery almost empty. Closing now." and discharge(app, 3) == "" and app.running
    later(10.5)
    assert discharge(app, 3) == "" and not app.running


def test_the_notice_says_saved_only_when_a_log_was_saved(tmp_path, later):
    app = make_app(tmp_path, keys=KEYS, fix=NO_FIX)
    press(app, "W")
    assert app.wardriving and discharge(app, 3) == "Battery at 3%. Plug in soon."
    later(15.5)
    assert discharge(app, 3) == "Battery almost empty. Closing now." and not app.wardriving
    assert list((tmp_path / "logs").iterdir()) == []
    failing = losing_rows(tmp_path / "failing")
    assert failing.drive.failed == "" and discharge(failing, 3) == "Battery at 3%. Plug in soon."
    later(15.5)
    assert discharge(failing, 3) == "Battery almost empty. Closing now." and not failing.wardriving
    assert failing.drive.failed == LOST
    again = losing_rows(tmp_path / "again", later)
    again.drive._wifi_sink(networks(430)[420:], ADAPTERS[0])
    assert (again.drive.error, again.drive.failed) == ("", LOST)
    assert discharge(again, 3) == "Battery at 3%. Plug in soon."
    later(15.5)
    assert discharge(again, 3) == "Battery almost empty. Closing now." and not again.wardriving


def test_the_closing_notice_leaves_the_reason_a_wardrive_was_not_saved_in_view(tmp_path, later, monkeypatch):
    app = losing_rows(tmp_path)
    assert discharge(app, 3) == "Battery at 3%. Plug in soon."
    later(15.5)
    assert discharge(app, 3) == "Battery almost empty. Closing now." and isinstance(app.screen, screens.Summary)
    lines = {text: y for text, (y, _) in placed(app, monkeypatch).items()}
    notice = lines["Battery almost empty. Closing now."] - 3
    assert notice == gfx.H - gfx.FOOTER_H - 24 and "WARDRIVE ENDED" in lines
    assert gfx.STATUS_H < lines[LOST] < lines["Wi-Fi networks"] - 10 and lines["Saved as"] + 10 <= notice


def test_the_wait_before_closing_counts_only_an_empty_battery_off_the_charger(tmp_path, later):
    app = make_app(tmp_path, keys=KEYS)
    press(app, "W")
    assert discharge(app, 2, charging=True) == ""
    later(60)
    assert discharge(app, 2, charging=True) == "" and discharge(app, 2) == "Battery at 2%. Plug in soon."
    later(14)
    assert discharge(app, 2) == "" and app.wardriving
    app.hw.battery = None
    app.step(0)
    later(1.5)
    app.step(0)
    assert app.wardriving and app._closing is None
    assert discharge(app, 2) == "Battery almost empty. Wardrive saved. Closing now."
    level = make_app(tmp_path / "level", keys=KEYS)
    press(level, "W")
    assert discharge(level, 3) == "Battery at 3%. Plug in soon."
    later(10)
    assert discharge(level, 4, charging=True) == ""
    later(10)
    assert discharge(level, 3) == "Battery 3%. Wardrive saved. Still recording." and level.wardriving
    late = make_app(tmp_path / "late", keys=KEYS)
    press(late, "W")
    assert discharge(late, 2) == "Battery at 2%. Plug in soon."
    later(30)
    assert discharge(late, 2, charging=True) == "" and late.wardriving and late._closing is None
    assert discharge(late, 2) == "Battery almost empty. Wardrive saved. Closing now."


def test_a_save_that_fails_does_not_keep_quartermaster_open_on_an_empty_battery(tmp_path, later, monkeypatch):
    app = make_app(tmp_path, keys=KEYS)
    press(app, "W")

    def broken():
        raise RuntimeError("The save failed")
    monkeypatch.setattr(app.drive, "stop", broken)
    assert discharge(app, 3) == "Battery at 3%. Plug in soon."
    later(15.5)
    with pytest.raises(RuntimeError):
        discharge(app, 3)
    later(9)
    assert discharge(app, 3) == "" and app.running
    later(1.5)
    app.step(0)
    assert not app.running


def test_the_closing_notice_stays_for_ten_seconds_after_a_slow_save(tmp_path, later, monkeypatch):
    app = make_app(tmp_path, keys=KEYS)
    press(app, "W")
    stop = app.drive.stop

    def slow():
        later(7)
        stop()
    monkeypatch.setattr(app.drive, "stop", slow)
    assert discharge(app, 3) == "Battery at 3%. Plug in soon."
    later(15.5)
    assert discharge(app, 3) == "Battery almost empty. Wardrive saved. Closing now."
    assert 9 < app.toast_until - time.monotonic() <= 10
    later(9)
    assert discharge(app, 3) == "" and app.running
    later(1.5)
    app.step(0)
    assert not app.running
    earlier = make_app(tmp_path / "earlier", keys=KEYS)
    press(earlier, "W", "ESC", "SPACE", "ENTER")
    assert not earlier.wardriving and earlier.drive.log_path.exists()
    assert discharge(earlier, 3) == "Battery at 3%. Plug in soon."
    later(15.5)
    assert discharge(earlier, 3) == "Battery almost empty. Closing now."


def drained(app, level, charging=False):
    """The battery is at a low level now. What is said of the level itself is not what these tests are about."""
    said = discharge(app, level, charging)
    return "" if said.startswith("Battery at ") else said


def recovered_rows(tmp_path, app):
    """The rows a power cut right now would leave, once the next start has finished the log."""
    from quartermaster import engine
    from tests.test_engine import rows_of
    copy = tmp_path / "cut" / "logs"
    shutil.rmtree(copy, ignore_errors=True)
    shutil.copytree(app.logs_dir, copy)
    engine._recover(copy, None)
    return [row for path in copy.glob("quartermaster_*.wigle.csv") for row in rows_of(path)]


def test_at_five_percent_a_running_wardrive_is_saved_in_full_once_and_keeps_recording(tmp_path, later):
    from tests.fakes import ADAPTERS, networks
    app = make_app(tmp_path, keys=KEYS)
    press(app, "W")
    drive = app.drive
    assert drained(app, 6) == ""
    later(60)
    assert drained(app, 6) == "" and not drive.low_battery
    assert drained(app, 5) == ""
    later(14)
    assert drained(app, 5) == "" and not drive.low_battery and len(recovered_rows(tmp_path, app)) < 412
    later(1.5)
    assert drained(app, 5) == "Battery 5%. Wardrive saved. Still recording." and app.toast_color == GREEN
    assert app.wardriving and drive.low_battery and isinstance(app.screen, screens.Wardrive) and app.running
    assert len(list(app.logs_dir.glob("*.summary.csv"))) == 1 and not list(app.logs_dir.glob("*.wigle.csv"))
    saved = recovered_rows(tmp_path, app)
    assert len({row[0] for row in saved}) == drive.status()["wifi"] == 412
    later(120)
    assert [drained(app, level) for level in (5, 4, 5)] == [""] * 3 and app.wardriving
    drive._wifi_sink(networks(3, seed=99), ADAPTERS[0])
    drive._keep_saving()
    assert len({row[0] for row in recovered_rows(tmp_path, app)}) == drive.status()["wifi"] > 412
    app.stop_wardrive()
    assert len({row[0] for row in rows_of_logs(app)}) == drive.status()["wifi"]
    press(app, "ESC", "W")
    assert app.drive is not drive and app.toast_text == "Battery 5%. Wardrive saved. Still recording."
    assert app.drive.low_battery and drained(app, 5) == ""


def rows_of_logs(app):
    from tests.test_engine import rows_of
    return [row for path in app.logs_dir.glob("quartermaster_*.wigle.csv") for row in rows_of(path)]


def test_the_save_at_five_percent_waits_like_the_close_does(tmp_path, later):
    app = make_app(tmp_path, keys=KEYS)
    press(app, "W")
    assert drained(app, 5, charging=True) == ""
    later(60)
    assert drained(app, 5, charging=True) == "" and not app.drive.low_battery
    assert drained(app, 5) == ""
    later(10)
    assert drained(app, 5, charging=True) == ""
    later(6)
    assert drained(app, 5) == "Battery 5%. Wardrive saved. Still recording."
    held = make_app(tmp_path / "held", keys=KEYS)
    press(held, "W")
    assert drained(held, 4) == ""
    later(10)
    assert drained(held, 4, charging=True) == ""
    later(16)
    assert drained(held, 4, charging=True) == "" and drained(held, 4) == ""
    later(14)
    assert drained(held, 4) == "" and not held.drive.low_battery
    later(1.5)
    assert drained(held, 4) == "Battery 4%. Wardrive saved. Still recording."
    up = make_app(tmp_path / "up", keys=KEYS)
    press(up, "W")
    assert drained(up, 5) == ""
    later(10)
    assert drained(up, 6) == ""
    later(10)
    assert drained(up, 5) == "" and not up.drive.low_battery


def test_a_charger_that_holds_ends_the_extra_saving_and_it_starts_again_without_a_word(tmp_path, later, drawn):
    app = make_app(tmp_path, keys=KEYS)
    press(app, "W")
    drive = app.drive
    assert drained(app, 5) == ""
    later(16)
    del drawn[:]
    assert drained(app, 5) == "Battery 5%. Wardrive saved. Still recording." and drive.low_battery
    assert "Saving the wardrive…" in drawn
    assert drained(app, 5, charging=True) == "" and drive.low_battery
    later(14)
    assert drained(app, 5, charging=True) == "" and drive.low_battery
    later(1.5)
    assert drained(app, 6, charging=True) == "" and not drive.low_battery
    assert drained(app, 5) == "" and not drive.low_battery
    later(16)
    del drawn[:]
    saves, checkpoint = [], drive.checkpoint
    drive.checkpoint = lambda keep=False: saves.append(keep) or checkpoint(keep)
    assert drained(app, 5) == "" and drive.low_battery and saves == [True] and "Saving the wardrive…" not in drawn
    assert drained(app, 5) == "" and saves == [True]
    assert drained(app, 20, charging=True) == ""
    later(16)
    assert drained(app, 20, charging=True) == "" and not drive.low_battery
    assert drained(app, 5) == ""
    later(16)
    assert drained(app, 5) == "Battery 5%. Wardrive saved. Still recording." and drive.low_battery
    assert saves == [True, True]


def test_at_five_percent_nothing_is_saved_without_a_wardrive_and_a_drive_without_rows_says_so(tmp_path, later):
    idle = make_app(tmp_path, keys=KEYS)
    assert drained(idle, 5) == ""
    later(30)
    assert drained(idle, 5) == "" and list(idle.logs_dir.iterdir()) == []
    waiting = make_app(tmp_path / "waiting", keys=KEYS, fix=NO_FIX)
    press(waiting, "W")
    assert drained(waiting, 5) == ""
    later(16)
    assert drained(waiting, 5) == "Battery 5%. Nothing to save yet. Still recording."
    assert waiting.toast_color == AMBER and waiting.drive.low_battery and waiting.wardriving


def test_a_save_at_five_percent_that_fails_is_said_and_the_wardrive_goes_on(tmp_path, later, monkeypatch, noted):
    app = make_app(tmp_path, keys=KEYS)
    press(app, "W")
    assert drained(app, 5) == ""

    def broken(keep=False):
        raise RuntimeError("no")
    monkeypatch.setattr(app.drive, "checkpoint", broken)
    later(16)
    assert drained(app, 5) == "Battery 5%. The wardrive could not be saved just now." and app.toast_color == RED
    assert app.wardriving and "Saving the wardrive on a low battery" in noted.read_text()
    assert drained(app, 5) == ""
    refused = make_app(tmp_path / "refused", keys=KEYS)
    press(refused, "W")
    assert drained(refused, 5) == ""
    refused.drive.error = refused.drive.failed = "Cannot write the log: No space left on device"
    later(16)
    assert drained(refused, 5) == "Battery 5%. Cannot write the log: No space left on device"


def test_the_save_at_five_percent_comes_before_the_close_at_three(tmp_path, later):
    app = make_app(tmp_path, keys=KEYS)
    press(app, "W")
    assert drained(app, 5) == ""
    later(16)
    assert drained(app, 5) == "Battery 5%. Wardrive saved. Still recording."
    assert drained(app, 3) == ""
    later(16)
    assert discharge(app, 3) == "Battery almost empty. Wardrive saved. Closing now." and not app.wardriving
