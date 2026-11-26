import csv

import pytest

from quartermaster import engine, hardware, lora, wigle


def test_diy_gps_discovers_usb_and_keeps_user_selected_ports(tmp_path):
    tty, dev = tmp_path / "tty", tmp_path / "dev"
    (tty / "ttyUSB0").mkdir(parents=True)
    dev.mkdir()
    for name in ("serial0", "ttyS0"):
        (dev / name).touch()
    custom = str(dev / "my-gps")
    ports = hardware.gps_candidates(tty, dev, extra=[custom], cardputer=False)
    assert [(port["kind"], port["path"]) for port in ports] == [
        ("custom", custom), ("usb", str(dev / "ttyUSB0"))]


def test_diy_cap_never_probes_even_when_both_chip_selects_are_enabled(tmp_path):
    spi, supply = tmp_path / "spidev0.1", tmp_path / "supply"
    spi.touch()
    supply.write_text("0")
    cap = lora.Cap(cardputer=False, spi=spi, supply=supply, radio=lambda *args: pytest.fail("Cap opened on DIY"))
    for wanted in (True, False, True):
        cap.start(wanted)
        assert cap.state == lora.ABSENT and cap._thread is None
    with pytest.raises(lora.RadioError, match=lora.ABSENT):
        cap.radio()
    assert supply.read_text() == "0"


@pytest.mark.parametrize("profile,size,mode", [
    ("st7735-128", "128x128", "track"),
    ("st7789-240", "240x240", "extremes"),
])
def test_diy_wardrive_records_its_actual_board_and_display(tmp_path, monkeypatch, profile, size, mode):
    model = tmp_path / "model"
    model.write_bytes(b"Raspberry Pi Zero 2 W Rev 1.0\x00")
    monkeypatch.setattr(wigle, "DEVICE_TREE_MODEL", model)
    monkeypatch.setattr(hardware, "os_release", lambda: "13")
    drive = engine.Wardrive({"log_rows": mode}, None, root=tmp_path, display_profile=profile)
    drive.logs.mkdir()
    drive.state = "running"
    row = ["aa:bb:cc:dd:ee:ff", "Test", "[ESS]", "2026-01-01 01:01:01", 1, 2412, -60, 1, 2, 0, 0, "", "", "WIFI"]
    assert drive._append(row, 1800000000)
    file = drive.writer.path
    drive.writer.close()
    with file.open(newline="") as data:
        header = next(csv.reader(data))
    assert "model=Raspberry Pi Zero 2 W Rev 1.0" in header and "board=Raspberry Pi Zero 2 W Rev 1.0" in header
    assert "display=" + size in header and "device=Quartermaster" in header
    model.unlink()
    assert "model=Raspberry Pi" in wigle.pre_header(display_profile=profile)
    assert "model=CardputerZero" in wigle.pre_header() and "display=320x170" in wigle.pre_header()
