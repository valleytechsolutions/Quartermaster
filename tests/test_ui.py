import http.client
import time

import pytest

from quartermaster import accounts, config, radios, uploads
from quartermaster.engine import Wardrive
from quartermaster.recon_engine import Recon
from quartermaster.ui import recon_screens, screens
from quartermaster.ui.app import App
from quartermaster.ui.keys import ScriptedInput
from tests.fakes import ADAPTERS, FIX, NO_FIX, Display, Fleet, Gps, Hardware, NoBle, QuietFleet, Silent
from tests.test_core import NOW
from tests.test_uploads import KEYS


def make_app(tmp_path, fix=FIX, hw=None, splash=False, keys=None, **settings):
    cfg = config.load(tmp_path / "config.json")
    cfg.update(ble=False, **settings)
    if keys:
        accounts.save(keys, tmp_path / "accounts.json")
    gps = Gps(fix)
    sent = []

    def sender(service, path, saved, endpoints):
        sent.append((service, path.name))
        return uploads.Outcome(uploads.DONE, "%s has it" % accounts.SERVICE_NAMES[service])
    uploader = uploads.Uploader(tmp_path, cfg, accounts_path=tmp_path / "accounts.json", sender=sender,
                                clock=lambda: NOW)
    app = App(Display(), ScriptedInput(), gps, cfg, root=tmp_path, splash=splash,
              wardrive=lambda c, g: Wardrive(c, g, root=tmp_path, adapters=ADAPTERS, ble=NoBle(), fleet=Fleet,
                                             clock=lambda: NOW),
              recon=Recon(cfg, gps, root=tmp_path, adapters=ADAPTERS, ble=NoBle(), fleet=QuietFleet, sound=Silent(),
                          clock=lambda: NOW),
              uploader=uploader, hw=hw or Hardware(), accounts_path=tmp_path / "accounts.json",
              config_path=tmp_path / "config.json",
              tester=lambda service, saved: uploads.Outcome(uploads.DONE, "Signed in to " + service))
    uploader.active = app.active_log
    app.sent = sent
    return app


def press(app, *names):
    app.keyboard.push(*names)
    while app.keyboard.queue:
        app.step(0)
    app.step(0)
    return app.compose()


def typed(app, text):
    app.step(0)
    app.keyboard.type(text)
    while app.keyboard.queue:
        app.step(0)


def screen_text(app, drawn):
    del drawn[:]
    app.compose()
    return "\n".join(drawn)


def settle(app):
    """Wait for background key checks and run what they queued for the screen thread."""
    deadline = time.monotonic() + 5
    while any(state == "busy" for state, _ in app.tests.values()) and time.monotonic() < deadline:
        time.sleep(0.01)
    app.step(0)


def test_menu_opens_every_screen_and_quits(tmp_path, drawn):
    app = make_app(tmp_path)
    text = screen_text(app, drawn)
    assert "QUARTERMASTER" in text and "3 radios" in text and "0 of 3 set" in text and "GPS 14" in text
    for letter, kind in (("L", screens.Logs), ("U", screens.Uploads), ("H", screens.Hardware),
                         ("S", screens.Settings), ("K", screens.Keys), ("A", screens.About),
                         ("R", recon_screens.Recon)):
        press(app, letter)
        assert isinstance(app.screen, kind) and len(app.stack) == 2
        press(app, "ESC")
        assert isinstance(app.screen, screens.Home)
    press(app, "A", "T")
    assert isinstance(app.screen, screens.Tips)
    press(app, "DOWN", "PAGEDOWN", "ESC", "ESC", "RIGHT", "DOWN", "ENTER")
    assert isinstance(app.screen, screens.Hardware)         # right of the last tile wraps to the first, then down a row
    press(app, "ESC", "Q")
    assert isinstance(app.screen, screens.Confirm) and app.running
    press(app, "Y")
    assert not app.running


