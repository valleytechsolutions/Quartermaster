"""On a Raspberry Pi with a Waveshare HAT, no screen, pop-up or setup page names the Cardputer, the CardputerZero or
the AppStore, and none asks for a key that the HAT does not have. The CardputerZero keeps its own words."""

import functools
import html
import re

import pytest

from quartermaster import adsb, deepscan, powerguard, radio_helper, setup_server, skyview
from quartermaster.engine import Wardrive
from quartermaster.ui import recon_screens, screens, sky_screens
from tests.fakes import ADAPTERS, Display, Fleet, Hardware, NoBle, SkyHardware
from tests.test_core import NOW
from tests.test_setup import Browser
from tests.test_ui import make_app, press
from tests.test_uploads import KEYS
from tools import render_screens

NAMES = re.compile(r"cardputer|app ?store", re.I)
LACKS = re.compile(r"\b[Pp]ress [A-Z0-9]\b|\bSPACE\b|\b[A-Z]: [a-z]|\b(?:Del|Q) (?:hide|quiet)\b")
UPLOADS = [("Press K to add your upload keys", "Open Keys to add upload keys"),
           ("Press K to add your upload keys.", "Open Keys on the menu to add your upload keys."),
           ("Automatic uploads are off. Press U to send it.", "Automatic uploads are off. Open Uploads to send it."),
           ("Press K to add your key", "Open Keys to add your key"),
           ("Add a key first: press K", "Open Keys to add an upload key"),
           ("Or pick a key below and type it on the Cardputer.",
            "Or pick a key below and type it with the on-screen keyboard."),
           ("Press T to check that the key works.", "Press K3 and choose Test to check that the key works.")]
PHONE = [("Join Wi-Fi in the CardputerZero settings first. This screen updates by itself. You can also type keys on "
          "the Cardputer.",
          "Join a network first. Log in to the Pi and run sudo nmtui. This screen updates by itself. You can also "
          "type keys with the on-screen keyboard."),
         ("Use the same Wi-Fi as the Cardputer. The page closes when you leave this screen.",
          "Use the same network as the Pi. The page closes when you leave this screen.")]
PAGES = [("It is on the Cardputer screen right now.", "It is on the Pi's screen right now."),
         ("Could not reach the Cardputer. Is the setup screen still open?",
          "Could not reach the Pi. Is the setup screen still open?"),
         ("Too many wrong PINs. Close the setup screen on the Cardputer and open it again for a new PIN.",
          "Too many wrong PINs. Close the setup screen on the Pi and open it again for a new PIN.")]
HELPER = [("Radio helper not installed. Update Quartermaster from the AppStore.",
           "Radio helper not found. Reinstall the Pi package from GitHub."),
          ("Restart the Cardputer once so the new permission applies.",
           "Restart the Pi once so the new permission applies.")]
STICK = [("No permission yet. Restart the Cardputer once.", "No permission yet. Restart the Pi once."),
         ("Restart the Cardputer once to use it", "Restart the Pi once to use it")]
HARDWARE = HELPER + STICK + [("Estimate. Within what the Cardputer's port gives.",
                              "Estimate. Within what the Pi's USB ports give.")]
LISTS = [("SPACE  ·  START RECON", "K3  ·  START RECON"),
         ("Press A to list all 12 devices heard.", "Press K3 and choose All to list all 12 devices heard."),
         ("Press A to add a Wi-Fi name or MAC address, such as your home network or phone hotspot. It still shows on "
          "screen but is never logged or uploaded.",
          "To add a Wi-Fi name or MAC address, such as your home network or phone hotspot, press K3 and choose Add. "
          "It still shows on screen but is never logged or uploaded."),
         ("Press A to add a Wi-Fi or Bluetooth name or address. veh matches every name that starts with veh, and * or "
          "? match any text. A match always alerts.",
          "To add a Wi-Fi or Bluetooth name or address, press K3 and choose Add. veh matches every name that starts "
          "with veh, and * or ? match any text. A match always alerts."),
         ("Open an alert from the wardrive alert list and press N to stop alerts for that device. It is still logged.",
          "To stop alerts for a device, open its alert from the wardrive alert list, press K3 and choose No alerts. "
          "It is still logged.")]


@pytest.fixture(params=["cardputer", "st7735-128", "st7789-240"])
def device(request, monkeypatch):
    """The screen of every app the test makes: the CardputerZero's own, or one of the two Waveshare HATs."""
    if request.param != "cardputer":
        size = int(request.param.rsplit("-", 1)[1])
        for name, value in (("profile", request.param), ("width", size), ("height", size)):
            monkeypatch.setattr(Display, name, value, raising=False)
    return request.param


class Stick:
    """The aircraft side of a wardrive, in the state the test puts it in."""

    state, count = adsb.RUNNING, 0

    def __init__(self, *args, **kwargs):
        pass

    def start(self, epoch):
        pass

    def stop(self):
        pass

    def finish(self):
        return None

    def status(self):
        return {"state": self.state, "aircraft": 0, "detail": adsb.WORDS[self.state]}


