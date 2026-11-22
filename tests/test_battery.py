from quartermaster import power
from quartermaster.ui import app as app_module, gfx, screens
from quartermaster.ui.gfx import AMBER, GREEN, MUTED, RED, TEXT, W, Canvas, font
from tests.fakes import Hardware
from tests.test_ui import KEYS, make_app, press, screen_text


def supply(root, name, **files):
    folder = root / name
    folder.mkdir(parents=True)
    for file, content in files.items():
        (folder / file).write_text(content)
    return folder


def test_the_battery_is_read_the_way_the_system_reports_it(tmp_path):
    gauge = supply(tmp_path, "bq27220-0", type="Battery\n", capacity="81\n", status="Charging\n", present="1\n")
    assert power.battery(tmp_path) == {"percent": 81, "charging": True}
    for status in ("Discharging", "Not charging", "Unknown", "charging", ""):
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


def test_the_battery_of_a_wireless_keyboard_is_not_the_devices_own(tmp_path):
    supply(tmp_path, "hidpp_battery_0", type="Battery\n", scope="Device\n", capacity="12\n", status="Discharging\n")
    assert power.battery(tmp_path) is None
    supply(tmp_path, "zz-gauge", type="Battery\n", capacity="64\n", status="Discharging\n")
    assert power.battery(tmp_path) == {"percent": 64, "charging": False}
    supply(tmp_path, "bq27220-0", type="Battery\n", scope="System\n", capacity="81\n", status="Charging\n")
    assert power.battery(tmp_path) == {"percent": 81, "charging": True}


def test_the_hardware_watch_keeps_the_battery_level_fresh(monkeypatch):
    levels = iter([None, {"percent": 50, "charging": False}, None, {"percent": 49, "charging": True}, None])
    monkeypatch.setattr(power, "battery", lambda: next(levels))
    for name in ("wifi_adapters", "bluetooth_adapters", "gps_candidates", "stalled_usb_adapters"):
        monkeypatch.setattr(app_module.hardware, name, lambda: [])
    watch = app_module.HardwareWatch()
    assert watch.battery is None
    seen = []
    for _ in range(4):
        watch.refresh()
        seen.append(watch.battery)
    assert seen == [None, {"percent": 50, "charging": False}, {"percent": 50, "charging": False},
                    {"percent": 49, "charging": True}]
    monkeypatch.setattr(app_module, "BATTERY_SILENCE", -1)
    watch.refresh()
    assert watch.battery is None

    def unplugged():
        raise OSError("gone")
    monkeypatch.setattr(app_module.hardware, "wifi_adapters", unplugged)
    monkeypatch.setattr(power, "battery", lambda: {"percent": 48, "charging": False})
    watch.refresh()
    assert watch.battery == {"percent": 48, "charging": False}


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


def test_the_gauge_turns_amber_then_red_and_green_while_charging(tmp_path, drawn):
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
    for level in (100, 15, 3):
        app.hw.battery = {"percent": level, "charging": True}
        picture, _ = bar(app)
        assert fill(picture, 0) == GREEN and picture.getpixel((W - 5 - 22, 8)) == GREEN


def test_a_device_without_a_battery_shows_no_gauge(tmp_path, drawn):
    app = make_app(tmp_path, hw=Hardware(battery=None))
    text = screen_text(app, drawn)
    assert "%" not in text and "GPS 14" in text
    small = font("monob", 9)
    assert app.indicators(Canvas(), None)[0][0] == int(Canvas().text_width("GPS 14", small))


def room(app, canvas, percent=True):
    """How wide the title may be before it reaches the indicators."""
    return W - 33 - sum(width + 8 for width, _ in app.indicators(canvas, app.hw.battery, percent))


