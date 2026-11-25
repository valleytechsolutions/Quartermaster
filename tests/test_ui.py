import http.client
import json
import os
from pathlib import Path
import shutil
import signal
import threading
import time

import pytest

from quartermaster import accounts, config, flasher, hardware, nodes, radios, recon, updates_check, uploads
from quartermaster.engine import Wardrive
from quartermaster.nodes import NOT_PLUGGED, UNPLUGGED
from quartermaster.recon_engine import Recon
from quartermaster.ui import gfx, recon_screens, screens
from quartermaster.ui.app import App, serial_ports
from quartermaster.ui.keys import ScriptedInput
from tests.fakes import (ADAPTERS, FIX, NO_FIX, Boards, Display, FakeAir, FakeFlasher, FakeHub, FakeNode, Fleet, Gps,
                         Hardware, NoBle, QuietFleet, Silent, Talker, networks, on_a_card, plane, usb_serial)
from tests.test_core import NOW
from tests.test_nodes import AIR1, AIR2, BRIDGE, HUB, HUB_LINK, NATIVE, ONE, THREE, TWO, Rig, node_cfg
from tests.test_uploads import KEYS


def offline():
    return updates_check.OFFLINE, None, "Could not check. Is Wi-Fi connected?"


def make_app(tmp_path, fix=FIX, hw=None, splash=False, keys=None, manager=None, flasher_class=flasher.Flasher,
             updates=offline, **settings):
    cfg = config.load(tmp_path / "config.json")
    cfg.update(dict(ble=False), **settings)
    if keys:
        accounts.save(keys, tmp_path / "accounts.json")
    gps = Gps(fix)
    sent = []

    def sender(service, path, saved, endpoints, **extra):
        sent.append((service, path.name))
        return uploads.Outcome(uploads.DONE, "%s has it" % accounts.SERVICE_NAMES[service])
    uploader = uploads.Uploader(tmp_path, cfg, accounts_path=tmp_path / "accounts.json", sender=sender,
                                clock=lambda: NOW)
    app = App(Display(), ScriptedInput(), gps, cfg, root=tmp_path, splash=splash,
              wardrive=lambda c, g: Wardrive(c, g, root=tmp_path, adapters=ADAPTERS, ble=NoBle(), fleet=Fleet,
                                             clock=lambda: NOW, nodes=manager),
              recon=Recon(cfg, gps, root=tmp_path, adapters=ADAPTERS, ble=NoBle(), fleet=QuietFleet, sound=Silent(),
                          clock=lambda: NOW, nodes=manager),
              nodes=manager, flasher_class=flasher_class, updates=updates,
              uploader=uploader, hw=hw or Boards(), accounts_path=tmp_path / "accounts.json",
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


def placed(app, monkeypatch):
    """What the screen draws now: each text with the height it is drawn at and its colour."""
    found, put = {}, gfx.Canvas.put

    def record(canvas, xy, text, f, fill=gfx.TEXT, anchor="la"):
        found[text] = (xy[1], fill)
        return put(canvas, xy, text, f, fill, anchor)
    with monkeypatch.context() as patch:
        patch.setattr(gfx.Canvas, "put", record)
        app.compose()
    return found


def red_button(picture):
    """True when the button a question has chosen now is a red one."""
    return gfx.RED in {picture.getpixel((x, 119)) for x in range(160, 280)}


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
    app = make_app(tmp_path, keys=KEYS, log_rows="track", auto_upload=True)
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


def test_ok_right_after_deleting_the_last_log_of_the_list_opens_the_one_left(tmp_path):
    app = make_app(tmp_path)
    for _ in range(2):
        press(app, "W", "ESC", "SPACE", "ENTER", "ESC")
    assert len(app.log_files()) == 2
    press(app, "L", "DOWN", "ENTER", "D", "RIGHT", "ENTER", "ENTER")
    assert isinstance(app.screen, screens.LogDetail) and [app.screen.entry] == app.log_files()


def test_remove_right_after_removing_the_last_ignored_network_takes_the_one_left(tmp_path):
    app = make_app(tmp_path, ignore=["Home WiFi", "Phone hotspot"])
    app.push(screens.IgnoreList())
    press(app, "DOWN", "D", "Y", "D")
    assert isinstance(app.screen, screens.Confirm) and app.screen.detail == "Home WiFi will be logged again."
    press(app, "Y", "D", "DOWN")
    assert isinstance(app.screen, screens.IgnoreList) and app.cfg["ignore"] == []


def test_the_logs_list_keeps_its_place_as_logs_go_and_keeps_the_one_being_recorded(tmp_path):
    app = make_app(tmp_path, log_rows="track")
    for _ in range(4):
        press(app, "W", "ESC", "SPACE", "ENTER", "ESC")
    first, second, third, fourth = app.log_files()
    press(app, "L", "DOWN", "ENTER")
    assert isinstance(app.screen, screens.LogDetail) and app.screen.entry == second
    press(app, "ESC", "DOWN", "DOWN", "ENTER", "D", "RIGHT", "ENTER")
    assert isinstance(app.screen, screens.Logs) and (app.screen.index, app.log_files()) == (2, [first, second, third])
    press(app, "ENTER", "D", "RIGHT", "ENTER", "UP", "ENTER")
    assert isinstance(app.screen, screens.LogDetail) and app.screen.entry == first and len(app.log_files()) == 2
    press(app, "D", "RIGHT", "ENTER", "ENTER")
    assert isinstance(app.screen, screens.LogDetail) and [app.screen.entry] == app.log_files() == [second]
    press(app, "D", "RIGHT", "ENTER", "ENTER", "DOWN", "PAGEUP")
    assert isinstance(app.screen, screens.Logs) and app.log_files() == []
    press(app, "ESC", "W", "ESC", "L", "ENTER", "D")
    assert isinstance(app.screen, screens.LogDetail) and app.screen.entry["path"] == app.active_log()
    assert app.toast_text == "Stop the wardrive before deleting its log" and app.active_log().exists()


LOST = "Cannot write the log: No space left on device"


def losing_rows(tmp_path, later=None):
    """A wardrive on a card that takes no more. With `later`, the wardrive has found that out."""
    app = make_app(tmp_path, keys=KEYS)
    press(app, "W")
    on_a_card(app.drive.writer, room=0)
    app.drive._wifi_sink(networks(420)[412:], ADAPTERS[0])
    if later:
        later(5)
        app.drive._sync()
        assert app.drive.failed == LOST
    return app


def test_a_card_that_takes_no_more_is_said_while_the_wardrive_runs_and_when_it_ends(tmp_path, drawn, later):
    app = losing_rows(tmp_path)
    assert "Cannot write" not in screen_text(app, drawn)
    later(5)
    app.drive._sync()
    assert LOST in screen_text(app, drawn)
    press(app, "ENTER")
    text = screen_text(app, drawn).replace("\n", " ")
    assert "The wardrive stops. Cannot write the log: No space left on device." in text and "is saved" not in text
    press(app, "ENTER")
    text = screen_text(app, drawn)
    assert isinstance(app.screen, screens.Summary) and "WARDRIVE ENDED" in text and "WARDRIVE SAVED" not in text
    assert LOST in text and "20261118-110640" in drawn
    assert "Uploading to" not in text and "Nothing" not in text and "420" in drawn and "412" in drawn
    assert app.drive.rows == 412 == len(app.drive.log_path.read_text().splitlines()) - 2


def test_rows_written_again_take_the_red_line_away_and_the_end_still_says_what_was_lost(tmp_path, drawn, later):
    app = losing_rows(tmp_path, later)
    assert LOST in screen_text(app, drawn)
    app.drive._wifi_sink(networks(430)[420:], ADAPTERS[0])
    assert (app.drive.error, app.drive.failed) == ("", LOST) and "Cannot write" not in screen_text(app, drawn)
    press(app, "ENTER")
    assert "The wardrive stops. %s." % LOST in screen_text(app, drawn).replace("\n", " ")
    press(app, "ENTER")
    text = screen_text(app, drawn)
    assert isinstance(app.screen, screens.Summary) and "WARDRIVE ENDED" in text and LOST in text
    assert "20261118-110640  +1 more" in drawn and "422" in drawn and "Uploading to" not in text


def writing_again(tmp_path, later):
    """A wardrive that lost rows to a card that took no more, and has its rows written again since."""
    app = losing_rows(tmp_path, later)
    app.drive._wifi_sink(networks(430)[420:], ADAPTERS[0])
    assert (app.drive.error, app.drive.failed) == ("", LOST)
    return app


def test_a_wardrive_that_writes_again_after_losing_rows_is_not_called_saved_by_recon_or_quitting(
        tmp_path, drawn, later):
    app = writing_again(tmp_path / "recon", later)
    press(app, "ESC", "R", "SPACE")
    assert "The wardrive stops and Recon starts. %s." % LOST in screen_text(app, drawn).replace("\n", " ")
    press(app, "ENTER")
    assert app.recon_running and not app.wardriving and (app.toast_text, app.toast_color) == (LOST, gfx.RED)
    app.shutdown()
    app = writing_again(tmp_path / "quit", later)
    press(app, "ESC", "ESC")
    assert "The scan stops. %s." % LOST in screen_text(app, drawn).replace("\n", " ")
    press(app, "Y")
    assert not app.running and not app.wardriving


def test_quitting_with_recon_running_says_nothing_of_a_wardrive_that_ended_before(tmp_path, drawn, later):
    app = losing_rows(tmp_path, later)
    press(app, "ENTER", "ENTER", "ESC", "R", "SPACE")
    assert app.recon_running and app.drive.failed == LOST
    press(app, "ESC", "ESC")
    text = screen_text(app, drawn).replace("\n", " ")
    assert "The scan stops and its log is saved." in text and "Cannot write" not in text
    press(app, "Y")
    assert not app.running
    app.shutdown()


def test_quitting_a_wardrive_that_is_going_well_saves_it_and_closes(tmp_path, drawn):
    app = make_app(tmp_path, keys=KEYS)
    assert not red_button(press(app, "W", "ENTER"))
    press(app, "ESC", "ESC", "ESC")
    assert "The scan stops and its log is saved." in screen_text(app, drawn).replace("\n", " ")
    assert red_button(press(app, "RIGHT"))
    press(app, "ENTER")
    log = app.drive.log_path
    assert not app.running and not app.wardriving and isinstance(app.screen, screens.Home)
    assert app.drive.rows == 412 == len(log.read_text().splitlines()) - 2
    assert log.with_name(log.name.replace(".wigle.csv", ".summary.csv")).exists()


def test_the_end_screen_puts_the_reason_first_and_is_as_ever_without_one(tmp_path, monkeypatch, later):
    app = make_app(tmp_path / "saved", keys=KEYS, auto_upload=True)
    press(app, "W", "ENTER", "ENTER")
    lines = placed(app, monkeypatch)
    assert lines["Wi-Fi networks"] == (screens.TOP, gfx.MUTED) and lines["Saved as"][0] == screens.TOP + 85
    assert lines["Uploading to WiGLE, WDGWars, Wardrift."] == (screens.TOP + 106, gfx.GREEN)
    app = losing_rows(tmp_path / "lost", later)
    press(app, "ENTER", "ENTER")
    lines = placed(app, monkeypatch)
    assert lines[LOST] == (screens.TOP - 1, gfx.RED)
    assert lines["Wi-Fi networks"][0] == screens.TOP + 12 and lines["Saved as"][0] == screens.TOP + 97
    long = "Cannot write the summary: 'utf-8' codec can't decode byte 0xff in position 0: invalid start byte"
    app.push(screens.Summary(dict(app.drive.status(), failed=long)))
    reason = next(text for text in placed(app, monkeypatch) if text.startswith("Cannot write the summary"))
    assert reason.endswith("…") and gfx.Canvas().text_width(reason, gfx.font("ui", 9)) <= gfx.W - 16


def test_a_wardrive_that_lost_rows_is_not_called_saved_when_recon_or_quitting_ends_it(tmp_path, drawn, later):
    app = losing_rows(tmp_path / "recon", later)
    press(app, "ESC", "R", "SPACE")
    text = screen_text(app, drawn).replace("\n", " ")
    assert isinstance(app.screen, screens.Confirm) and "is saved" not in text
    assert "The wardrive stops and Recon starts. Cannot write the log: No space left on device." in text
    press(app, "ENTER")
    assert app.recon_running and not app.wardriving and (app.toast_text, app.toast_color) == (LOST, screens.RED)
    app.shutdown()
    app = losing_rows(tmp_path / "quit", later)
    press(app, "ESC", "ESC")
    text = screen_text(app, drawn).replace("\n", " ")
    assert "Quit Quartermaster?" in text and "is saved" not in text
    assert "The scan stops. Cannot write the log: No space left on device." in text
    press(app, "Y")
    assert not app.running and not app.wardriving


def test_a_save_that_fails_only_as_the_wardrive_ends_is_said_before_recon_shows_or_quartermaster_closes(
        tmp_path, drawn):
    app = losing_rows(tmp_path / "recon")
    press(app, "ESC", "R", "SPACE")
    assert "The wardrive is saved and Recon starts." in screen_text(app, drawn).replace("\n", " ")
    press(app, "ENTER")
    assert app.recon_running and not app.wardriving and (app.toast_text, app.toast_color) == (LOST, screens.RED)
    app.shutdown()
    app = losing_rows(tmp_path / "quit")
    press(app, "ESC", "ESC")
    assert "The scan stops and its log is saved." in screen_text(app, drawn).replace("\n", " ")
    press(app, "Y")
    text = screen_text(app, drawn)
    assert app.running and not app.wardriving and isinstance(app.screen, screens.Summary) and len(app.stack) == 2
    assert "WARDRIVE ENDED" in text and LOST in text and "412" in drawn
    press(app, "ESC", "ESC")
    assert "Finished logs keep uploading next time." in screen_text(app, drawn)
    press(app, "Y")
    assert not app.running
    earlier = losing_rows(tmp_path / "earlier")
    press(earlier, "ENTER", "ENTER", "ESC", "ESC", "Y")
    assert earlier.drive.failed == LOST and not earlier.running


def test_a_wardrive_that_could_never_start_its_log_ends_with_the_reason(tmp_path, drawn, monkeypatch):
    real, logs = os.fsync, str(tmp_path / "logs")

    def full(fd):
        if os.readlink("/proc/self/fd/%d" % fd).startswith(logs):
            raise OSError(28, "No space left on device")
        return real(fd)
    monkeypatch.setattr(os, "fsync", full)
    app = make_app(tmp_path, keys=KEYS)
    press(app, "W")
    text = screen_text(app, drawn)
    assert "Cannot write the log: No space left on device" in text and app.drive.rows == 0
    press(app, "ENTER", "ENTER")
    text = screen_text(app, drawn)
    assert "WARDRIVE ENDED" in text and "Nothing" in drawn and "GPS" not in text.replace("GPS 14", "")
    assert "Cannot write the log: No space left on device" in text and list((tmp_path / "logs").iterdir()) == []


def test_a_logs_folder_that_cannot_be_used_is_said_and_no_wardrive_starts(tmp_path, monkeypatch):
    app = make_app(tmp_path, log_rows="track")

    def broken(path):
        raise OSError(5, "Input/output error")
    with monkeypatch.context() as patch:
        patch.setattr(shutil, "disk_usage", broken)
        press(app, "W")
    assert app.toast_text == "The logs folder cannot be used: Input/output error" and not app.wardriving
    assert isinstance(app.screen, screens.Home) and app.drive is None and radios.owner() is None
    leftover = tmp_path / "logs" / ".quartermaster_20261117-090000.summary.csv.part"
    others = [leftover.with_name(".quartermaster_20261116-080000.summary.csv" + end) for end in ("", ".part")]
    for path in [leftover] + others:
        path.write_text("")
    real = Path.unlink

    def unlink(self, *args, **kwargs):
        if self == leftover:
            raise OSError(30, "Read-only file system")
        return real(self, *args, **kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(Path, "unlink", unlink)
        press(app, "W")
    assert app.wardriving and isinstance(app.screen, screens.Wardrive)
    assert [path.name for path in (tmp_path / "logs").iterdir() if path.name.startswith(".")] == [leftover.name]


def test_a_stalled_adapter_is_told_once_at_wardrive_start(tmp_path, drawn):
    stalled = [{"usb_id": "0e8d:7961", "label": "MT7921AU tri-band", "port": "1-1.2"}]
    app = make_app(tmp_path, hw=Hardware(stalled=stalled))
    press(app, "W")
    assert "MT7921AU tri-band is not starting. See Hardware." in screen_text(app, drawn)
    assert "not starting" not in app.toast_text


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


def test_rows_per_device_starts_at_closest_and_furthest_and_waits_for_the_wardrive_to_stop(tmp_path, drawn):
    app = make_app(tmp_path)
    assert config.DEFAULTS["log_rows"] == app.cfg["log_rows"] == "extremes"
    press(app, "S")
    settings = app.screen
    settings.index = next(i for i, item in enumerate(settings.items) if item[0] == "log_rows")
    text = screen_text(app, drawn)
    assert "Rows per device" in text and "Closest + furthest" in drawn and "two rows per device" in text
    settings.index += 1
    assert "Full track only." in screen_text(app, drawn)
    settings.index -= 1
    press(app, "ESC", "W")
    assert app.drive.extremes and app.drive.active_log().name.startswith(".")
    press(app, "ESC", "S")
    app.screen.index = settings.index
    press(app, "ENTER")
    assert app.cfg["log_rows"] == "extremes" and "Stop the wardrive" in app.toast_text
    press(app, "ESC", "SPACE", "ENTER", "ESC", "S")
    app.screen.index = settings.index
    press(app, "ENTER")
    assert app.cfg["log_rows"] == config.load(tmp_path / "config.json")["log_rows"] == "track"
    assert "Full track" in screen_text(app, drawn)
    press(app, "ESC", "W")
    assert not app.drive.extremes and app.drive.active_log() == app.drive.log_path


def ends(app, count):
    """Where the cursor of the list on screen goes at its two ends."""
    screen, seen = app.screen, []
    screen.index = screen.top = 0
    for keys in (["PAGEUP"], ["UP"], ["DOWN"], ["PAGEDOWN"] * (count + 2), ["PAGEDOWN"], ["DOWN"], ["DOWN", "DOWN"]):
        press(app, *keys)
        seen.append(screen.index)
    assert app.screen is screen and count > 2
    return seen == [0, count - 1, 0, count - 1, count - 1, 0, 2]


def test_every_list_stops_at_its_ends_for_a_page_and_goes_round_for_a_row(tmp_path):
    app = make_app(tmp_path)
    press(app, "S")
    settings = app.screen
    rows = [i for i, item in enumerate(settings.items) if item[0] is not None]
    assert settings.index == rows[0] and len(rows) < len(settings.items) > 2 * settings.VISIBLE
    press(app, "PAGEUP")
    assert settings.index == rows[0]
    press(app, "PAGEDOWN", "PAGEUP", "PAGEUP", "PAGEUP")
    assert settings.index == rows[0]
    for start in rows:
        for key, page in (("PAGEDOWN", settings.VISIBLE), ("PAGEUP", -settings.VISIBLE)):
            settings.index = start
            press(app, key)
            landed = max(0, min(len(settings.items) - 1, start + page))
            assert settings.index == (landed if landed in rows else landed + 1), (start, key)
    press(app, *["PAGEUP"] * len(rows))
    seen = []
    for _ in rows:
        press(app, "UP")
        seen.append(settings.index)
    assert seen == rows[::-1]
    press(app, *["PAGEDOWN"] * len(rows))
    assert settings.index == rows[-1]
    seen = []
    for _ in rows:
        press(app, "DOWN")
        seen.append(settings.index)
    assert seen == rows
    press(app, "ESC", "K", "PAGEUP")
    last = len(accounts.FIELDS)
    assert isinstance(app.screen, screens.Keys) and app.screen.index == 0
    press(app, "PAGEDOWN")
    assert app.screen.index == min(last, app.screen.VISIBLE)
    press(app, "PAGEDOWN", "PAGEDOWN")
    assert app.screen.index == last
    press(app, "DOWN")
    assert app.screen.index == 0
    press(app, "UP")
    assert app.screen.index == last
    press(app, "ESC")
    for _ in range(9):
        press(app, "W", "ESC", "SPACE", "ENTER", "ESC")
    press(app, "L")
    assert isinstance(app.screen, screens.Logs) and len(app.log_files()) == 9 > app.screen.VISIBLE
    assert ends(app, 9) and app.screen.top == 0
    app.change("ignore", ["Network %d" % number for number in range(13)])
    app.push(screens.IgnoreList())
    assert ends(app, 13) and app.screen.top == 0
    press(app, "ESC", "ESC", "S")
    settings = app.screen
    for name, kind, count in (("recon_alert_min", screens.Picker, len(recon.CONFIDENCE_BY_KEY)),
                              ("recon_categories", recon_screens.Categories, len(recon.CATEGORIES))):
        settings.index = next(i for i, item in enumerate(settings.items) if item[0] == name)
        press(app, "ENTER")
        assert isinstance(app.screen, kind) and ends(app, count), name
        press(app, "ESC")
    press(app, "ESC", "R", "SPACE", "A")
    for number in range(12):
        app.recon.observe("wifi", {"bssid": "AA:BB:CC:00:00:%02X" % number, "ssid": "Network %d" % number,
                                   "rssi": -70, "channel": 1, "frequency": 2412, "security": "OPEN", "band": "2.4"})
    assert isinstance(app.screen, recon_screens.Recon) and app.screen.show_all and ends(app, 12)


def test_recon_lists_devices_and_never_overlaps_a_wardrive(tmp_path, drawn, monkeypatch):
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
    app.recon.unseen_alerts = 2
    text = screen_text(app, drawn)
    assert "Body-camera candidate  +1" in text and "Del hide" in text
    press(app, "BACKSPACE")
    text = screen_text(app, drawn)
    assert isinstance(app.screen, screens.Home) and "Del hide" not in text and "Body-camera" not in text
    press(app, "H", "R")
    assert isinstance(app.screen, screens.Hardware)
    press(app, "ESC", "W")
    assert isinstance(app.screen, screens.Confirm) and not app.wardriving
    press(app, "ENTER")
    assert app.wardriving and not app.recon_running and radios.owner() == "wardrive"
    press(app, "ESC", "R", "SPACE")
    assert isinstance(app.screen, screens.Confirm)
    press(app, "ENTER")
    assert app.recon_running and not app.wardriving and radios.owner() == "recon"
    assert (app.toast_text, app.toast_color) == ("Recon started", gfx.GREEN)
    assert len(list(tmp_path.glob("logs/*.wigle.csv"))) == 1
    press(app, "SPACE")
    assert not app.recon_running and radios.owner() is None and "Recon stopped. 0 devices flagged" in app.toast_text

    def full():
        raise OSError(28, "No space left on device", str(tmp_path / "recon"))
    monkeypatch.setattr(app.recon, "start", full)
    press(app, "SPACE")
    assert app.toast_text == "Recon could not start: No space left on device" and not app.recon_running


def test_recon_that_cannot_start_says_why_and_leaves_its_screen_open(tmp_path, monkeypatch):
    app = make_app(tmp_path)
    (tmp_path / "recon").write_text("")
    press(app, "W", "ESC", "R")
    asked = app.screen
    press(app, "SPACE", "ENTER")
    assert not app.wardriving and not app.recon_running and radios.owner() is None and app.screen is asked
    assert (app.toast_text, app.toast_color) == ("Recon could not start: File exists", gfx.RED)
    radios.claim("wardrive")
    press(app, "SPACE")
    assert (app.toast_text, app.toast_color) == ("Stop the wardrive first", gfx.RED) and app.screen is asked
    wrong = [RuntimeError(), OSError("the card is gone")]

    def refuse():
        raise wrong.pop(0)
    monkeypatch.setattr(app.recon, "start", refuse)
    press(app, "SPACE")
    assert app.toast_text == "Recon could not start"
    press(app, "SPACE")
    assert app.toast_text == "Recon could not start: the card is gone" and not app.recon_running


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


class Typist(ScriptedInput):
    """Keys that wait to be read, as they do while the screen thread is busy, and can be forgotten."""

    def flush(self):
        del self.queue[:]


def test_keys_pressed_while_the_wardrive_is_saved_are_not_acted_on(tmp_path):
    app = make_app(tmp_path, keys=KEYS)
    app.keyboard = Typist()
    press(app, "W", "ENTER")
    press(app, "ENTER", "ESC", "ESC", "Y")
    assert app.running and not app.wardriving and isinstance(app.screen, screens.Summary)


def test_a_key_after_quit_in_the_same_read_is_not_acted_on(tmp_path):
    app = make_app(tmp_path)

    class Burst(ScriptedInput):
        def poll(self, timeout):
            keys, self.queue = self.queue, []
            return keys
    app.keyboard = Burst()
    press(app, "ESC", "Y", "W")
    assert not app.running and not app.wardriving


def test_quitting_does_not_start_an_upload_it_would_cut_off(tmp_path):
    app = make_app(tmp_path, keys=KEYS)
    kicks = []
    app.uploader.kick = lambda: kicks.append(1)
    press(app, "W", "ENTER", "ENTER")
    assert kicks == [1]
    press(app, "ESC", "W")
    assert app.wardriving
    press(app, "ESC", "ESC", "Y")
    assert not app.running and not app.wardriving and kicks == [1]


def test_a_new_reason_found_as_the_wardrive_ends_is_shown_before_quartermaster_closes(tmp_path, drawn, later):
    app = losing_rows(tmp_path, later)
    press(app, "ESC", "ESC")
    assert "The scan stops. %s." % LOST in screen_text(app, drawn).replace("\n", " ")
    other = "Cannot write the log: Input/output error"
    app.drive.failed = other
    press(app, "Y")
    assert app.running and isinstance(app.screen, screens.Summary) and other in screen_text(app, drawn)


def test_a_wardrive_without_a_log_is_never_called_saved(tmp_path, drawn):
    app = make_app(tmp_path, fix=NO_FIX)
    press(app, "W")
    assert app.wardriving and app.drive.log_path is None
    press(app, "ESC", "ESC")
    assert "The scan stops. Nothing has been logged yet." in screen_text(app, drawn).replace("\n", " ")
    press(app, "ESC", "R", "SPACE")
    text = screen_text(app, drawn).replace("\n", " ")
    assert "The wardrive stops and Recon starts. Nothing has been logged yet." in text and "saved" not in text
    press(app, "ESC", "ESC", "W", "ENTER")
    text = screen_text(app, drawn).replace("\n", " ")
    assert "The wardrive stops. Nothing has been logged yet." in text and "saved" not in text


@pytest.fixture
def ports_let_go():
    yield
    hardware._held.clear()
    hardware._marks.clear()


def node_app(tmp_path, ports=None, configured=(), **settings):
    """An app with nodes on fake USB ports: by default ONE on its own port with the firmware that came with
    Quartermaster, and TWO, a Stamp-C5 with older firmware, behind a bridge."""
    if ports is None:
        stamp = FakeNode(TWO, board="stamp-c5", caps="wifi24+wifi5", flash_mb=4, psram_mb=0)
        ports = {"ttyACM0": ("303a:1001", NATIVE, FakeNode(ONE, firmware=screens.bundled_firmware())),
                 "ttyUSB1": ("1a86:55d4", BRIDGE, stamp)}
    cfg = node_cfg(*configured)
    rig = Rig(tmp_path / "bus", ports, cfg)
    settings.setdefault("country", "US")
    settings.setdefault("country_set", True)
    app = make_app(tmp_path, manager=rig.manager, hw=Boards(serial_ports(rig.sys_tty, rig.dev, rig.by_id)),
                   nodes=cfg["nodes"], **settings)
    rig.manager.scan()
    rig.settle()
    return app, rig


def open_setting(app, name):
    app.home()
    press(app, "S")
    app.screen.index = next(i for i, item in enumerate(app.screen.items) if item[0] == name)
    press(app, "ENTER")


def finish(app):
    deadline = time.monotonic() + 5
    while app.flashing() and time.monotonic() < deadline:
        time.sleep(0.005)
    app.step(0)


def test_a_new_node_is_said_once_and_added_with_the_name_of_its_board(tmp_path, drawn, later, ports_let_go):
    app, rig = node_app(tmp_path)
    press(app)
    assert app.toast_text == "New node found. Settings > Nodes to add it."
    app.toast_text = ""
    one = rig.devices["ttyACM0"]
    one.unplug()
    rig.settle()
    one.replug()
    rig.clock.advance(5)
    rig.manager.scan()
    later(2)
    press(app)
    assert rig.manager.node(ONE)["connected"] and app.toast_text == ""
    open_setting(app, "nodes")
    assert isinstance(app.screen, screens.Nodes)
    text = screen_text(app, drawn)
    assert "New node found" in text and "ESP32-C5 E4F5" in text and "Stamp-C5 E4F6" in text and "Add" in text
    press(app, "ENTER")
    assert config.load(tmp_path / "config.json")["nodes"] == [{"id": ONE, "name": "ESP32-C5 E4F5", "ble": False}]
    assert app.toast_text == "ESP32-C5 E4F5 added"
    text = screen_text(app, drawn)
    assert "Ready" in text and "Open" in text
    press(app, "DOWN", "ESC")
    assert "1 node, 1 new" in screen_text(app, drawn)
    _, dev, _ = usb_serial(tmp_path / "bus", {"ttyACM2": ("303a:1001", "")})
    rig.bus.add(dev / "ttyACM2", FakeNode(THREE, clock=rig.clock))
    rig.manager.scan()
    later(2)
    press(app)
    assert app.toast_text == "New node found. Settings > Nodes to add it."


def test_the_nodes_list_says_how_each_node_is(tmp_path, drawn, ports_let_go):
    app, rig = node_app(tmp_path, configured=(ONE, TWO, THREE), ble=True)
    open_setting(app, "nodes")
    assert {"N0", "N1", "N2", "Ready", "Old firmware", "Not plugged in"} <= set(drawn)
    press(app, "ESC", "ESC", "W")
    rig.settle(3)
    open_setting(app, "nodes")
    words = screen_text(app, drawn).split("\n")
    assert words.count("Scanning") == 2 and "Not plugged in" in words and "Old firmware" not in words
    app.nodes = Rig(tmp_path / "empty", {}).manager
    assert "No nodes yet" in screen_text(app, drawn)


def test_a_node_is_renamed_given_bluetooth_identified_and_removed_from_its_screen(tmp_path, drawn, ports_let_go):
    app, rig = node_app(tmp_path, configured=(ONE,), ble=True)
    one = rig.devices["ttyACM0"]
    press(app, "W")
    rig.settle(3)
    open_setting(app, "nodes")
    press(app, "DOWN", "ENTER")
    assert isinstance(app.screen, screens.NodeDetail)
    text = screen_text(app, drawn)
    assert "Scanning" in text and "ch 1 2 3" in text and "chip 1.0" in text and "8 MB flash" in text
    assert "Update firmware" not in text and "Bluetooth" in text and "Off" in drawn
    press(app, "ENTER", "BACKSPACE", "BACKSPACE")
    typed(app, "Mast")
    press(app, "ENTER")
    assert config.load(tmp_path / "config.json")["nodes"][0]["name"] == "Mast" and "Mast" in screen_text(app, drawn)
    press(app, "DOWN")
    assert "Bluetooth halves the node's time for Wi-Fi." in screen_text(app, drawn)
    assert not one.plan["ble"]
    press(app, "ENTER")
    rig.settle()
    assert one.plan["ble"] and config.load(tmp_path / "config.json")["nodes"][0]["ble"] is True
    press(app, "DOWN")
    assert "Not set" in screen_text(app, drawn) and "Which light the board has. Identify needs it." in drawn
    press(app, "DOWN", "ENTER")
    rig.settle()
    assert one.idents == [] and app.toast_text == "Set its light first"
    press(app, "UP", "ENTER")
    assert isinstance(app.screen, screens.Picker) and app.screen.index == 0
    press(app, "DOWN", "ENTER")
    rig.settle()
    assert one.led == ["ws2812", "27"] and config.load(tmp_path / "config.json")["nodes"][0]["led"] == "ws2812,27"
    assert "DevKitC" in screen_text(app, drawn)
    press(app, "DOWN", "ENTER")
    rig.settle()
    assert one.idents == [5] and app.toast_text == "Its light blinks for 5 seconds"
    one.unplug()
    rig.settle()
    press(app, "ENTER")
    assert app.toast_text == "It is not plugged in" and "Not plugged in" in screen_text(app, drawn)
    press(app, "DOWN", "ENTER")
    assert isinstance(app.screen, screens.Confirm)
    press(app, "ENTER")
    assert isinstance(app.screen, screens.NodeDetail) and len(app.cfg["nodes"]) == 1
    press(app, "ENTER", "RIGHT", "ENTER")
    assert isinstance(app.screen, screens.Nodes) and app.toast_text == "Mast removed"
    assert config.load(tmp_path / "config.json")["nodes"] == [] and rig.manager.known() == []


def test_a_node_with_old_firmware_is_let_go_for_its_update_and_taken_back(tmp_path, drawn, ports_let_go):
    log = []
    app, rig = node_app(tmp_path, configured=(TWO,))
    path = str(rig.by_id / BRIDGE)
    app.flasher_class = FakeFlasher(log, error=flasher.LOST, during=lambda: (TWO in rig.manager.links,
                                                                             hardware.port_holder(path)))
    open_setting(app, "nodes")
    press(app, "DOWN", "ENTER")
    text = screen_text(app, drawn)
    assert "Old firmware" in text and "Update firmware" in text and "1.0.0 to %s" % screens.bundled_firmware() in text
    press(app, "DOWN", "DOWN", "DOWN", "DOWN", "ENTER")
    assert isinstance(app.screen, screens.Picker) and app.screen.options[app.screen.index][0] == "update"
    press(app, "ENTER")
    finish(app)
    assert log == [("open", path), ("connect", False, (False, "node")), ("close",)]
    assert flasher.LOST in screen_text(app, drawn).replace("\n", " ") and rig.manager.away == {}
    app.flasher_class.error = ""
    del log[:]
    press(app, "ENTER")
    finish(app)
    assert log == [("open", path), ("connect", False, (False, "flash")), ("install", "update"), ("close",)]
    assert rig.manager.away == {} and hardware.port_holder(str(rig.by_id / BRIDGE)) is None
    text = screen_text(app, drawn)
    assert "Done. It starts again as a node by itself." in text and "100 %" in text and "Then add it" not in text
    press(app, "ENTER")
    assert isinstance(app.screen, screens.NodeDetail)
    rig.manager.scan()
    assert rig.manager.node(TWO)["connected"]


def test_a_board_without_node_firmware_is_flashed_with_progress_plain_errors_and_boot_help(
        tmp_path, drawn, ports_let_go):
    log, gate = [], threading.Event()
    gps = "usb-Silicon_Labs_CP2102N_USB_to_UART_Bridge_Controller_0001-if00-port0"
    app, rig = node_app(tmp_path, ports={"ttyACM0": ("303a:1001", NATIVE, Talker(b"")),
                                         "ttyUSB0": ("10c4:ea60", gps, Talker())})
    hardware.mark_port(rig.path("ttyUSB0"), "gps")
    path = str(rig.by_id / NATIVE)
    fake = FakeFlasher(log, error=flasher.NO_ANSWER, during=lambda: hardware.port_holder(path))
    app.flasher_class = fake
    assert hardware.port_holder(path) is None and rig.manager.found() == []
    open_setting(app, "flash_node")
    text = screen_text(app, drawn)
    assert "ESP32-C5 on ttyACM0" in text and "No node firmware" in text and "Update keeps its settings." in text
    assert "ttyUSB0" not in text and [port["name"] for port in app.hw.serial_ports] == ["ttyACM0", "ttyUSB0"]
    press(app, "ENTER")
    assert isinstance(app.screen, screens.Confirm) and app.screen.choice == 1
    assert "erases the board" in app.screen.detail
    press(app, "ENTER")
    finish(app)
    assert log[:2] == [("open", path), ("connect", True, "flash")] and hardware.port_holder(path) is None
    text = screen_text(app, drawn)
    assert "It did not work" in text and flasher.NO_ANSWER in text.replace("\n", " ") and "Try again" in text
    assert all(line in text for line in screens.BOOT_HELP)
    fake.error, fake.gate = "", gate
    press(app, "ENTER")
    deadline = time.monotonic() + 5
    while app.flash_job.state != "writing" and time.monotonic() < deadline:
        time.sleep(0.005)
    press(app, "ESC")
    assert isinstance(app.screen, screens.Flashing) and "Wait until it is done" in app.toast_text
    assert "50 %" in screen_text(app, drawn) and "Do not unplug the board." in drawn
    gate.set()
    finish(app)
    text = screen_text(app, drawn)
    assert ("install", "first") in log and "Then add it under Settings > Nodes." in text and "100 %" in text
    fake.error, fake.at, fake.gate = flasher.MISMATCH, "install", None
    press(app, "ESC", "ENTER", "ENTER")
    finish(app)
    text = screen_text(app, drawn)
    assert flasher.MISMATCH in text.replace("\n", " ") and screens.BOOT_HELP[0] not in text


def test_flashing_waits_until_no_wardrive_or_recon_runs_and_says_why(tmp_path, drawn, ports_let_go):
    log = []
    app, rig = node_app(tmp_path, configured=(TWO,))
    app.flasher_class = FakeFlasher(log)
    press(app, "W")
    open_setting(app, "flash_node")
    text = screen_text(app, drawn)
    assert "Stop the wardrive first" in text and "cannot happen while Quartermaster scans" in text
    press(app, "ENTER")
    assert isinstance(app.screen, screens.FlashTargets)
    open_setting(app, "nodes")
    press(app, "DOWN", "ENTER", "DOWN", "DOWN", "DOWN", "DOWN", "ENTER")
    assert app.toast_text == "Stop the wardrive first. Flashing restarts the board."
    assert isinstance(app.screen, screens.NodeDetail)
    app.flash({"node": TWO, "path": "/dev/ttyUSB1", "native": False, "title": "N0", "mode": "update",
               "configured": True}, "update")
    assert app.flash_job is None and log == []
    press(app, "ESC", "ESC", "ESC", "SPACE", "ENTER", "ESC", "R", "SPACE")
    assert app.recon_running
    open_setting(app, "flash_node")
    assert "Stop Recon first" in screen_text(app, drawn)
    press(app, "ESC", "ESC", "R", "SPACE")
    assert not app.recon_running
    open_setting(app, "flash_node")
    assert "N0" in screen_text(app, drawn)


def test_the_hardware_and_wardrive_screens_show_the_nodes(tmp_path, drawn, ports_let_go):
    app, rig = node_app(tmp_path, configured=(ONE, THREE))
    assert "4 radios" in screen_text(app, drawn)
    press(app, "H", "DOWN", "DOWN", "DOWN")
    text = screen_text(app, drawn)
    assert "N0  ESP32-C5" in text and "Ready. It scans in the next wardrive or Recon." in text
    assert "N1  ESP32-C5" in text and "Not plugged in" in text
    assert "New node found  Stamp-C5 E4F6" in text and "Add it under Settings > Nodes." in text
    press(app, "ESC", "W")
    rig.settle(5)
    assert "3 radios + 1 node" in screen_text(app, drawn)
    press(app, "ESC", "H", "DOWN", "DOWN", "DOWN")
    text = screen_text(app, drawn)
    assert "ch 1 2 3 4" in text and "2 sweeps" in text


def test_the_country_is_locked_while_a_wardrive_runs_and_shares_the_channels_again_in_recon(tmp_path, ports_let_go):
    app, rig = node_app(tmp_path, configured=(ONE,))
    one = rig.devices["ttyACM0"]
    open_setting(app, "country")
    assert isinstance(app.screen, screens.Picker) and len(app.screen.options) == len(config.COUNTRIES)
    press(app, *["DOWN"] * 6 + ["ENTER"])
    assert app.cfg["country"] == config.load(tmp_path / "config.json")["country"] == "JP" == rig.manager.country
    press(app, "ESC", "W")
    open_setting(app, "country")
    assert app.cfg["country"] == "JP" and "Stop the wardrive" in app.toast_text
    press(app, "ESC", "SPACE", "ENTER", "ESC", "R", "SPACE")
    rig.settle()
    assert one.plan["country"] == "JP"
    open_setting(app, "country")
    press(app, "UP", "UP", "UP", "UP", "UP", "UP", "ENTER")
    rig.settle()
    assert app.cfg["country"] == "US" and one.plan["country"] == "US"


def test_check_for_updates_asks_in_the_background_and_says_what_to_do(tmp_path, drawn):
    gate, asked = threading.Event(), []

    def opener(url, current):
        asked.append(url)
        gate.wait(5)
        return b'{"apps": [{"id": "quartermaster", "package": "quartermaster", "version": "1.1.1"}]}'
    app = make_app(tmp_path, updates=lambda: updates_check.check("1.1.0", opener))
    open_setting(app, "check_updates")
    assert isinstance(app.screen, screens.Updates) and "Checking…" in screen_text(app, drawn)
    press(app, "ENTER")
    gate.set()
    deadline = time.monotonic() + 5
    while app.update[0] == "busy" and time.monotonic() < deadline:
        time.sleep(0.005)
    text = screen_text(app, drawn)
    assert "1.1.1 is ready. Open the AppStore and press 7 to update it." in text and asked == [updates_check.REGISTRY]
    app.updates = offline
    press(app, "ENTER")
    deadline = time.monotonic() + 5
    while app.update[0] == "busy" and time.monotonic() < deadline:
        time.sleep(0.005)
    assert "Could not check. Is Wi-Fi connected?" in screen_text(app, drawn)


def test_the_hardware_check_lists_the_ports_an_esp32_c5_board_may_be_on(tmp_path, monkeypatch):
    from quartermaster import power
    from quartermaster.ui import app as app_module
    sys_tty, dev, by_id = usb_serial(tmp_path, {"ttyUSB0": ("1546:01a7", ""), "ttyUSB1": ("1a86:55d4", BRIDGE),
                                                "ttyACM0": ("303a:1001", NATIVE)})
    found = serial_ports(sys_tty, dev, by_id)
    assert [(port["name"], port["path"], port["native"]) for port in found] == [
        ("ttyACM0", str(by_id / NATIVE), True), ("ttyUSB1", str(by_id / BRIDGE), False)]
    monkeypatch.setattr(power, "battery", lambda: None)
    for name in ("wifi_adapters", "bluetooth_adapters", "gps_candidates", "stalled_usb_adapters"):
        monkeypatch.setattr(hardware, name, lambda: [])
    monkeypatch.setattr(app_module, "serial_ports", lambda: found)
    watch = app_module.HardwareWatch()
    watch.refresh()
    assert watch.serial_ports == found


def wait_for(test):
    deadline = time.monotonic() + 5
    while not test() and time.monotonic() < deadline:
        time.sleep(0.005)
    assert test()


def test_a_board_the_gps_search_is_trying_is_listed_and_flashed_once_the_gps_lets_go_of_it(
        tmp_path, drawn, ports_let_go, monkeypatch):
    from quartermaster.ui import app as app_module
    log = []
    app, rig = node_app(tmp_path, ports={"ttyUSB0": ("1a86:55d4", BRIDGE, Talker(b""))})
    path = str(rig.by_id / BRIDGE)
    hardware.mark_port(path, "checked")
    assert hardware.hold_port(path, "gps")
    app.flasher_class = FakeFlasher(log, during=lambda: hardware.port_holder(path))
    open_setting(app, "flash_node")
    assert "USB serial on ttyUSB0" in screen_text(app, drawn)
    press(app, "ENTER", "ENTER")
    wait_for(lambda: app.flash_job.waiting)
    assert "Waiting for the port…" in screen_text(app, drawn) and log == []
    hardware.let_go_port(path, "gps")
    finish(app)
    assert log == [("open", path), ("connect", False, "flash"), ("install", "first"), ("close",)]
    assert hardware.port_holder(path) is None
    monkeypatch.setattr(app_module, "PORT_WAIT", 0.05)
    del log[:]
    assert hardware.hold_port(path, "gps")
    press(app, "ESC", "ENTER", "ENTER")
    finish(app)
    assert app_module.BUSY in screen_text(app, drawn).replace("\n", " ") and log == []
    assert hardware.port_holder(path) == "gps"


def test_a_board_that_turns_out_to_be_a_node_is_flashed_as_one(tmp_path, ports_let_go):
    log = []
    app, rig = node_app(tmp_path)
    path = rig.manager.node(ONE)["port"]
    app.flasher_class = FakeFlasher(log, during=lambda: (ONE in rig.manager.links, hardware.port_holder(path)))
    app.flash({"node": None, "path": path, "native": True, "title": "ESP32-C5 on ttyACM0", "mode": "first",
               "configured": False}, "first")
    finish(app)
    assert log == [("open", path), ("connect", True, (False, "node")), ("install", "first"), ("close",)]
    assert app.flash_job.state == "done" and rig.manager.away == {}


def test_a_node_behind_a_bridge_is_flashed_as_one_before_the_hardware_check_has_seen_its_port(
        tmp_path, ports_let_go):
    log = []
    app, rig = node_app(tmp_path, configured=(TWO,))
    app.hw.serial_ports = []
    app.flasher_class = FakeFlasher(log)
    target = next(target for target in screens.flash_targets(app) if target["node"] == TWO)
    app.flash(target, "update")
    finish(app)
    assert log[1][:2] == ("connect", False) and app.flash_job.state == "done"


def test_a_node_with_newer_firmware_asks_for_a_newer_quartermaster_and_is_never_offered_an_older_one(
        tmp_path, drawn, ports_let_go):
    bundled = screens.bundled_firmware()
    ports = {"ttyACM0": ("303a:1001", NATIVE, FakeNode(ONE, firmware="9.0.0", protocol=2)),
             "ttyUSB1": ("1a86:55d4", BRIDGE, FakeNode(TWO, firmware=bundled, protocol=0))}
    app, rig = node_app(tmp_path, ports=ports, configured=(ONE, TWO))
    one, two = rig.manager.node(ONE), rig.manager.node(TWO)
    assert screens.node_word(one)[0] == "Newer firmware" and screens.node_word(two)[0] == "Old firmware"
    assert "update" not in [action for action, _, _ in screens.NodeDetail.actions(one)]
    assert ("update", "Update firmware", "%s to %s" % (bundled, bundled)) in screens.NodeDetail.actions(two)
    details = {target["node"]: target["detail"] for target in screens.flash_targets(app)}
    assert details[ONE] == "Up to date" and details[TWO] == "%s to %s" % (bundled, bundled)
    open_setting(app, "nodes")
    press(app, "ENTER")
    assert "Update Quartermaster to use this node" in screen_text(app, drawn) and "Update firmware" not in drawn


def test_the_new_node_toast_waits_while_the_nodes_list_is_open_and_never_comes_for_a_node_seen_there(
        tmp_path, later, ports_let_go):
    app, rig = node_app(tmp_path)
    app._booted = True
    app.push(screens.Nodes())
    app.step(0)
    assert app.toast_text == "" and rig.manager.found()
    press(app, "ESC")
    later(2)
    app.step(0)
    assert app.toast_text == ""


def test_the_nodes_are_looked_for_while_the_app_runs_and_let_go_when_it_ends(tmp_path, monkeypatch, ports_let_go):
    app, rig = node_app(tmp_path)
    calls = []
    monkeypatch.setattr(rig.manager, "start", lambda: calls.append("start"))
    monkeypatch.setattr(rig.manager, "stop", lambda: calls.append("stop"))
    app.running = False
    app.run()
    assert calls == ["start", "stop"]


def test_one_board_is_flashed_at_a_time_and_quitting_waits_for_it(tmp_path, ports_let_go):
    from quartermaster.ui import app as app_module
    log, gate = [], threading.Event()
    app, rig = node_app(tmp_path, configured=(ONE, TWO))
    app.hw = Boards(app.hw.serial_ports, battery={"percent": 1, "charging": False})
    app.flasher_class = FakeFlasher(log, gate=gate)
    target = next(target for target in screens.flash_targets(app) if target["node"] == TWO)
    app.flash(target, "update")
    job = app.flash_job
    wait_for(lambda: job.state == "writing")
    assert screens.node_word(rig.manager.node(TWO)) == ("Updating", gfx.GOLD)
    app.flash(target, "update")
    assert app.flash_job is job and app.toast_text == "Another board is being flashed"
    app.ask_quit()
    assert app.toast_text == "Wait until the board is flashed" and isinstance(app.screen, screens.Flashing)
    app._booted, app._closing = True, time.monotonic() - 1
    app.step(0)
    assert app.running and app.toast_text == app_module.FLASH_LAST
    gate.set()
    finish(app)
    app._closing = time.monotonic() - 1
    app.step(0)
    assert not app.running
    gate.clear()
    rig.manager.scan()
    app.flash(target, "update")
    wait_for(lambda: app.flash_job.state == "writing")
    threading.Timer(0.1, gate.set).start()
    app.shutdown()
    assert app.flash_job.state == "done"


def test_bluetooth_flips_once_per_press_and_the_side_keys_do_nothing_on_the_other_rows(
        tmp_path, drawn, ports_let_go):
    from quartermaster.ui.keys import Key
    app, rig = node_app(tmp_path, configured=(ONE,))
    one = rig.devices["ttyACM0"]
    open_setting(app, "nodes")
    press(app, "DOWN", "ENTER", "DOWN")
    app.keyboard.queue.extend([Key("RIGHT"), Key("RIGHT", repeat=True)])
    press(app)
    assert rig.manager.node(ONE)["ble"] is True
    press(app, "DOWN", "LEFT", "RIGHT", "DOWN", "RIGHT")
    rig.settle()
    assert one.idents == [] and isinstance(app.screen, screens.NodeDetail)


def test_a_node_that_was_unplugged_says_so_once_on_its_screen(tmp_path, drawn, ports_let_go):
    app, rig = node_app(tmp_path, configured=(ONE,))
    open_setting(app, "nodes")
    press(app, "DOWN", "ENTER")
    rig.devices["ttyACM0"].unplug()
    rig.settle()
    assert rig.manager.node(ONE)["error"] in (UNPLUGGED, NOT_PLUGGED)
    text = screen_text(app, drawn)
    assert text.count("plugged") == 1 and "Channels are shared out when a scan starts" in text


def test_a_wardrive_with_only_nodes_counts_them_as_nodes(tmp_path, drawn, ports_let_go):
    app, rig = node_app(tmp_path, configured=(ONE,))
    app.wardrive_factory = lambda c, g: Wardrive(c, g, root=tmp_path, adapters=[], ble=NoBle(), fleet=Fleet,
                                                 clock=lambda: NOW, nodes=rig.manager)
    press(app, "W")
    rig.settle(3)
    text = screen_text(app, drawn)
    assert "1 node" in drawn and "radio" not in text


def test_an_update_check_that_hangs_or_breaks_says_it_could_not_check(tmp_path, drawn, monkeypatch):
    from quartermaster.ui import app as app_module
    gate = threading.Event()

    def hangs():
        gate.wait(5)
        return updates_check.LATEST, "1.1.0", "You have the latest version (1.1.0)."
    app = make_app(tmp_path, updates=hangs)
    open_setting(app, "check_updates")
    assert "Checking…" in screen_text(app, drawn)
    monkeypatch.setattr(app_module, "UPDATE_SECONDS", 0)
    assert updates_check.OFFLINE_TEXT in screen_text(app, drawn)

    def breaks():
        raise RuntimeError("boom")
    app.updates = breaks
    press(app, "ENTER")
    gate.set()
    wait_for(lambda: app.update[0] != "busy")
    time.sleep(0.05)
    assert app.update == (updates_check.UNKNOWN, updates_check.UNKNOWN_TEXT)
    assert updates_check.UNKNOWN_TEXT in screen_text(app, drawn)


def test_a_check_that_answers_after_a_newer_one_is_not_shown(tmp_path, monkeypatch):
    from quartermaster.ui import app as app_module
    gate, answers = threading.Event(), []

    def slow():
        answers.append("slow")
        gate.wait(5)
        return updates_check.GITHUB, "1.1.1", "1.1.1 is on GitHub. It reaches the AppStore after review."
    app = make_app(tmp_path, updates=slow)
    monkeypatch.setattr(app_module, "UPDATE_SECONDS", 0)
    app.check_updates()
    wait_for(lambda: answers)
    app.updates = offline
    app.check_updates()
    wait_for(lambda: app.update[0] != "busy")
    gate.set()
    time.sleep(0.1)
    assert app.update == (updates_check.OFFLINE, updates_check.OFFLINE_TEXT)


def test_the_port_of_the_gps_named_in_the_settings_is_never_offered_for_flashing(tmp_path, ports_let_go):
    app, rig = node_app(tmp_path, ports={"ttyUSB0": ("1a86:55d4", BRIDGE, Talker(b""))})
    assert [target["path"] for target in screens.flash_targets(app)] == [str(rig.by_id / BRIDGE)]
    app.cfg["gps_source"] = rig.path("ttyUSB0")
    assert screens.flash_targets(app) == []


def hub_app(tmp_path):
    """ONE on USB, HUB on USB with the firmware for wireless nodes, both in the settings, and two wireless nodes near
    it that are not paired yet."""
    hub = FakeHub(HUB, airs=[FakeAir(AIR1, board="stamp-c5"), FakeAir(AIR2)], firmware=screens.bundled_firmware())
    ports = {"ttyACM0": ("303a:1001", NATIVE, FakeNode(ONE, firmware=screens.bundled_firmware())),
             "ttyACM1": ("303a:1001", HUB_LINK, hub)}
    app, rig = node_app(tmp_path, ports=ports, configured=(ONE, HUB))
    return app, rig, hub


def open_node(app, node_id):
    open_setting(app, "nodes")
    app.screen.index = next(i for i, (kind, node) in enumerate(app.screen.rows(app))
                            if kind == "known" and node["id"] == node_id)
    press(app, "ENTER")


def test_a_node_is_made_the_hub_and_wireless_nodes_are_added_by_pairing(tmp_path, drawn, ports_let_go):
    app, rig, hub = hub_app(tmp_path)
    open_setting(app, "nodes")
    assert "Add a wireless node" not in screen_text(app, drawn)
    press(app, "ENTER", "ESC")
    open_node(app, ONE)
    assert "Hub for wireless nodes" not in screen_text(app, drawn)
    press(app, "ESC", "ESC")
    open_node(app, HUB)
    app.screen.index = [action for action, _, _ in app.screen.actions(rig.manager.node(HUB))].index("hub")
    press(app, "ENTER")
    rig.settle()
    key = config.load(tmp_path / "config.json")["cluster_key"]
    assert len(key) == 32 and hub.role == "hub" and hub.saved_key == key and app.cfg["cluster_key"] == key
    assert app.toast_text == "It is the hub now. It does not scan."
    saved = {"id": HUB, "name": "N1", "ble": False, "hub": True, "last_hub": True}
    assert config.load(tmp_path / "config.json")["nodes"][1] == saved
    text = screen_text(app, drawn)
    assert "Hub for 0 wireless nodes" in text and key not in text and "Bluetooth" not in text
    press(app, "ESC")
    text = screen_text(app, drawn)
    assert "Add a wireless node" in text and "hub" in text.split("\n")
    app.screen.index = len(app.screen.rows(app)) - 1
    press(app, "ENTER")
    assert isinstance(app.screen, screens.Pairing) and hub.hub_commands[-1][0] == "HUB"
    rig.settle(2)
    assert hub.hub_commands[-1] == ("PAIR", "60")
    text = screen_text(app, drawn)
    assert "Hold BOOT on the new node for 2 seconds." in text and "0:5" in text
    assert text.count("New wireless node") == 2 and "Stamp-C5 E4F5" in text and "ESP32-C5 E4F6" in text
    press(app, "ENTER")
    rig.settle(2)
    text = screen_text(app, drawn)
    assert "Added" in text and text.count("New wireless node") == 1
    added = {"id": AIR1, "name": "Stamp-C5 E4F5", "ble": False, "wireless": True}
    assert config.load(tmp_path / "config.json")["nodes"][2] == added
    rig.settle(60 + 30)
    assert "Pairing is closed." in screen_text(app, drawn) and "Pair again" in drawn
    assert "New wireless node" not in drawn
    press(app, "ENTER")
    rig.settle()
    assert hub.hub_commands[-1] == ("PAIR", "60")
    press(app, "ESC")
    rig.settle()
    assert hub.hub_commands[-1] == ("PAIR", "0") and isinstance(app.screen, screens.Nodes)
    words = screen_text(app, drawn).split("\n")
    assert "wireless" in words and "Stamp-C5 E4F5" in words


def test_wireless_nodes_show_on_the_hardware_screen_and_say_when_the_hub_is_unplugged(tmp_path, drawn, ports_let_go):
    app, rig, hub = hub_app(tmp_path)
    rig.manager.set_hub(HUB, True)
    rig.settle()
    rig.manager.pair()
    rig.settle(2)
    rig.manager.admit(AIR1, "Bow")
    rig.settle(3)
    press(app, "H", "DOWN", "DOWN")
    text = screen_text(app, drawn)
    assert "Bow  wireless" in text and "-52 dBm" in text and "heard" in text and "AIR" not in text.split("\n")
    assert "Hub for 1 wireless node. It does not scan." in text and "HUB" in text
    assert [t["node"] for t in screens.flash_targets(app)] == [ONE, HUB]
    press(app, "ESC")
    open_node(app, AIR1)
    text = screen_text(app, drawn)
    assert "wireless" in text and "-52 dBm" in text and "Update firmware" not in text and "Hub for" not in text
    hub.unplug()
    rig.settle()
    text = screen_text(app, drawn)
    assert "Waiting for the hub. Plug it in." in text and "Waiting" in text
    press(app, "ESC")
    text = screen_text(app, drawn)
    assert "The hub is unplugged. Wireless nodes wait for it." in text and "Add a wireless node" not in text


def test_a_wireless_node_is_removed_and_the_hub_forgets_it(tmp_path, drawn, ports_let_go):
    app, rig, hub = hub_app(tmp_path)
    rig.manager.set_hub(HUB, True)
    rig.settle()
    rig.manager.pair()
    rig.settle(2)
    rig.manager.admit(AIR1, "Bow")
    rig.settle(3)
    open_node(app, AIR1)
    actions = [action for action, _, _ in app.screen.actions(rig.manager.node(AIR1))]
    assert actions == ["name", "ble", "light", "identify", "forget"]
    app.screen.index = 4
    press(app, "ENTER")
    assert "Hold BOOT on it for 2 seconds" in screen_text(app, drawn)
    press(app, "Y")
    rig.settle()
    assert ("FORGET", AIR1) in hub.hub_commands and AIR1 not in hub.admitted and app.toast_text == "Bow removed"
    assert [entry["id"] for entry in config.load(tmp_path / "config.json")["nodes"]] == [ONE, HUB]


def test_moving_the_hub_asks_first_when_its_wireless_nodes_must_be_added_again(tmp_path, drawn, ports_let_go):
    hub = FakeHub(HUB, airs=[FakeAir(AIR1)], firmware=screens.bundled_firmware())
    other = FakeHub(TWO, firmware=screens.bundled_firmware())
    ports = {"ttyACM0": ("303a:1001", NATIVE, FakeNode(ONE, firmware=screens.bundled_firmware())),
             "ttyACM1": ("303a:1001", HUB_LINK, hub), "ttyACM2": ("303a:1001", "", other)}
    app, rig = node_app(tmp_path, ports=ports, configured=(ONE, HUB, TWO))
    rig.manager.set_hub(HUB, True)
    rig.settle()
    rig.manager.pair()
    rig.settle(2)
    rig.manager.admit(AIR1, "Bow")
    rig.settle(3)
    open_node(app, TWO)
    app.screen.index = [action for action, _, _ in app.screen.actions(rig.manager.node(TWO))].index("hub")
    press(app, "ENTER")
    text = screen_text(app, drawn)
    assert "Move the hub here?" in text and "Your 1 wireless node must be added again." in text
    press(app, "ESC")
    rig.settle()
    assert rig.manager.hub()["id"] == HUB and hub.role == "hub" and other.role == "usb"
    press(app, "ENTER", "Y")
    rig.settle()
    assert rig.manager.hub()["id"] == TWO and other.role == "hub" and hub.role == "usb"
    assert app.toast_text == "It is the hub now. It does not scan."
    app.screen.index = [action for action, _, _ in app.screen.actions(rig.manager.node(TWO))].index("hub")
    press(app, "ENTER")
    text = screen_text(app, drawn).replace("\n", " ")
    assert "Stop the hub?" in text and "Your 1 wireless node stops until this node is the hub again." in text
    press(app, "Y")
    rig.settle()
    assert rig.manager.hub() is None and app.toast_text == "It scans again"
    open_node(app, HUB)
    app.screen.index = [action for action, _, _ in app.screen.actions(rig.manager.node(HUB))].index("hub")
    press(app, "ENTER")
    assert "Move the hub here?" in screen_text(app, drawn)
    press(app, "ESC")
    open_node(app, TWO)
    app.screen.index = [action for action, _, _ in app.screen.actions(rig.manager.node(TWO))].index("hub")
    press(app, "ENTER")
    rig.settle()
    assert rig.manager.hub()["id"] == TWO and app.toast_text == "It is the hub now. It does not scan."
    app.screen.index = [action for action, _, _ in app.screen.actions(rig.manager.node(TWO))].index("remove")
    press(app, "ENTER")
    assert "Your 1 wireless node stops until this node is the hub again." in screen_text(app, drawn).replace("\n", " ")


def test_the_pairing_screen_says_when_the_hub_is_plugged_in_but_not_answering_yet(tmp_path, drawn, ports_let_go):
    app, rig, hub = hub_app(tmp_path)
    rig.manager.set_hub(HUB, True)
    rig.settle()
    app.push(screens.Pairing())
    rig.manager.hub_ok = False
    text = screen_text(app, drawn)
    assert "Waiting for the hub to answer." in text and "unplugged" not in text
    hub.unplug()
    rig.settle()
    assert "The hub is unplugged." in screen_text(app, drawn)


def test_a_node_added_by_the_hub_is_saved_on_the_screen_thread(tmp_path, ports_let_go):
    app, rig, hub = hub_app(tmp_path)
    rig.manager.configured.append({"id": AIR1, "name": "Bow", "ble": False, "wireless": True})
    worker = threading.Thread(target=rig.manager.on_change, args=(rig.manager.entries(),))
    worker.start()
    worker.join()
    assert len(app.cfg["nodes"]) == 2 and len(config.load(tmp_path / "config.json")["nodes"]) < 3
    rig.manager.rename(ONE, "Roof")
    app.step(0)
    entries = rig.manager.entries()
    assert [entry["name"] for entry in entries][0::2] == ["Roof", "Bow"]
    assert app.cfg["nodes"] == entries and config.load(tmp_path / "config.json")["nodes"] == entries


def sky_app(tmp_path, hw=None, sdr=True, **settings):
    """An app whose wardrives start a stand-in for rtl_adsb."""
    import functools
    import subprocess
    from quartermaster import adsb
    from tests.fakes import FakeRtlAdsb, SkyHardware
    app = make_app(tmp_path, hw=hw or SkyHardware(), **settings)
    fake = FakeRtlAdsb()
    stick = [] if not sdr else [{"usb_id": "0bda:2838"}]
    dongles = (lambda: stick) if sdr in (True, False) else sdr
    receiver = functools.partial(adsb.Receiver, launcher=fake,
                                 which=lambda name: "/usr/bin/rtl_adsb" if name == "rtl_adsb" else None,
                                 dongles=dongles, feeds=(),
                                 out=tmp_path / "run", cpus=lambda: 4, kill=fake.kill, poll=0.01,
                                 runner=lambda argv, **kw: subprocess.CompletedProcess(argv, 0, "", ""),
                                 tools=lambda name: None)
    app.wardrive_factory = lambda c, g: Wardrive(c, g, root=tmp_path, adapters=ADAPTERS, ble=NoBle(), fleet=Fleet,
                                                 clock=lambda: NOW, receiver=receiver, sdr=dongles)
    app.rtl = fake
    return app


def hear(app, *planes):
    fake = app.rtl
    wait_for(lambda: fake.processes)
    fake.air(*planes)
    wait_for(lambda: app.drive.status()["aircraft"] == len(planes))


def test_the_aircraft_setting_starts_on_auto_and_waits_for_the_wardrive_to_stop(tmp_path, drawn):
    assert config.load(tmp_path / "none.json")["adsb"] == "auto"
    app = sky_app(tmp_path)
    press(app, "S")
    settings = app.screen
    settings.index = next(i for i, item in enumerate(settings.items) if item[0] == "adsb")
    press(app)
    text = screen_text(app, drawn)
    assert "Aircraft (ADS-B)\nAuto" in text and "Auto listens when one is plugged in." in text.replace("\n", " ")
    press(app, "RIGHT")
    assert app.cfg["adsb"] is True and config.load(tmp_path / "config.json")["adsb"] is True
    press(app, "RIGHT")
    assert app.cfg["adsb"] is False and config.load(tmp_path / "config.json")["adsb"] is False
    press(app, "ENTER")
    assert isinstance(app.screen, screens.Picker) and [text for _, text, _ in app.screen.options] == \
        ["Auto", "On", "Off"]
    press(app, "UP", "UP", "ENTER")
    assert app.cfg["adsb"] == "auto" and config.load(tmp_path / "config.json")["adsb"] == "auto"
    press(app, "ESC", "W", "ESC", "S")
    settings = app.screen
    settings.index = next(i for i, item in enumerate(settings.items) if item[0] == "adsb")
    press(app, "ENTER")
    assert app.cfg["adsb"] == "auto" and "Stop the wardrive" in app.toast_text


@pytest.mark.parametrize("saved, kept", [
    ({}, "auto"), ({"adsb": False}, "auto"), ({"adsb": False, "adsb_set": True}, False), ({"adsb": True}, True),
    ({"adsb": True, "adsb_set": True}, True), ({"adsb": "auto", "adsb_set": True}, "auto"), ({"adsb": "yes"}, "auto"),
    ({"adsb": 1}, "auto"), ({"adsb": 0, "adsb_set": True}, "auto"), ({"adsb": False, "adsb_set": "yes"}, "auto")])
def test_an_older_config_that_never_changed_the_aircraft_setting_gets_auto_and_one_that_did_keeps_it(
        tmp_path, saved, kept):
    path = tmp_path / "config.json"
    path.write_text(json.dumps(dict({"ble": True, "country": "DE", "country_set": True}, **saved)))
    cfg = config.load(path)
    assert cfg["adsb"] == kept and type(cfg["adsb"]) is type(kept) and cfg["country"] == "DE"
    config.save(cfg, path)
    assert config.load(path)["adsb"] == kept


def test_aircraft_turned_off_by_hand_stays_off_after_a_restart(tmp_path):
    app = make_app(tmp_path)
    assert app.cfg["adsb"] == "auto" and not app.cfg["adsb_set"]
    app.change("ble", False)
    assert config.load(tmp_path / "config.json")["adsb"] == "auto"
    app.change("adsb", False)
    saved = json.loads((tmp_path / "config.json").read_text())
    assert saved["adsb"] is False and saved["adsb_set"] is True
    assert config.load(tmp_path / "config.json")["adsb"] is False


def hardware_text(app, drawn):
    app.open("Hardware")
    return screen_text(app, drawn)


def test_the_hardware_screen_says_how_the_aircraft_radio_is(tmp_path, drawn):
    from tests.fakes import SkyHardware
    title = "Radio for aircraft (RTL-SDR)"
    assert title not in hardware_text(sky_app(tmp_path / "a", hw=SkyHardware(sdr=False)), drawn)
    assert "Off in Settings" in hardware_text(sky_app(tmp_path / "b", adsb=False), drawn)
    assert title not in hardware_text(sky_app(tmp_path / "auto", hw=SkyHardware(sdr=False)), drawn)
    assert "Ready. It listens in the next wardrive." in hardware_text(sky_app(tmp_path / "auto+"), drawn)
    for hw, words in ((SkyHardware(sdr=False), "Not plugged in"),
                      (SkyHardware(decoder=False), "rtl-sdr is missing. Reinstall Quartermaster"),
                      (SkyHardware(), "Ready. It listens in the next wardrive.")):
        text = hardware_text(sky_app(tmp_path / words[:6], hw=hw, adsb=True), drawn)
        assert title in text and words in text
    app = sky_app(tmp_path / "c", adsb=True)
    press(app, "W")
    hear(app, plane("a1b2c3", 25.2, -77.7))
    text = hardware_text(app, drawn)
    assert "Running" in text and "1 aircraft" in text
    app.rtl.processes[0].exit("usb_claim_interface error -6\n")
    wait_for(lambda: app.drive.status()["adsb"]["state"] == "busy")
    assert "In use by another program" in hardware_text(app, drawn)


def test_the_wardrive_and_its_summary_count_aircraft_only_when_they_are_on(tmp_path, drawn, monkeypatch):
    off = sky_app(tmp_path / "off", adsb=False)
    press(off, "W")
    assert "aircraft" not in screen_text(off, drawn)
    press(off, "ENTER", "ENTER")
    assert "Aircraft" not in screen_text(off, drawn) and off.rtl.started == []
    app = sky_app(tmp_path / "on", adsb=True)
    press(app, "W")
    hear(app, plane("a1b2c3", 25.2, -77.7), plane("c0ffee"))
    texts = placed(app, monkeypatch)
    assert "2 aircraft" in texts and "3 radios" in texts and texts["2 aircraft"][0] == texts["3 radios"][0]
    assert "WI-FI" in texts and "LOGGED" in texts
    press(app, "ENTER", "ENTER")
    text = screen_text(app, drawn)
    assert "WARDRIVE SAVED" in text and "Aircraft\n2" in text
    assert app.rtl.processes[0].signals == [signal.SIGTERM]


def test_auto_listens_for_aircraft_only_while_a_stick_is_plugged_in_also_when_it_comes_during_the_wardrive(
        tmp_path, drawn, monkeypatch):
    from quartermaster import adsb, engine
    from tests.fakes import SkyHardware
    monkeypatch.setattr(engine, "UPKEEP_SECONDS", 0.01)
    stick = []
    app = sky_app(tmp_path, hw=SkyHardware(sdr=False), sdr=lambda: stick)
    assert app.cfg["adsb"] == "auto"
    press(app, "W")
    time.sleep(0.1)
    assert app.drive.aircraft is None and app.rtl.started == []
    assert "aircraft" not in screen_text(app, drawn) and app.drive.status()["aircraft"] is None
    assert "Radio for aircraft (RTL-SDR)" not in hardware_text(app, drawn)
    stick.append({"usb_id": "0bda:2838"})
    app.hw.sdr = [{"usb_id": "0bda:2838", "label": "RTL2838UHIDIR", "port": "1-1.3"}]
    hear(app, plane("a1b2c3", 25.2, -77.7))
    assert "Running" in hardware_text(app, drawn) and "1 aircraft" in screen_text(app, drawn)
    stick.clear()
    app.rtl.processes[0].exit("usb_claim_interface error -6\n")
    wait_for(lambda: app.drive.status()["adsb"]["state"] == adsb.NO_DONGLE)
    app.hw.sdr = []
    assert "Not plugged in" in hardware_text(app, drawn)
    app.open("Wardrive")
    press(app, "ENTER", "ENTER")
    assert "Aircraft\n1" in screen_text(app, drawn) and app.drive.aircraft_path is not None


class StandInReceiver:
    count = 0

    def __init__(self, *args, **kwargs):
        self.started = False

    def start(self, epoch):
        self.started = True

    def stop(self):
        pass

    def finish(self):
        return None

    def status(self):
        return {"state": "running", "aircraft": 0, "detail": "Running"}


def test_auto_counts_a_stick_as_a_radio_to_wardrive_with(tmp_path, monkeypatch):
    from quartermaster import engine
    cfg = dict(config.DEFAULTS, ble=False)
    stick = []

    def drive(**settings):
        return Wardrive(dict(cfg, **settings), Gps(FIX), root=tmp_path, adapters=[], ble=NoBle(), fleet=Fleet,
                        clock=lambda: NOW, receiver=StandInReceiver, sdr=lambda: stick)
    with pytest.raises(RuntimeError):
        drive().start()
    stick.append({"usb_id": "0bda:2838"})
    with pytest.raises(RuntimeError):
        drive(adsb=False).start()
    listening = drive()
    listening.start()
    assert listening.state == "running" and listening.aircraft.started
    listening.stop()
    assert drive(adsb=True)._sky_wanted() and not drive(adsb=False)._sky_wanted()
    stick.clear()
    assert drive(adsb=True)._sky_wanted() and not drive()._sky_wanted()
    monkeypatch.setattr(engine.hardware, "rtl_sdr_dongles", lambda: [{"usb_id": "0bda:2838"}])
    assert Wardrive(cfg, Gps(FIX), root=tmp_path)._sky_wanted()


def test_aircraft_files_are_listed_with_their_own_marks_and_are_never_for_wigle(tmp_path, drawn):
    app = sky_app(tmp_path, adsb=True, keys=KEYS)
    press(app, "W")
    hear(app, plane("a1b2c3", 25.2, -77.7))
    press(app, "ENTER", "ENTER")
    app.uploader.run_pass()
    assert sorted(app.sent) == sorted([("wigle", "quartermaster_20261118-110640.wigle.csv"),
                                       ("wdgwars", "quartermaster_20261118-110640.wigle.csv"),
                                       ("wardrift", "quartermaster_20261118-110640.wigle.csv"),
                                       ("wdgwars", "quartermaster_20261118-110640.aircraft.json.gz"),
                                       ("wardrift", "quartermaster_20261118-110640.aircraft.json.gz")])
    app.stale()
    press(app, "ESC", "L")
    text = screen_text(app, drawn)
    assert "2 logs" in text and "AIR" in text
    files = app.log_files()
    assert [f["aircraft"] for f in files] == [False, True]
    press(app, "DOWN", "ENTER")
    text = screen_text(app, drawn)
    assert "quartermaster_20261118-110640.aircraft.j\nson.gz" in text and "Aircraft\n1" in text
    assert "Not for WiGLE" in text and "WDGWars has it" in text and "WDGWars and Wardrift only" in text
    press(app, "D", "RIGHT", "ENTER")
    assert sorted(p.name for p in (tmp_path / "logs").iterdir()) == [
        "quartermaster_20261118-110640.summary.csv", "quartermaster_20261118-110640.wigle.csv"]
    assert "quartermaster_20261118-110640.aircraft.json.gz" not in app.uploader.ledger.data


def test_the_decoder_is_stopped_when_quartermaster_closes_during_a_wardrive(tmp_path):
    app = sky_app(tmp_path, adsb=True)
    press(app, "W")
    wait_for(lambda: app.rtl.processes)
    app.shutdown()
    assert app.rtl.processes[0].signals == [signal.SIGTERM] and app.drive.aircraft_path is None


def test_the_aircraft_card_is_amber_when_the_stick_cannot_listen_and_says_who_holds_it(tmp_path):
    from quartermaster import adsb
    from tests.fakes import SkyHardware
    cards = {}
    for name, hw in (("ready", SkyHardware()), ("gone", SkyHardware(sdr=False)), ("bare", SkyHardware(decoder=False)),
                     ("system", SkyHardware(decoder=False, sky_use=adsb.SYSTEM)),
                     ("busy", SkyHardware(sky_use=adsb.BUSY))):
        cards[name] = screens.Hardware.aircraft_card(sky_app(tmp_path / name, hw=hw, adsb=True), None)
    assert cards["ready"]["color"] == gfx.GOLD and "edge" not in cards["ready"]
    for name in ("gone", "bare", "busy"):
        assert cards[name]["color"] == cards[name]["edge"] == cards[name]["detail_color"] == gfx.AMBER
    assert cards["busy"]["detail"] == "In use by another program"
    assert (cards["system"]["detail"], cards["system"]["detail_color"], cards["system"]["right"]) == \
        ("Uses the aircraft program that already runs", gfx.GREEN, "")


def test_the_aircraft_count_is_amber_when_not_listening_and_left_out_when_it_does_not_fit(tmp_path, monkeypatch):
    from quartermaster import adsb
    app = sky_app(tmp_path / "gone", sdr=False, adsb=True)
    press(app, "W")
    wait_for(lambda: app.drive.status()["adsb"]["state"] == adsb.NO_DONGLE)
    assert placed(app, monkeypatch)["0 aircraft"][1] == gfx.AMBER
    press(app, "ENTER", "ENTER")
    live = sky_app(tmp_path / "live", adsb=True)
    press(live, "W")
    hear(live, plane("a1b2c3", 25.2, -77.7))
    assert placed(live, monkeypatch)["1 aircraft"][1] == gfx.MUTED
    monkeypatch.setattr(adsb.Receiver, "count", property(lambda self: 10 ** 16))
    texts = placed(live, monkeypatch)
    assert "3 radios" in texts and not any(text.endswith(" aircraft") for text in texts)
    press(live, "ENTER", "ENTER")


def test_a_wardrive_that_saved_only_aircraft_says_where_they_go(tmp_path, drawn):
    app = sky_app(tmp_path, fix=NO_FIX, adsb=True, keys=KEYS, auto_upload=True)
    press(app, "W")
    hear(app, plane("a1b2c3", 25.2, -77.7))
    press(app, "ENTER", "ENTER")
    text = screen_text(app, drawn)
    assert "WARDRIVE SAVED" in text and "Uploading to WDGWars, Wardrift." in text and "GPS fix" not in text


def test_the_log_of_a_wardrive_ended_by_a_power_cut_is_finished_when_quartermaster_opens(tmp_path, monkeypatch):
    from quartermaster import engine, wigle
    from tests.fakes import networks
    from tests.test_engine import closest_and_furthest, power_cut, rows_of
    drive, _ = closest_and_furthest(tmp_path)
    drive._wifi_sink(networks(5), ADAPTERS[0])
    log = drive.log_path
    power_cut(drive)
    engine._writing.clear()
    app = make_app(tmp_path)
    started = []
    monkeypatch.setattr(app.uploader, "start", lambda: started.append(list(app.uploader.logs())))
    app._booted = True
    app.finish_last()
    app.step(0)
    assert len(rows_of(log)) == 5 and not wigle.journal_path(log).exists() and started == [[log]]
    assert app.toast_text == "Finished the log of your last wardrive."


def test_the_log_of_a_running_wardrive_is_never_finished_from_under_it(tmp_path, monkeypatch):
    from quartermaster import wigle
    from tests.fakes import networks
    app = make_app(tmp_path)
    assert app.start_wardrive()
    app.drive._wifi_sink(networks(5), ADAPTERS[0])
    journal = wigle.journal_path(app.drive.log_path)
    monkeypatch.setattr(app.uploader, "start", lambda: None)
    app.finish_last()
    assert journal.exists() and app.toast_text != "Finished the log of your last wardrive."
    app.stop_wardrive()


def test_starting_a_wardrive_says_so_while_it_finishes_the_last_one(tmp_path, drawn):
    from tests.fakes import networks
    from tests.test_engine import closest_and_furthest, power_cut, rows_of
    drive, _ = closest_and_furthest(tmp_path)
    drive._wifi_sink(networks(5), ADAPTERS[0])
    log = drive.log_path
    power_cut(drive)
    app = make_app(tmp_path)
    shown = []
    app.display.show = lambda image: shown.append(list(drawn))
    assert app.start_wardrive()
    assert any("Finishing the last wardrive…" in texts for texts in shown) and len(rows_of(log)) == 5
    app.stop_wardrive()


def test_the_country_is_asked_the_first_time_the_nodes_are_opened(tmp_path, ports_let_go):
    app, rig = node_app(tmp_path, country_set=False)
    assert not config.load(tmp_path / "config.json")["country_set"]
    open_setting(app, "nodes")
    assert isinstance(app.screen, screens.Picker) and app.screen.title == "Where are you?"
    press(app, *["DOWN"] * 3 + ["ENTER"])
    assert isinstance(app.screen, screens.Nodes) and app.cfg["country"] == "DE" == rig.manager.country
    assert config.load(tmp_path / "config.json")["country_set"]
    press(app, "ESC")
    open_setting(app, "nodes")
    assert isinstance(app.screen, screens.Nodes)
    again, _ = node_app(tmp_path / "upgraded", country_set=False)
    open_setting(again, "nodes")
    press(again, "ESC")
    assert isinstance(again.screen, screens.Nodes) and again.cfg["country"] == "US"
    open_setting(again, "nodes")
    assert isinstance(again.screen, screens.Picker)


def test_a_node_is_told_its_light_and_a_stamp_is_warned_about_its_boot_button(tmp_path, drawn, ports_let_go):
    app, rig, hub = hub_app(tmp_path)
    rig.manager.set_hub(HUB, True)
    rig.settle()
    rig.manager.pair()
    rig.settle(2)
    rig.manager.admit(AIR1, "Bow")
    rig.settle(3)
    open_node(app, AIR1)
    app.screen.index = [action for action, _, _ in app.screen.actions(rig.manager.node(AIR1))].index("light")
    press(app, "ENTER")
    assert isinstance(app.screen, screens.Picker) and app.screen.options[app.screen.index][1] == "Stamp-C5"
    press(app, "ENTER")
    assert "Use G28 as its light?" in screen_text(app, drawn) and rig.manager.node(AIR1)["led"] == ""
    press(app, "Y")
    rig.settle(3)
    air = hub.airs[AIR1]
    assert air.led == ["gpio", "28"] and rig.manager.node(AIR1)["led"] == "gpio,28"
    assert "Stamp-C5" in screen_text(app, drawn)
    air.reach = False
    rig.clock.advance(nodes.AIR_SILENT_SECONDS + 1)
    rig.settle(1)
    app.screen.index = [action for action, _, _ in app.screen.actions(rig.manager.node(AIR1))].index("identify")
    press(app, "ENTER")
    assert app.toast_text == "It is not heard right now"


def test_every_word_about_a_node_fits_where_it_is_shown():
    canvas = gfx.Canvas()
    said = [value for name, value in vars(nodes).items() if name.isupper() and isinstance(value, str) and " " in value]
    said += list(nodes.ADMIT_ERRORS.values())
    for words in said:
        assert canvas.text_width(words, gfx.font("mono", 8)) <= gfx.W - 26, words
        assert canvas.text_width(words, gfx.font("ui", 8)) <= gfx.W - 57, words
    for words in list(screens.NodeDetail.HELP.values()) + [screens.NodeDetail.STRANDED]:
        assert canvas.text_width(words, gfx.font("ui", 9)) <= gfx.W - 20, words


def test_every_word_about_the_aircraft_radio_fits_on_its_card():
    from quartermaster import adsb
    canvas = gfx.Canvas()
    for state, words in adsb.WORDS.items():
        room = gfx.W - 57
        if state in (adsb.RUNNING, adsb.SYSTEM):
            room -= int(canvas.text_width("9999 aircraft", gfx.font("ui", 8))) + 6
        assert canvas.text_width(words, gfx.font("ui", 8)) <= room, words


def test_a_flash_that_cannot_work_again_offers_no_retry_and_another_layout_offers_a_first_install(
        tmp_path, drawn, ports_let_go):
    log = []
    app, rig = node_app(tmp_path, configured=(TWO,))
    target = next(target for target in screens.flash_targets(app) if target["node"] == TWO)
    app.flasher_class = FakeFlasher(log, error=flasher.EARLY % (0, 1))
    app.flash(target, "update")
    finish(app)
    assert app.screen.hints(app) == [("Esc", "Back")]
    del log[:]
    press(app, "ENTER")
    assert not isinstance(app.screen, screens.Flashing) and log == []
    rig.manager.scan()
    rig.settle()
    app.flasher_class = FakeFlasher(log, error=flasher.LAYOUT, at="install")
    app.flash(target, "update")
    finish(app)
    assert app.flash_job.error == flasher.LAYOUT, app.flash_job.error
    assert app.screen.hints(app)[0] == ("OK", "First install") and ("install", "update") in log
    press(app, "ENTER")
    assert "Install from scratch?" in screen_text(app, drawn)
    app.flasher_class.error = ""
    del log[:]
    rig.manager.scan()
    rig.settle()
    press(app, "Y")
    finish(app)
    assert ("install", "first") in log and app.flash_job.mode == "first" and app.flash_job.state == "done"


def mesh_app(tmp_path, cap=None, **settings):
    """An app with a Cap LoRa-1262 stand-in that its wardrives listen with."""
    from tests.fakes import FakeCap
    app = make_app(tmp_path, **dict(dict(country_set=True), **settings))
    app.cap = cap or FakeCap()
    app.wardrive_factory = lambda c, g: Wardrive(c, g, root=tmp_path, adapters=ADAPTERS, ble=NoBle(), fleet=Fleet,
                                                 clock=lambda: NOW, cap=app.cap)
    return app


def heard_meshtastic(app, *sources):
    """Meshtastic packets on a private channel: the sender is in the clear, so no key is needed."""
    import struct
    for source in sources:
        app.cap.the_radio.hear(struct.pack("<IIIBBBB", 0xFFFFFFFF, source, 7, 0x63, 0x55, 0, 0) + bytes(24))
    wait_for(lambda: app.drive.status()["mesh"] == len(sources))


def test_the_lora_setting_starts_on_auto_and_looks_for_the_cap_again_when_turned_back_on(tmp_path, drawn):
    from quartermaster import lora
    from tests.fakes import FakeCap
    assert config.load(tmp_path / "none.json")["lora"] == "auto"
    app = mesh_app(tmp_path, cap=FakeCap(lora.OFF), lora=False)
    open_setting(app, "lora")
    press(app, "ESC")
    text = screen_text(app, drawn)
    assert "LoRa mesh\nOff" in text and "Auto listens when one is plugged in." in text.replace("\n", " ")
    press(app, "LEFT")
    assert app.cfg["lora"] is True and config.load(tmp_path / "config.json")["lora"] is True
    assert app.cap.started == [True] and app.cap.state == lora.CHECKING


def test_every_setting_help_fits_in_its_two_lines():
    canvas = gfx.Canvas()
    for key, label, _, help_text in config.SETTINGS:
        assert len(canvas.wrap(help_text, gfx.W - 20, gfx.font("ui", 9))) <= 2, key


def test_the_hardware_screen_says_how_the_lora_cap_is(tmp_path, drawn):
    from quartermaster import lora
    from tests.fakes import FakeCap
    title = "Cap LoRa-1262 for mesh nodes"
    assert title not in hardware_text(mesh_app(tmp_path / "a", cap=FakeCap(lora.ABSENT)), drawn)
    text = hardware_text(mesh_app(tmp_path / "b", cap=FakeCap(lora.ABSENT), lora=True), drawn)
    assert title in text and "Not found. Plug it in, then switch on." in text
    text = hardware_text(mesh_app(tmp_path / "c"), drawn)
    assert title in text and "Ready. It listens in the next wardrive." in text and "US 915" in text
    assert "Off in Settings" in hardware_text(mesh_app(tmp_path / "d", lora=False), drawn)
    assert "No LoRa band for this country" in hardware_text(mesh_app(tmp_path / "e", country="JP"), drawn)
    app = mesh_app(tmp_path / "f")
    press(app, "W")
    heard_meshtastic(app, 0x1A2B3C4D)
    text = hardware_text(app, drawn)
    assert "Meshtastic LongFast 906.875 MHz" in text and "1 packet" in text
    app.cap.the_radio.error = lora.BUSY_ELSEWHERE
    wait_for(lambda: app.drive.status()["lora"]["reason"] == lora.BUSY_ELSEWHERE)
    card = screens.Hardware.lora_card(app, app.drive.status())
    assert card["detail"] == "In use by another app" and card["color"] == card["detail_color"] == gfx.AMBER
    app.shutdown()


def test_with_the_cap_found_and_no_country_chosen_the_first_wardrive_asks_where_you_are(tmp_path, drawn):
    from quartermaster import lora
    app = mesh_app(tmp_path / "a", country_set=False)
    text = hardware_text(app, drawn)
    assert "Set your country in Settings." in text and "Ready. It listens" not in text
    app.home()
    press(app, "W")
    assert isinstance(app.screen, screens.Picker) and app.screen.title == "Where are you?" and app.drive is None
    nz = next(i for i, (value, _) in enumerate(config.COUNTRIES) if value == "NZ")
    app.screen.index = nz
    press(app, "ENTER")
    assert app.cfg["country"] == "NZ" and app.cfg["country_set"] and isinstance(app.screen, screens.Wardrive)
    assert app.drive.mesh.plan == lora.plan("NZ")
    press(app, "ENTER", "ENTER")
    later = mesh_app(tmp_path / "b", country_set=False)
    press(later, "W")
    press(later, "ESC")
    assert later.drive is None and not later.cfg["country_set"]
    press(later, "W")
    assert later.drive is not None and isinstance(later.screen, screens.Wardrive)
    assert "LoRa uses the US 915 band. Set your country in Settings." in screen_text(later, drawn)
    press(later, "ENTER", "ENTER")
    hands_off = mesh_app(tmp_path / "c", country_set=False, auto_start=True)
    hands_off.booted()
    assert hands_off.drive is not None and isinstance(hands_off.screen, screens.Wardrive)
    press(hands_off, "ENTER", "ENTER")
    for country in ("US", "NZ", "BR", "DE"):
        words = "LoRa uses the %s band. Set your country in Settings." % lora.region_name(country)
        assert gfx.Canvas().text_width(words, gfx.font("ui", 9)) <= gfx.W - 10, country


def test_the_wardrive_and_its_summary_count_mesh_nodes(tmp_path, drawn, monkeypatch):
    from tests.fakes import FakeCap
    absent = mesh_app(tmp_path / "absent", cap=FakeCap("absent"))
    press(absent, "W")
    assert " mesh" not in screen_text(absent, drawn)
    press(absent, "ENTER", "ENTER")
    assert "Mesh" not in screen_text(absent, drawn)
    app = mesh_app(tmp_path / "on")
    press(app, "W")
    heard_meshtastic(app, 0x1A2B3C4D, 0x0BADCAFE)
    texts = placed(app, monkeypatch)
    assert texts["2 mesh"][0] == texts["3 radios"][0] and texts["2 mesh"][1] == gfx.MUTED
    press(app, "ENTER", "ENTER")
    text = screen_text(app, drawn)
    assert "WARDRIVE SAVED" in text and "Mesh nodes\n2" in text


def test_the_mesh_count_is_amber_when_the_cap_stops_listening(tmp_path, monkeypatch):
    from quartermaster import lora
    app = mesh_app(tmp_path)
    app.cap.the_radio.error = lora.ABSENT
    press(app, "W")
    wait_for(lambda: app.drive.status()["lora"]["state"] != "starting")
    assert placed(app, monkeypatch)["0 mesh"][1] == gfx.AMBER
    press(app, "ENTER", "ENTER")


def test_a_full_counter_row_still_shows_the_mesh_count_and_a_stopped_cap_is_said(tmp_path, monkeypatch, drawn):
    from quartermaster import adsb, mesh
    app = mesh_app(tmp_path)
    press(app, "W")
    real = app.drive.status
    full = dict(bands={"2.4": 163, "5": 175, "6": 74}, aircraft=23, mesh=9, adsb={"state": adsb.RUNNING},
                lora={"state": mesh.LISTENING},
                adapters=[{"interface": "wlan%d" % i} for i in range(3)] +
                [{"interface": "node%d" % i, "method": "node"} for i in range(2)])
    monkeypatch.setattr(app.drive, "status", lambda: dict(real(), **full))
    texts = placed(app, monkeypatch)
    assert texts["9 mesh"][0] == texts["23 aircraft"][0] == texts["5 radios"][0]
    assert texts["9 mesh"][1] == gfx.MUTED and "3 radios + 2 nodes" not in texts
    monkeypatch.setattr(app.drive, "status", lambda: dict(real(), **dict(full, lora={"state": mesh.FAILED})))
    assert "The LoRa cap stopped listening. See Hardware." in screen_text(app, drawn)
    full.update(aircraft=None, mesh=None)
    assert "3 radios + 2 nodes" in placed(app, monkeypatch)
    monkeypatch.setattr(app.drive, "status", real)
    press(app, "ENTER", "ENTER")


def test_mesh_files_are_listed_with_their_own_tag_and_are_never_for_wigle(tmp_path, drawn):
    app = mesh_app(tmp_path, keys=KEYS)
    press(app, "W")
    heard_meshtastic(app, 0x1A2B3C4D)
    press(app, "ENTER", "ENTER")
    app.uploader.run_pass()
    assert ("wigle", "quartermaster_20261118-110640.mesh.json.gz") not in app.sent
    assert ("wdgwars", "quartermaster_20261118-110640.mesh.json.gz") in app.sent
    assert ("wardrift", "quartermaster_20261118-110640.mesh.json.gz") in app.sent
    app.stale()
    press(app, "ESC", "L")
    text = screen_text(app, drawn)
    assert "2 logs" in text and "MESH" in text
    assert [f["mesh"] for f in app.log_files()] == [False, True]
    press(app, "DOWN", "ENTER")
    text = screen_text(app, drawn)
    assert "Mesh nodes\n1" in text and "Not for WiGLE" in text and "Mesh nodes go to WDGWars and Wardrift only." in text
    press(app, "D", "RIGHT", "ENTER")
    assert sorted(p.name for p in (tmp_path / "logs").iterdir()) == [
        "quartermaster_20261118-110640.summary.csv", "quartermaster_20261118-110640.wigle.csv"]


def test_every_word_about_the_lora_cap_fits_on_its_card():
    from quartermaster import lora, mesh
    canvas = gfx.Canvas()
    small = gfx.font("ui", 8)
    room = gfx.W - 57
    for words in list(mesh.WORDS.values()) + ["Ready. It listens in the next wardrive."]:
        assert canvas.text_width(words, small) <= room, words
    beside = room - int(canvas.text_width("9999 packets", small)) - 6
    for country in lora.COUNTRY_REGION:
        for profile in lora.plan(country):
            assert canvas.text_width(profile.short, small) <= beside, profile.short
    title = canvas.text_width("Cap LoRa-1262 for mesh nodes", gfx.font("ui", 10))
    for country in lora.COUNTRY_REGION:
        assert title + canvas.text_width(lora.region_name(country), small) + 8 <= gfx.W - 40, country


def test_the_about_screen_thanks_the_people_who_helped(tmp_path, monkeypatch):
    app = make_app(tmp_path)
    press(app, "A")
    found = placed(app, monkeypatch)
    lines = [text for text in found if text.startswith("Thanks to") or "Ekoms_Savior" in text or "Awok" in text]
    assert len(lines) == 3 and " ".join(lines) == screens.THANKS
    for name in ("justcallmekoko", "Code_Hedge", "Ekoms_Savior", "Jesse Hale", "Vileer from Hacker Gadgets", "LocoSp",
                 "Awok", "Talking Sasquach"):
        assert name in screens.THANKS.replace("\u00a0", " ")
    assert any("Hacker\u00a0Gadgets" in line for line in lines)
    assert all(found[line][0] + 9 < screens.CONTENT_BOTTOM - 37 for line in lines)
