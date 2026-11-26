import pytest

from quartermaster import drive_watch
from quartermaster.ui import recon_screens, screens
from tests.fakes import Display, NO_FIX
from tests.test_ui import make_app, press, screen_text, typed


@pytest.fixture(params=[128, 240])
def app(request, monkeypatch, tmp_path):
    monkeypatch.setattr(Display, "profile", {128: "st7735-128", 240: "st7789-240"}[request.param], raising=False)
    monkeypatch.setattr(Display, "width", request.param, raising=False)
    monkeypatch.setattr(Display, "height", request.param, raising=False)
    return make_app(tmp_path)


def test_hat_menu_and_native_frames(app):
    for index, (_, label, _) in enumerate(screens.Home.TILES):
        assert app.screen.index == index
        img = app.compose()
        assert img.size == (app.display.width, app.display.height)
        if label == "Settings":
            press(app, "ENTER")
            assert isinstance(app.screen, screens.Settings)
            press(app, "DOWN", "ENTER")
            assert isinstance(app.screen, screens.Picker)
            press(app, "ESC", "ESC")
        press(app, "DOWN")
    assert app.screen.index == 0
    app.busy("Starting wardrive…")
    assert app.display.last.size == img.size


def test_hat_wardrive_actions_and_confirm(app):
    press(app, "ENTER")
    assert app.wardriving
    press(app, "TAB", "ENTER")
    assert app.screen.filter == "wifi"
    press(app, "ENTER")
    assert isinstance(app.screen, screens.Confirm)
    assert app.screen.choice == 1 and app.screen.labels == ("Cancel", "Stop + save", "Stop, don't save")
    press(app, "DOWN")
    assert app.screen.choice == 1
    press(app, "RIGHT")
    assert app.screen.choice == 2
    press(app, "RIGHT")
    assert app.screen.choice == 0
    press(app, "RIGHT", "ENTER")
    assert isinstance(app.screen, screens.Summary)
    assert not app.wardriving


def test_hat_throws_a_wardrive_away_with_the_stick_after_asking_once(app, drawn):
    press(app, "ENTER")
    assert app.wardriving
    press(app, "ENTER")
    text = screen_text(app, drawn).replace("\n", " ")
    assert "‹ Stop + save ›" in text and "The log is saved and sent to your upload sites." in text
    press(app, "RIGHT")
    text = screen_text(app, drawn).replace("\n", " ")
    assert "‹ Stop, don't save ›" in text and app.screen.choice == 2
    assert "The wardrive stops and is thrown away. You are asked once more first." in text
    assert "saved" not in text and "upload" not in text
    press(app, "RIGHT")
    text = screen_text(app, drawn).replace("\n", " ")
    assert "‹ Cancel ›" in text and "The log is saved and sent to your upload sites." in text
    press(app, "LEFT", "ENTER")
    assert app.screen.title == "Throw away this wardrive?" and app.screen.choice == 0 and app.wardriving
    text = screen_text(app, drawn).replace("\n", " ")
    assert "Throw away this wardrive?" in text and "412 networks will be lost." in text
    press(app, "ENTER")
    assert app.wardriving and isinstance(app.screen, screens.Wardrive)
    assert list(app.logs_dir.glob(".quartermaster_*.part"))
    press(app, "ENTER", "RIGHT", "ENTER", "RIGHT", "ENTER")
    assert not app.wardriving and isinstance(app.screen, screens.Home)
    assert app.toast_text == "Wardrive thrown away. Nothing was saved."
    assert [path for path in app.root.rglob("*") if path.is_file() and path.name != "config.json"] == []


def test_optional_battery_and_receivers_on_wardrive(app, drawn, monkeypatch):
    app.hw.battery = None
    assert "%" not in screen_text(app, drawn)
    app.hw.battery = {"percent": 17, "charging": False}
    assert "17%" in screen_text(app, drawn)
    press(app, "ENTER")
    status = app.drive.status()
    status.update(aircraft=23, adapters=status["adapters"] + [{"method": "node"}])
    monkeypatch.setattr(app.drive, "status", lambda: status)
    assert "Aircraft 23 · 1 node" in screen_text(app, drawn)
    press(app, "ENTER", "ENTER")


