"""Whatever is on the air, and whatever goes wrong inside, a wardrive carries on."""

import threading

from PIL import Image, ImageChops, ImageDraw
import pytest

from quartermaster import __version__, config, faults, wifi, wigle
from quartermaster.engine import Wardrive
from quartermaster.names import one_line
from quartermaster.ui import gfx, keys, screens
from tests.fakes import ADAPTERS, FIX, Fleet, Gps, NoBle, networks
from tests.test_core import NOW
from tests.test_ui import make_app, press, screen_text

BROKEN = ("line\nbreak", "tab\there", "\x1b[2Jclear", "nul\x00", " separator", "half\udcffcharacter")


def test_names_are_one_line_in_the_log():
    assert one_line("a\nb\rc\td\x00e\x7ff\x85g h\udcffi") == "a b c d e f g h i"
    for name in BROKEN:
        row = wigle.WigleWriter.row("WIFI", {"mac": "AA:BB:CC:DD:EE:01", "name": name}, -50, FIX, NOW)
        assert row[1].isprintable() and len(row[1]) == len(name)
        row[1].encode("utf-8")


def test_broken_names_cannot_break_a_screen_or_the_log(tmp_path, drawn, monkeypatch):
    found = networks(len(BROKEN))
    for net, name in zip(found, BROKEN):
        net["ssid"] = name
    monkeypatch.setattr(Fleet, "found", found)
    app = make_app(tmp_path)
    press(app, "W")
    text = screen_text(app, drawn)
    assert "line break" in text and "tab here" in text and text.replace("\n", "").isprintable()
    press(app, "ENTER", "ENTER", "ESC", "R", "SPACE", "A")
    app.recon.observe("ble", {"address": "AA:BB:CC:00:00:01", "name": "Flock\nSafety", "rssi": -50})
    assert app.recon_running and "Flock Safety" in screen_text(app, drawn)
    log = app.drive.log_path.read_text(encoding="utf-8")
    assert len(log.splitlines()) == 2 + app.drive.rows and "line break" in log and "\x1b" not in log


def test_cached_text_is_the_picture_pillow_draws():
    cases = (("display", 30, "1,263", "ls", (10, 60)), ("ui", 10, "Queen Anne's Revenge", "la", (29, 40)),
             ("monob", 9, "GPS 14", "ra", (315, 4)), ("bold", 10, "Wardrive saved", "ma", (160, 120.5)),
             ("monob", 8, "ENTER", "mm", (20.5, 160)), ("ui", 9, "Stop + save", "lm", (31.84375, 160)),
             ("mono", 8, "-67", "ra", (-4, -3)), ("ui", 11, "g̀ jpq", "la", (317.25, 164.75)),
             ("ui", 10, "", "la", (5, 5)), ("ui", 10, "below zero", "la", (-2.5, -1.25)))
    for kind, size, text, anchor, xy in cases:
        f = gfx.font(kind, size)
        want = Image.new("RGB", (gfx.W, gfx.H), gfx.SURFACE)
        ImageDraw.Draw(want).text(xy, text, font=f, fill=gfx.GOLD, anchor=anchor)
        for _ in range(2):                                   # drawn once, then pasted from the cache
            c = gfx.Canvas()
            c.rect((0, 0, gfx.W, gfx.H), fill=gfx.SURFACE)
            c.put(xy, text, f, gfx.GOLD, anchor)
            assert not ImageChops.difference(want, c.image).getbbox(), (kind, size, text, anchor, xy)


def test_scan_text_straight_off_the_air_cannot_break_the_parser():
    text = ("BSS aa:bb:cc:dd:ee:01(on wlan1)\n\tfreq: 1e999\n\tsignal: inf dBm\n\tlast seen: %s ms ago\n"
            "\tDS Parameter set: channel 99999\n\tWPS:\t * Version: 1.0\n\t\t * Device name: Living\n"
            "signal: -10.00 dBm\nRoom\n"
            "BSS zz:zz(on wlan1)\n\tsignal: -40.00 dBm\n"
            "BSS aa:bb:cc:dd:ee:02(on wlan1)\n\tfreq: 2412\n\tsignal: -70.00 dBm\n\tsignal: nan dBm\n\tSSID: ok\n"
            "\tRSN:\t * Version: 1\n\t\t * Pairwise ciphers: \x1b[2J,\"\n\t\t * Authentication suites: PSK\n"
            % ("9" * 5000))
    first, second = wifi.parse_iw_scan(text)
    assert (first["frequency"], first["rssi"], first["last_seen_ms"], first["channel"]) == (None, None, None, None)
    assert (second["frequency"], second["rssi"], second["channel"], second["ssid"]) == (2412, -70.0, 1, "ok")
    assert second["capabilities"] == "[WPA2-PSK-CCMP][ESS]"


