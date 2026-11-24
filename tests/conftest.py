import time

import pytest

from quartermaster import drive_watch, engine, faults, hardware, lora, radios, tones
from quartermaster.names import one_line
from quartermaster.ui import gfx, keys


@pytest.fixture
def drawn(monkeypatch):
    """Every string a screen draws, so a test can read the screen."""
    texts = []
    original = gfx.Canvas.put

    def record(self, xy, text, *args, **kwargs):
        texts.append(one_line(text))
        return original(self, xy, text, *args, **kwargs)
    monkeypatch.setattr(gfx.Canvas, "put", record)
    return texts


@pytest.fixture
def noted(tmp_path, monkeypatch):
    """The file unexpected errors are noted in."""
    monkeypatch.setattr(faults, "path", tmp_path / "errors.log")
    monkeypatch.setattr(faults, "_recent", {})
    return faults.path


@pytest.fixture(autouse=True)
def free_radios():
    yield
    radios.release("wardrive")
    radios.release("recon")


@pytest.fixture(autouse=True)
def no_wardrive_left_running(monkeypatch):
    """A wardrive that outlives its test would go on writing to its log during the tests after it."""
    started, start = [], engine.Wardrive.start

    def record(drive):
        started.append(drive)
        start(drive)
    monkeypatch.setattr(engine.Wardrive, "start", record)
    yield
    for drive in started:
        engine.Wardrive.stop(drive)


@pytest.fixture(autouse=True)
def no_alert_log_left_open(monkeypatch):
    """Drive alerts and Recon that a test started without stopping them must not leave their log file open."""
    started, start = [], drive_watch.DriveWatch.start

    def record(watch, epoch):
        started.append(watch)
        start(watch, epoch)
    monkeypatch.setattr(drive_watch.DriveWatch, "start", record)
    from quartermaster.recon_engine import Recon
    made, init = [], Recon.__init__

    def keep(recon_mode, *args, **kwargs):
        made.append(recon_mode)
        init(recon_mode, *args, **kwargs)
    monkeypatch.setattr(Recon, "__init__", keep)
    yield
    for watch in started:
        with watch._lock:
            if watch._log is not None:
                watch._log.close()
                watch._log = None
    for recon_mode in made:
        if recon_mode._log is not None:
            recon_mode._log.close()
            recon_mode._log = None


@pytest.fixture(autouse=True)
def no_radio_helper(monkeypatch, tmp_path):
    """The radio helper of the machine the tests run on is never asked for anything."""
    from quartermaster import radio_helper
    monkeypatch.setattr(radio_helper, "SOCKET", str(tmp_path / "no-radio.sock"))
    monkeypatch.setattr(radio_helper.connect, "__defaults__", (str(tmp_path / "no-radio.sock"), radio_helper.Client))


@pytest.fixture(autouse=True)
def no_installed_keymap(monkeypatch):
    """The keymap file of the machine the tests run on must not decide their result."""
    monkeypatch.setattr(keys, "KEYMAP", "/nonexistent")


@pytest.fixture(autouse=True)
def no_usb_of_this_machine(tmp_path, monkeypatch):
    """An RTL-SDR stick on the machine the tests run on must not turn on aircraft on Auto."""
    monkeypatch.setattr(hardware.rtl_sdr_dongles, "__defaults__", (tmp_path / "no-usb",))


@pytest.fixture(autouse=True)
def no_lora_cap_of_this_machine(tmp_path, monkeypatch):
    """A Cap LoRa-1262 on the machine the tests run on must not be powered or looked for."""
    missing = (tmp_path / "no-spidev", tmp_path / "no-supply", tmp_path / "no-mux")
    monkeypatch.setattr(lora.Cap.__init__, "__defaults__", (None,) + missing + (lora.CapRadio,))


@pytest.fixture
def later(monkeypatch):
    """Moves the clock on by a number of seconds, for everything that reads it."""
    real, ahead = time.monotonic, [0.0]
    monkeypatch.setattr(time, "monotonic", lambda: real() + ahead[0])

    def move(seconds):
        ahead[0] += seconds
    return move


@pytest.fixture(autouse=True)
def no_speaker(monkeypatch):
    """No test plays a tone on the machine it runs on."""
    monkeypatch.setattr(tones, "find_player", lambda: None)