def test_wardrive_saves_uploads_and_deletes(tmp_path, drawn):
    app = make_app(tmp_path, keys=KEYS)
    press(app, "W")
    assert app.wardriving and isinstance(app.screen, screens.Wardrive)
    text = screen_text(app, drawn)
    assert "412" in text and "WARDRIVE" in text and "3 radios" in text and "Stop + save" in text
    press(app, "TAB")
    assert app.screen.filter == "wifi"
    press(app, "ESC")
    assert isinstance(app.screen, screens.Home) and app.wardriving and "REC 00:00:0" in screen_text(app, drawn)
    press(app, "L")
    assert "REC" in screen_text(app, drawn)                 # the log being written is marked, not offered for upload
    press(app, "ESC", "SPACE")
    assert isinstance(app.screen, screens.Confirm)
    press(app, "ENTER")
    assert not app.wardriving and isinstance(app.screen, screens.Summary) and app.display.frames == 1
    text = screen_text(app, drawn)
    assert "WARDRIVE SAVED" in text and "Uploading to WiGLE, WDGWars, Wardrift." in text and "412" in text
    log = app.drive.log_path
    assert log.exists() and log.with_name(log.name.replace(".wigle.csv", ".summary.csv")).exists()
    assert app.uploader.run_pass() is False and len(app.sent) == 3
    press(app, "L")
    assert isinstance(app.screen, screens.Logs) and len(app.log_files(refresh=True)) == 1
    assert app.uploader.marks(log.name) == {"wigle": "done", "wdgwars": "done", "wardrift": "done"}
    press(app, "ENTER")
    text = screen_text(app, drawn)
    assert "WiGLE has it" in text and "412" in text and log.name in text.replace("\n", "")
    press(app, "D")
    assert isinstance(app.screen, screens.Confirm)
    press(app, "RIGHT", "ENTER")
    assert not log.exists() and not list(tmp_path.glob("logs/*.csv")) and app.uploader.ledger.data == {}
    assert isinstance(app.screen, screens.Logs) and "No wardrives saved yet" in screen_text(app, drawn)


def test_wardrive_without_gps_says_why_nothing_is_logged(tmp_path, drawn):
    stalled = [{"usb_id": "0e8d:7961", "label": "MT7921AU tri-band", "port": "1-1.2"}]
    app = make_app(tmp_path, fix=NO_FIX, hw=Hardware(stalled=stalled))
    press(app, "W")
    text = screen_text(app, drawn)
    assert "No GPS found. Nothing is logged until one has a fix." in text and "NO GPS" in text
    assert "MT7921AU tri-band is not starting" in app.toast_text
    press(app, "ENTER", "ENTER")
    text = screen_text(app, drawn)
    assert "WARDRIVE ENDED" in text and "Nothing: GPS never had a fix" in text and not list(tmp_path.glob("logs/*"))
    press(app, "ESC", "H")
    text = screen_text(app, drawn)
    assert "On USB but not starting" in text and "CP210x USB GPS found" in text and "wlan1" in text
    press(app, "DOWN", "DOWN", "DOWN")
    assert "hci0" in screen_text(app, drawn) and app.screen.top == 1


def test_keys_are_typed_tested_and_removed_on_the_device(tmp_path, drawn):
    app = make_app(tmp_path)
    press(app, "K")
    assert "Set up from a phone or PC" in screen_text(app, drawn)
    press(app, "DOWN", "DOWN", "DOWN", "ENTER")
    assert isinstance(app.screen, screens.TextInput)
    typed(app, "f" * 64)
    assert app.screen.text_mode and app.screen.value == "f" * 64      # f is a letter here, not the up arrow
    press(app, "BACKSPACE")
    typed(app, "f")
    press(app, "ENTER")
    assert accounts.load(tmp_path / "accounts.json")["wdgwars_key"] == "f" * 64
    settle(app)
    assert app.tests["wdgwars"] == (uploads.DONE, "Signed in to wdgwars")
    text = screen_text(app, drawn)
    assert "••••ffff" in text and "f" * 64 not in text and "Signed in to wdgwars" in text
    press(app, "UP", "ENTER")
    typed(app, "0123456789abcdef")
    press(app, "ENTER")
    assert "add the WiGLE API name" in app.toast_text and accounts.ready(app.accounts()) == ["wdgwars"]
    press(app, "DOWN", "D", "Y")
    assert accounts.load(tmp_path / "accounts.json")["wdgwars_key"] == "" and "wdgwars" not in app.tests
    press(app, "ESC", "U")
    text = screen_text(app, drawn)
    assert "Add a key to start uploading" in text and text.count("NO KEY") == 3