def test_scan_tool_output_that_is_not_text_is_still_read():
    scanner = wifi.AdapterScanner({"interface": "wlan1"})
    assert scanner._run(["printf", r"\t * Device name: caf\351\n"]).stdout == "\t * Device name: caf�\n"


def test_a_scan_that_fails_unexpectedly_is_noted_and_tried_again(noted):
    heard = threading.Event()

    class Scanner:
        method, sweeps, last_sweep_seconds, last_error, calls = "iw", 0, None, "", 0

        def __init__(self, adapter):
            self.adapter, self.interface = adapter, adapter["interface"]

        def scan(self):
            self.calls += 1
            if self.calls == 1:
                raise KeyError("signal")
            return [{"bssid": "AA:BB:CC:00:00:01"}]
    fleet = wifi.WifiFleet(ADAPTERS[:1], lambda found, adapter: heard.set(), scanner=Scanner)
    fleet.start()
    assert heard.wait(5)
    fleet.stop()
    assert "Wi-Fi scan on wlan1" in noted.read_text() and "KeyError" in noted.read_text()


def test_one_unreadable_network_does_not_hide_the_others(tmp_path, noted, monkeypatch):
    monkeypatch.setattr(Fleet, "found", [{"bssid": "AA:BB:CC:00:00:99"}] + networks(3))
    cfg = config.load(tmp_path / "config.json")
    cfg.update(ble=False)
    drive = Wardrive(cfg, Gps(FIX), root=tmp_path, adapters=ADAPTERS, ble=NoBle(), fleet=Fleet, clock=lambda: NOW)
    drive.start()
    drive._ble_sink([{"address": "not an address", "rssi": -50}, {"address": "AA:BB:CC:00:00:AA"},
                     {"address": "AA:BB:CC:00:00:BB", "rssi": -60, "name": "x" * 400}], {})
    drive.stop()
    status = drive.status()
    assert (status["wifi"], status["ble"], drive.rows) == (3, 1, 4)
    assert "Logging a Wi-Fi network" in noted.read_text() and "Logging a Bluetooth device" in noted.read_text()


def test_errors_are_noted_once_and_the_file_stays_small(noted, monkeypatch):
    for _ in range(3):
        try:
            raise ValueError("half \udcff character")
        except ValueError:
            faults.note("Test")
    text = noted.read_text(encoding="utf-8")
    assert text.count("Quartermaster %s  Test" % __version__) == 1 and "ValueError: half ? character" in text
    monkeypatch.setattr(faults, "LIMIT_BYTES", 10)
    try:
        raise KeyError("another")
    except KeyError:
        faults.note("Test")
    assert "ValueError" not in noted.read_text() and "KeyError" in noted.read_text()


def test_a_failing_screen_goes_back_to_the_menu_and_the_wardrive_carries_on(tmp_path, noted):
    app = make_app(tmp_path)
    press(app, "W")

    class Broken(screens.Screen):
        def render(self, c, app):
            raise ZeroDivisionError("broken screen")
    app.push(Broken())
    frames = []

    def show(image):
        frames.append((type(app.screen), app.wardriving, app.toast_text))
        app.running = False
    app.display.show = show
    app.run()
    assert frames == [(screens.Home, True, "That screen hit a problem. Back at the menu.")]
    assert "Screen Broken" in noted.read_text() and "ZeroDivisionError" in noted.read_text()


def test_a_screen_that_keeps_failing_ends_the_app_and_saves_the_wardrive(tmp_path, noted, monkeypatch):
    app = make_app(tmp_path)
    press(app, "W", "ESC")
    monkeypatch.setattr(screens.Home, "render", lambda self, c, app: 1 / 0)
    with pytest.raises(ZeroDivisionError):
        app.run()
    assert not app.wardriving and app.drive.log_path.exists() and app.drive.rows == 412
    assert noted.read_text().count("Screen Home") == 1


def test_an_unplugged_keyboard_is_dropped_instead_of_spinning():
    board = keys.EvdevInput(["/dev/null"])
    assert len(board.devices) == 1
    assert board.poll(0) == [] and not board.devices
    board.close()
