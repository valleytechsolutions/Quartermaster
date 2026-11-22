import http.client
import os
from pathlib import Path
import shutil
import time

import pytest

from quartermaster import accounts, config, radios, recon, uploads
from quartermaster.engine import Wardrive
from quartermaster.recon_engine import Recon
from quartermaster.ui import gfx, recon_screens, screens
from quartermaster.ui.app import App
from quartermaster.ui.keys import ScriptedInput
from tests.fakes import (ADAPTERS, FIX, NO_FIX, Display, Fleet, Gps, Hardware, NoBle, QuietFleet, Silent, networks,
                         on_a_card)
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
    app = make_app(tmp_path, keys=KEYS, log_rows="track")
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
    app = make_app(tmp_path / "saved", keys=KEYS)
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