def test_settings_are_saved_and_locked_while_scanning(tmp_path, drawn):
    app = make_app(tmp_path)
    press(app, "S", "RIGHT")
    assert app.cfg["wifi_adapters"] == config.load(tmp_path / "config.json")["wifi_adapters"] == "external"
    press(app, "LEFT", "LEFT")
    assert app.cfg["wifi_adapters"] == "internal"
    press(app, "ENTER")
    assert isinstance(app.screen, screens.Picker)
    press(app, "UP", "UP", "ENTER")
    assert app.cfg["wifi_adapters"] == "all" and isinstance(app.screen, screens.Settings)
    settings = app.screen
    settings.index = next(i for i, item in enumerate(settings.items) if item[0] == "ble")
    press(app, "ENTER")
    assert app.cfg["ble"] is True                            # two choices: OK flips it without a list
    settings.index = next(i for i, item in enumerate(settings.items) if item[0] == "ignore")
    press(app, "ENTER", "A")
    typed(app, "Home WiFi")
    press(app, "ENTER", "A")
    typed(app, "aa:bb:cc:dd:ee:ff")
    press(app, "ENTER")
    assert app.cfg["ignore"] == ["Home WiFi", "aa:bb:cc:dd:ee:ff"]
    text = screen_text(app, drawn)
    assert "Home WiFi" in text and "address" in text and "name" in text
    press(app, "D", "RIGHT", "ENTER")
    assert config.load(tmp_path / "config.json")["ignore"] == ["aa:bb:cc:dd:ee:ff"]
    press(app, "ESC", "ESC", "W", "ESC", "S")
    assert app.wardriving and app.drive.ignore_macs == {"AA:BB:CC:DD:EE:FF"}
    press(app, "RIGHT")
    assert app.cfg["wifi_adapters"] == "all" and "Stop the wardrive" in app.toast_text
    assert "Stop the wardrive to change this setting." in screen_text(app, drawn)
    settings = app.screen
    settings.index = next(i for i, item in enumerate(settings.items) if item[0] == "ignore")
    press(app, "ENTER", "A")
    typed(app, "Black Pearl")
    press(app, "ENTER")
    assert app.drive.ignore_names == {"Black Pearl"}         # the ignore list applies to a running wardrive at once


def test_recon_lists_devices_and_never_overlaps_a_wardrive(tmp_path, drawn):
    app = make_app(tmp_path)
    press(app, "R")
    assert "START RECON" in screen_text(app, drawn)
    press(app, "T")
    assert isinstance(app.screen, recon_screens.Categories)
    press(app, "ENTER")
    assert app.cfg["recon_categories"] == [c for c in config.default_lists()["recon_categories"] if c != "alpr"]
    press(app, "A", "ESC", "SPACE")
    assert app.recon_running and radios.owner() == "recon"
    assert "Nothing flagged nearby" in screen_text(app, drawn)
    app.recon.observe("wifi", {"bssid": "00:25:DF:11:22:33", "ssid": "", "rssi": -48, "channel": 6,
                               "frequency": 2437, "security": "WPA2", "band": "2.4"})
    app.recon.observe("wifi", {"bssid": "AA:BB:CC:00:00:01", "ssid": "Coffee Shop", "rssi": -70, "channel": 1,
                               "frequency": 2412, "security": "OPEN", "band": "2.4"})
    text = screen_text(app, drawn)
    assert "BODYCAM" in text and "Coffee Shop" not in text
    press(app, "A")
    assert "Coffee Shop" in screen_text(app, drawn)
    press(app, "A", "S", "ENTER")
    assert isinstance(app.screen, recon_screens.ReconDetail) and "Body-camera candidate" in screen_text(app, drawn)
    press(app, "ENTER")
    assert isinstance(app.screen, recon_screens.Finder) and "-48" in screen_text(app, drawn)
    press(app, "R", "ESC", "ESC", "ESC")
    assert isinstance(app.screen, screens.Home) and "1 flagged" in screen_text(app, drawn)
    app.recon.unseen_alerts = 1
    assert "R to view" in screen_text(app, drawn)
    press(app, "H", "R")
    assert isinstance(app.screen, recon_screens.Recon)       # R jumps to a fresh alert from any screen
    press(app, "ESC", "W")
    assert isinstance(app.screen, screens.Confirm) and not app.wardriving
    press(app, "ENTER")
    assert app.wardriving and not app.recon_running and radios.owner() == "wardrive"
    press(app, "ESC", "R", "SPACE")
    assert isinstance(app.screen, screens.Confirm)
    press(app, "ENTER")
    assert app.recon_running and not app.wardriving and radios.owner() == "recon"
    assert len(list(tmp_path.glob("logs/*.wigle.csv"))) == 1
    press(app, "SPACE")
    assert not app.recon_running and radios.owner() is None and "Recon stopped. 0 devices flagged" in app.toast_text