def act(app, label):
    """Do what a hint offers: on a HAT through the K3 list, on the CardputerZero with the hint's own key."""
    if app.square is None:
        key = next(key for key, name in app.screen.hints(app) if name == label)
        return press(app, {"Space": "SPACE", "Tab": "TAB", "Del": "BACKSPACE"}.get(key, key.upper()))
    labels = [name for name, _ in app.square.options(app)]
    return press(app, "TAB", *["DOWN"] * labels.index(label), "ENTER")


def look(app):
    """Draw the screen. On a HAT, draw its K3 list too and the wider layout that Pan details shows."""
    app.compose()
    if app.square is not None and "Pan details" in [name for name, _ in app.square.options(app)]:
        act(app, "Pan details")
        press(app, "ESC")


def every_card(app):
    """Each card of the Hardware screen in turn, as a HAT shows them one at a time. Returns what the cards say in
    full, as the CardputerZero cuts a long line short."""
    words = []
    for top, card in enumerate(app.screen.cards(app)):
        app.screen.top = top
        look(app)
        words += [card["title"], card.get("detail", ""), card.get("right", "")]
    return words


def said(device, texts, words):
    """The texts hold every one of the device's own words and none of the other device's. On a HAT none of them
    names the Cardputer or the AppStore, or a key that the HAT does not have."""
    text = " ".join(texts)
    for cardputer, pi in words:
        ours, theirs = (cardputer, pi) if device == "cardputer" else (pi, cardputer)
        assert ours in text and theirs not in text, ours
    if device != "cardputer":
        wrong = [text[max(0, found.start() - 30):found.end() + 30] for pattern in (NAMES, LACKS)
                 for found in pattern.finditer(text)]
        assert not wrong, wrong


def test_the_summary_uploads_and_keys_say_how_to_add_a_key_in_the_words_of_the_device(device, tmp_path, drawn):
    app = make_app(tmp_path / "no keys", splash=True)
    look(app)
    press(app, "ENTER")
    assert isinstance(app.screen, screens.Home)
    app.start_wardrive()
    app.stop_wardrive()
    assert isinstance(app.screen, screens.Summary)
    look(app)
    app.open("Uploads")
    look(app)
    act(app, "Test keys")
    app.open("Keys")
    look(app)
    press(app, "DOWN")
    look(app)
    act(app, "Test")
    assert app.toast_text == "Save the WiGLE key first"
    manual = make_app(tmp_path / "manual", keys=KEYS, auto_upload=False)
    manual.start_wardrive()
    manual.stop_wardrive()
    look(manual)
    said(device, drawn, UPLOADS)


def test_phone_setup_and_its_page_name_the_device_they_run_on(device, tmp_path, drawn):
    offline = make_app(tmp_path / "offline", hw=Hardware(addresses=()))
    offline.open("Keys")
    press(offline, "ENTER")
    assert isinstance(offline.screen, screens.PhoneSetup) and offline.screen.server is None
    look(offline)
    app = make_app(tmp_path / "online")
    app.open("Keys")
    press(app, "ENTER")
    look(app)
    server = app.screen.server
    browser = Browser(server.port)
    pages = [browser.request("GET", "/")[1]]
    browser.request("POST", "/unlock", {"pin": server.pin})
    pages.append(browser.request("GET", "/")[1])
    for _ in range(setup_server.MAX_WRONG_PINS):
        pages.append(Browser(server.port).request("POST", "/unlock", {"pin": "wrong"})[1])
    pages.append(Browser(server.port).request("GET", "/")[1])
    assert server.locked
    press(app, "ESC")
    said(device, drawn, PHONE)
    said(device, [html.unescape(page) for page in pages], PAGES)


def test_the_hardware_screen_names_the_device_for_the_radio_helper_usb_power_and_the_aircraft_radio(device, tmp_path,
                                                                                                    drawn):
    app = make_app(tmp_path, hw=SkyHardware(), deep_scan=True, adsb=True)
    app.hw.power = {"devices": [{"kind": "wifi"}], "hubs": {}, "ma": 450, "peak": 600, "budget": powerguard.PORT_MA,
                    "over": False}
    app.open("Hardware")
    cards, toasts = every_card(app), []
    for state in ("missing", "no_group", "error"):
        helper = functools.partial(deepscan.DeepScan, connect=lambda state=state: (None, state, "it said no"))
        app.wardrive_factory = lambda c, g, helper=helper: Wardrive(c, g, root=tmp_path, adapters=ADAPTERS, ble=NoBle(),
                                                                    fleet=Fleet, clock=lambda: NOW, receiver=Stick,
                                                                    deep=helper)
        app.start_wardrive()
        assert app.drive.status()["deep"]["state"] == state
        app.open("Hardware")
        cards += every_card(app)
        app.stop_wardrive()
    app.start_wardrive()
    app.open("Hardware")
    for state in adsb.WORDS:
        app.drive.aircraft.state = state
        cards += every_card(app)
        if state in (adsb.DENIED, adsb.DRIVER):
            press(app, "A")
            toasts.append(app.toast_text)
    app.stop_wardrive()
    said(device, drawn + cards, HARDWARE)
    said(device, toasts, STICK)