def test_pi_hardware_choices_and_gps_help(app, drawn):
    app.cfg.update(lora=True, drive_alert_types=["lorawan", "mine"])
    app.hw.gps_ports = []
    app.gps.fix = NO_FIX
    assert "USB GPS" in screens.gps_wait(app, {"gps": NO_FIX, "gps_weak": False})[1]
    app.open("Settings")
    entries = {key: (choices, note) for key, _, choices, note in app.screen.items}
    assert "lora" not in entries and "gps_power" not in entries
    assert "/dev/ttyS0" not in dict(entries["gps_source"][0])
    assert "Cap" not in str(entries["gps_source"])
    assert app.screen.value(app, "drive_alert_types").startswith("1 of ")
    for name in (recon_screens.Categories, recon_screens.AlertTypes):
        screen = name()
        app.push(screen)
        options = drive_watch.TYPES if name is recon_screens.AlertTypes else drive_watch.SQUACHWATCH_ORDER
        for i in range(len(options) - 1):
            screen.index = i
            assert "LoRaWAN networks" not in screen_text(app, drawn)
        app.pop()
    app.open("Hardware")
    app.hw.adapters = [dict(app.hw.adapters[0], external=False, bands=["2.4", "5"], band_label="dual-band")]
    cards = app.screen.cards(app)
    assert all(card["tag"] != "LORA" for card in cards)
    assert "gpsd" in next(card["detail"] for card in cards if card["tag"] == "GPS")
    assert "2.4 / 5 GHz" in screen_text(app, drawn)


def test_hat_can_pan_the_entire_detail_view(app):
    press(app, "H", "TAB", "ENTER")
    assert app.square.mode == "pan"
    press(app, *["RIGHT"] * 8, *["DOWN"] * 8)
    assert app.square.x + app.display.width == 320
    assert app.square.y + app.square.bottom - app.square.top >= 155
    press(app, *["LEFT"] * 8, *["UP"] * 8, "ESC")
    assert app.square.x == 0 and not app.square.mode
    assert isinstance(app.screen, screens.Hardware)
    press(app, "ESC")
    assert isinstance(app.screen, screens.Home)


def test_hat_text_entry_without_an_external_keyboard(app):
    saved = []
    app.push(screens.TextInput("Device name", saved.append))
    press(app, "TAB", "RIGHT", "RIGHT", "RIGHT", "ENTER", "RIGHT", "ENTER")
    assert app.screen.value == "ab"
    press(app, "TAB", "ENTER")
    assert saved == ["ab"]


def test_hat_masks_keys_while_typing(app, drawn):
    press(app, "K", "DOWN", "DOWN", "ENTER")
    assert isinstance(app.screen, screens.TextInput) and app.screen.secret
    typed(app, "secret-token")
    rendered = screen_text(app, drawn)
    assert "secret-token" not in rendered and "••••" in rendered
    assert app.screen.value == "secret-token"


def test_the_picture_of_both_hats_in_the_diy_guide_comes_from_the_screenshot_tool(tmp_path, drawn):
    from PIL import Image
    from tools import render_screens
    picture = render_screens.hats(tmp_path / "hats")
    with Image.open(render_screens.ROOT / "docs" / "screens" / "waveshare-preview.png") as shipped:
        assert picture.size == shipped.size == (1290, 550)
    assert drawn.count("‹ Stop + save ›") == 2 and "Stop the wardrive?" in drawn
    for line, size in enumerate((128, 240)):
        for column, name in enumerate(render_screens.HAT_TILES):
            assert "%s %d" % (name, size) in drawn
            shown = size * (256 // size)
            tile = picture.crop((column * 258, line * 275, column * 258 + shown, line * 275 + shown))
            assert len(tile.getcolors(100000)) > 8 and tile.getpixel((2, 2)) != render_screens.HAT_GROUND
            assert picture.getpixel((column * 258 + 257, line * 275 + 100)) == render_screens.HAT_GROUND