def test_phone_setup_shows_the_page_only_while_its_screen_is_open(tmp_path, drawn):
    app = make_app(tmp_path)
    press(app, "K", "ENTER")
    setup = app.screen
    assert isinstance(setup, screens.PhoneSetup) and setup.server is not None
    text = screen_text(app, drawn)
    assert "http://192.168.1.50:%d" % setup.server.port in text and "Waiting for your phone" in text
    assert "%s %s" % (setup.server.pin[:3], setup.server.pin[3:]) in text and setup.code[1].size == (116, 116)
    port = setup.server.port

    def fetch(method, path, body=None, cookie=""):
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        headers = {"Content-Type": "application/x-www-form-urlencoded", "Cookie": cookie}
        conn.request(method, path, body, headers)
        reply = conn.getresponse()
        data = reply.read().decode()
        conn.close()
        return reply, data
    reply, page = fetch("GET", "/")
    assert reply.status == 200 and "Enter the PIN" in page
    reply, _ = fetch("POST", "/unlock", "pin=" + setup.server.pin)
    cookie = reply.getheader("Set-Cookie").split(";")[0]
    reply, page = fetch("GET", "/", cookie=cookie)
    token = page.split('name="csrf" value="')[1].split('"')[0]
    fetch("POST", "/save", "csrf=%s&wardrift_key=wdk_live_1234567890" % token, cookie)
    fetch("POST", "/ignore", "csrf=%s&ignore=Home+WiFi" % token, cookie)
    app.step(0)
    assert accounts.ready(app.accounts()) == ["wardrift"] and "Keys saved from the setup page" in app.toast_text
    assert app.cfg["ignore"] == ["Home WiFi"] and config.load(tmp_path / "config.json")["ignore"] == ["Home WiFi"]
    assert "Saved from the page" in screen_text(app, drawn)
    press(app, "ESC")
    assert setup.server is None and isinstance(app.screen, screens.Keys)
    with pytest.raises(OSError):
        fetch("GET", "/")
    offline = make_app(tmp_path, hw=Hardware(addresses=()))
    press(offline, "K", "ENTER")
    assert offline.screen.server is None and "Not on a network" in screen_text(offline, drawn)


def test_boot_animation_then_menu_and_auto_start(tmp_path, drawn):
    app = make_app(tmp_path, splash=True)
    assert isinstance(app.screen, screens.Splash)
    app.step(0)
    assert screen_text(app, drawn) == "" and isinstance(app.screen, screens.Splash)
    press(app, "ENTER")
    assert isinstance(app.screen, screens.Home) and "Press K to add your upload keys" in app.toast_text
    auto = make_app(tmp_path, keys=KEYS, auto_start=True)
    auto.step(0)
    assert auto.wardriving and isinstance(auto.screen, screens.Wardrive)
    auto.shutdown()
    assert not auto.wardriving and auto.stack == []


def test_uploads_screen_reports_each_site(tmp_path, drawn):
    app = make_app(tmp_path, keys=dict(KEYS, wardrift_key=""))
    press(app, "W", "ENTER", "ENTER", "ESC", "U")
    text = screen_text(app, drawn)
    assert "2 uploads waiting" in text and text.count("KEY SAVED") == 2 and "NO KEY" in text
    assert "1 waiting" in text and "0 sent" in text
    press(app, "ENTER")
    assert "Uploading now" in app.toast_text
    app.uploader.run_pass()
    app.stale()
    text = screen_text(app, drawn)
    assert "Every log has been sent" in text and "WDGWars has it" in text and "1 sent" in text
    press(app, "T")
    settle(app)
    assert "Signed in to wigle" in screen_text(app, drawn)
    app.cfg["auto_upload"] = False
    app.stale()
    assert "Manual" in screen_text(app, drawn)