def test_every_setting_and_what_the_radio_helper_needs_are_said_in_the_words_of_the_device(device, tmp_path, drawn,
                                                                                         monkeypatch):
    app = make_app(tmp_path, deep_scan=True, direct_scan=True, radio_tuning=True)
    for state in ("missing", "no_group", "error"):
        monkeypatch.setattr(radio_helper, "state", lambda cfg, diy=False, state=state: (
            state, radio_helper.words(state, "no", diy=diy)))
        app.open("Settings")
        for index, (key, _, _, _) in enumerate(app.screen.items):
            if key is not None:
                app.screen.index = index
                look(app)
    for key in ("ignore", "recon_categories", "drive_alert_types", "drive_watch", "drive_alert_skip", "nodes",
                "flash_node", "check_updates"):
        app.open("Settings")
        app.screen.index = next(i for i, item in enumerate(app.screen.items) if item[0] == key)
        press(app, "ENTER")
        look(app)
    said(device, drawn, HELPER)


def test_the_radio_helper_state_comes_in_the_words_of_the_device(tmp_path):
    def refused(path):
        raise PermissionError(13, "Permission denied")

    def broken(path):
        raise OSError(5, "Input/output error")
    no_access = lambda path: radio_helper.Client(path, connect=refused)
    failing = lambda path: radio_helper.Client(path, connect=broken)
    none = str(tmp_path / "none.sock")
    cardputer, pi = zip(*HELPER)
    for diy, (missing, no_group) in ((False, cardputer), (True, pi)):
        assert radio_helper.state({"deep_scan": True}, path=none, diy=diy) == ("missing", missing)
        assert radio_helper.state({"direct_scan": True}, client=no_access, diy=diy) == ("no_group", no_group)
        assert radio_helper.state({"radio_tuning": True}, client=failing, diy=diy) == (
            "error", "Radio helper failed: [Errno 5] Input/output error")
        assert radio_helper.state({}, diy=diy) == ("off", "Deep scan is off in Settings.")


def test_recon_and_the_alert_lists_name_the_keys_of_the_device(device, tmp_path, drawn):
    app = make_app(tmp_path)
    app.open("Recon")
    look(app)
    act(app, "Start")
    assert app.recon_running
    for number in range(12):
        app.recon.observe("wifi", {"bssid": "AA:BB:CC:00:00:%02X" % number, "ssid": "Network %d" % number,
                                   "rssi": -70, "channel": 1, "frequency": 2412, "security": "OPEN", "band": "2.4"})
    look(app)
    act(app, "All")
    assert app.screen.show_all
    app.stop_recon()
    app.open("Settings")
    for key, kind in (("ignore", screens.IgnoreList), ("drive_watch", recon_screens.MyAlerts),
                      ("drive_alert_skip", recon_screens.SkipList)):
        app.screen.index = next(i for i, item in enumerate(app.screen.items) if item[0] == key)
        press(app, "ENTER")
        assert isinstance(app.screen, kind)
        look(app)
        if kind is not recon_screens.SkipList:
            act(app, "Add")
            assert isinstance(app.screen, screens.TextInput)
            press(app, "ESC")
        press(app, "ESC")
    app.start_wardrive()
    render_screens.flock(app)
    act(app, "Alerts")
    look(app)
    press(app, "ENTER")
    assert isinstance(app.screen, recon_screens.DriveAlertDetail)
    look(app)
    act(app, "No alerts")
    assert app.cfg["drive_alert_skip"] == ["B4:1E:52:3A:10:07"]
    app.stop_wardrive()
    said(device, drawn, LISTS)


def test_the_aircraft_viewer_says_why_it_cannot_listen_in_the_words_of_the_device(device, tmp_path, drawn,
                                                                                  monkeypatch):
    monkeypatch.setattr(skyview.Viewer, "start", lambda viewer: None)
    app = make_app(tmp_path, hw=SkyHardware(), adsb=True)
    app.open("Hardware")
    act(app, "Aircraft")
    assert isinstance(app.screen, sky_screens.AircraftList)
    for state in skyview.WORDS:
        app.sky.state = state
        look(app)
    app.home()
    said(device, drawn, STICK)


def test_no_screen_of_the_screenshots_names_the_cardputer_or_a_key_that_a_hat_does_not_have(tmp_path, drawn):
    for profile, size in render_screens.HATS:
        del drawn[:]
        root = tmp_path / profile
        render_screens.old_logs(root)
        app = render_screens.build(root, render_screens.Hat(profile, size))
        render_screens.tour(app, root, lambda name: look(app))
        app.shutdown()
        assert len(drawn) > 1000
        said(profile, drawn, [])