def test_the_title_never_runs_into_the_indicators(tmp_path, drawn):
    app = make_app(tmp_path, keys=KEYS)
    app.hw.battery = {"percent": 100, "charging": True}
    press(app, "W")
    app.upload_status = lambda: {"uploading": False, "waiting": 7, "keys": 2, "files": []}
    press(app, "ESC")
    assert isinstance(app.screen, screens.Home) and app.wardriving
    canvas, title = Canvas(), font("bold", 11)
    width = canvas.text_width("QUARTERMASTER", title)
    assert room(app, canvas, percent=False) >= width > room(app, canvas)
    text = screen_text(app, drawn).split("\n")
    assert "QUARTERMASTER" in text and "100%" not in text and "7" in text and "GPS 14" in text
    app.screen.title = "IGNORED NETWORKS AND MORE"
    text = screen_text(app, drawn).split("\n")
    shortened = [line for line in text if line.endswith("…")]
    assert len(shortened) == 1 and shortened[0].startswith("IGNORED NETW") and "100%" not in text
    assert canvas.text_width(shortened[0], title) <= room(app, canvas, percent=False)
    app.hw.battery = {"percent": 100, "charging": False}
    app.screen.title = "QUARTERMASTER"
    assert room(app, canvas) >= width
    text = screen_text(app, drawn).split("\n")
    assert "QUARTERMASTER" in text and "100%" in text
    app.hw.battery = None
    app.screen.title = "IGNORED NETWORKS AND MORE AND MORE"
    text = screen_text(app, drawn).split("\n")
    assert [line for line in text if line.endswith("…")][0].startswith("IGNORED NETWORKS A")


def discharge(app, level, charging=False):
    app.toast_text = ""
    app.hw.battery = {"percent": level, "charging": charging}
    app.step(0)
    return app.toast_text


def test_a_battery_running_down_is_announced_once_per_level(tmp_path):
    app = make_app(tmp_path, keys=KEYS)
    assert [discharge(app, level) for level in (87, 50, 25, 21)] == [""] * 4
    assert discharge(app, 20) == "Battery at 20%. Plug in to keep going." and app.toast_color == AMBER
    assert [discharge(app, level) for level in (19, 20, 21, 22, 20, 11)] == [""] * 6
    assert discharge(app, 10) == "Battery at 10%. Plug in soon." and app.toast_color == RED
    assert [discharge(app, level) for level in (9, 12, 4)] == [""] * 3
    assert discharge(app, 9, charging=True) == "" and discharge(app, 15) == "Battery at 15%. Plug in to keep going."
    assert discharge(app, 25) == "" and discharge(app, 8) == "Battery at 8%. Plug in soon."
    app.hw.battery = None
    app.step(0)
    assert discharge(app, 7) == ""


def test_a_battery_that_is_already_low_at_the_start_shows_in_the_gauge_only(tmp_path, drawn):
    app = make_app(tmp_path, keys=KEYS, hw=Hardware(battery={"percent": 15, "charging": False}))
    app.step(0)
    assert app.toast_text == "" and "15%" in screen_text(app, drawn)
    assert discharge(app, 14) == "" and discharge(app, 10) == "Battery at 10%. Plug in soon."
    splash = make_app(tmp_path, splash=True, hw=Hardware(battery={"percent": 60, "charging": False}))
    assert discharge(splash, 5) == "" and isinstance(splash.screen, screens.Splash)


def test_a_wardrive_is_saved_before_the_battery_is_empty(tmp_path, drawn, monkeypatch):
    app = make_app(tmp_path, keys=KEYS)
    press(app, "W")
    assert discharge(app, 5) == "Battery at 5%. Plug in soon." and discharge(app, 4) == ""
    assert discharge(app, 3) == "" and app.wardriving
    assert discharge(app, 9) == "" and discharge(app, 0) == "" and discharge(app, 2, charging=True) == ""
    assert discharge(app, 3) == "Battery at 3%. Plug in soon." and app.wardriving
    monkeypatch.setattr(app_module, "EMPTY_SECONDS", 0)
    assert discharge(app, 3) == "Battery almost empty. The wardrive is saved." and app.toast_color == RED
    assert not app.wardriving and isinstance(app.screen, screens.Summary)
    log = app.drive.log_path
    assert log.exists() and log.with_name(log.name.replace(".wigle.csv", ".summary.csv")).stat().st_size > 0
    assert "WARDRIVE SAVED" in screen_text(app, drawn)
    press(app, "ESC", "W")
    assert app.wardriving and discharge(app, 2) == "" and discharge(app, 1) == "" and app.wardriving
    assert discharge(app, 1, charging=True) == "" and app.wardriving
    assert discharge(app, 1) == "Battery almost empty. The wardrive is saved." and not app.wardriving
    idle = make_app(tmp_path / "idle", keys=KEYS)
    monkeypatch.setattr(app_module, "EMPTY_SECONDS", 15)
    assert discharge(idle, 40) == "" and discharge(idle, 2) == "Battery at 2%. Plug in soon."
    monkeypatch.setattr(app_module, "EMPTY_SECONDS", 0)
    assert discharge(idle, 2) == "Battery almost empty. Plug in now." and discharge(idle, 1) == ""
