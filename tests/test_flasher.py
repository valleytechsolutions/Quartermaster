import hashlib
import json
import os
import random
import sys

import pytest

from quartermaster import flasher
from quartermaster.flasher import FlashError, Flasher
from tests.fakes import Rom

APP, NVS, OTADATA, TABLE = 0x20000, 0x9000, 0xF000, 0x8000


def firmware(seed=1, size=0x30000):
    rnd = random.Random(seed)
    image = bytearray(b"\xff" * size)
    image[0x2000:0x6000] = rnd.randbytes(0x4000)
    image[TABLE:TABLE + 0xC00] = bytes(range(256)) * 12
    image[APP:] = rnd.randbytes(0x8000) + b"\x00" * (size - APP - 0x8000)
    return bytes(image)


def manifest(folder, merged, app=None):
    app = merged[APP:] if app is None else app
    (folder / "merged.bin").write_bytes(merged)
    (folder / "app.bin").write_bytes(app)
    data = {"version": "1.0.0", "partition_table_offset": TABLE,
            "partition_offsets": {"nvs": NVS, "otadata": OTADATA, "ota_0": APP},
            "merged": {"file": "merged.bin", "offset": 0, "sha256": hashlib.sha256(merged).hexdigest(),
                       "size": len(merged)},
            "app": {"file": "app.bin", "offset": APP, "sha256": hashlib.sha256(app).hexdigest(), "size": len(app)}}
    path = folder / "quartermaster-node.json"
    path.write_text(json.dumps(data))
    return path


def flasher_for(rom, **kwargs):
    return Flasher("/dev/ttyACM0", lambda: rom, rom.clock, rom.sleep, **kwargs)


def connected(rom, native_usb=True):
    board = flasher_for(rom)
    board.connect(native_usb)
    return board


def refusal(rom, native_usb=True):
    with pytest.raises(FlashError) as caught:
        connected(rom, native_usb)
    return str(caught.value)


def lines(events):
    """The line changes and waits after the port was opened."""
    start = events.index(next(event for event in events if event[0] == "open")) + 2
    return [event for event in events[start:] if event[0] in ("dtr", "rts", "wait")]


CLASSIC = [("dtr", False), ("rts", True), ("wait", 0.1), ("dtr", True), ("rts", False), ("wait", 0.05),
           ("dtr", False)]
USB = [("rts", False), ("dtr", False), ("wait", 0.1), ("dtr", True), ("rts", False), ("wait", 0.1),
       ("rts", True), ("dtr", False), ("rts", True), ("wait", 0.1), ("dtr", False), ("rts", False)]


def test_a_first_install_writes_the_whole_image_compressed_and_starts_it(tmp_path):
    merged, rom, seen = firmware(), Rom(), []
    connected(rom).install(manifest(tmp_path, merged), "first", lambda done, total: seen.append((done, total)))
    assert bytes(rom.flash[:len(merged)]) == merged
    assert 0x11 in rom.commands and 0x03 not in rom.commands
    assert seen[-1] == (len(merged), len(merged)) and len(seen) > 1
    assert [done for done, _ in seen] == sorted(done for done, _ in seen)
    assert rom.booted and not rom.is_open


def test_an_update_writes_only_the_app_selects_ota_0_and_keeps_the_settings(tmp_path):
    merged, rom = firmware(), Rom()
    rom.flash[:len(merged)] = merged
    rom.flash[NVS:NVS + 6] = b"wigle!"
    rom.flash[OTADATA:OTADATA + 32] = b"\x01" * 32
    before = bytes(rom.flash)
    app = firmware(seed=2)[APP:]
    seen = []
    connected(rom).install(manifest(tmp_path, merged, app), "update", lambda done, total: seen.append((done, total)))
    assert bytes(rom.flash[APP:APP + len(app)]) == app
    assert bytes(rom.flash[OTADATA:OTADATA + 0x2000]) == b"\xff" * 0x2000
    assert rom.flash[:OTADATA] == before[:OTADATA] and rom.flash[NVS:NVS + 6] == b"wigle!"
    assert rom.flash[OTADATA + 0x2000:APP] == before[OTADATA + 0x2000:APP]
    assert seen[-1] == (len(app) + 0x2000,) * 2 and rom.booted
    assert [offset for _, offset in rom.begins] == [APP, OTADATA]


def test_an_update_whose_app_would_leave_ota_0_is_refused(tmp_path):
    merged = firmware()
    for change in ({"ota_0": APP + 0x1000}, {"ota_1": APP + 0x4000}):
        rom = Rom()
        rom.flash[:len(merged)] = merged
        path = manifest(tmp_path, merged)
        data = json.loads(path.read_text())
        data["partition_offsets"].update(change)
        path.write_text(json.dumps(data))
        with pytest.raises(FlashError) as caught:
            connected(rom).install(path, "update")
        assert str(caught.value) == flasher.DAMAGED and not rom.begins
    data.update(partition_table_offset=len(merged) - 0x400, partition_offsets={"otadata": OTADATA, "ota_0": APP})
    path.write_text(json.dumps(data))
    with pytest.raises(FlashError, match="damaged"):
        connected(Rom()).install(path, "update")


def test_an_update_is_refused_on_a_board_set_up_another_way(tmp_path):
    merged, rom = firmware(), Rom()
    rom.flash[:len(merged)] = merged
    rom.flash[TABLE] ^= 1
    board = connected(rom)
    with pytest.raises(FlashError, match="Use a first install"):
        board.install(manifest(tmp_path, merged), "update")
    assert 0x10 not in rom.commands and 0x02 not in rom.commands


def test_damaged_firmware_is_never_written(tmp_path):
    merged, rom = firmware(), Rom()
    path = manifest(tmp_path, merged)
    (tmp_path / "merged.bin").write_bytes(merged[:-1] + b"\x01")
    board = connected(rom)
    with pytest.raises(FlashError) as caught:
        board.install(path, "first")
    assert str(caught.value) == flasher.DAMAGED and 0x10 not in rom.commands
    with pytest.raises(FlashError, match="damaged"):
        board.install(tmp_path / "missing.json", "first")


def test_a_compressed_write_of_a_part_block_announces_whole_blocks():
    image, rom = firmware()[:5000], Rom()
    connected(rom).write(image, 0x10000)
    assert bytes(rom.flash[0x10000:0x10000 + len(image)]) == image and 0x11 in rom.commands


def test_a_rom_without_compressed_writes_gets_plain_blocks():
    image, rom = firmware()[:5000], Rom(deflate=False)
    board = connected(rom)
    board.write(image, 0x10000)
    assert bytes(rom.flash[0x10000:0x10000 + len(image)]) == image
    assert 0x03 in rom.commands and 0x11 not in rom.commands
    assert not board.deflate


def test_a_corrupt_block_is_sent_again():
    image, rom = firmware(), Rom()
    board = connected(rom)
    rom.corrupt = 2
    board.write(image, 0)
    assert bytes(rom.flash[:len(image)]) == image


def test_a_block_that_stays_corrupt_fails_in_plain_words():
    rom = Rom()
    board = connected(rom)
    rom.corrupt = 3
    with pytest.raises(FlashError) as caught:
        board.write(firmware(), 0)
    assert str(caught.value) == flasher.FAILED


def test_a_bad_compressed_write_is_written_again_plainly_and_a_bad_plain_one_fails():
    image, rom, seen = firmware(), Rom(), []
    board = connected(rom)
    rom.garble = 1
    board.write(image, 0, lambda done, total: seen.append(done))
    assert bytes(rom.flash[:len(image)]) == image and 0x03 in rom.commands
    restarts = [i for i in range(1, len(seen)) if seen[i] < seen[i - 1]]
    assert len(restarts) == 1 and seen[restarts[0] - 1] == seen[-1] == len(image)
    rom.garble = 2
    with pytest.raises(FlashError) as caught:
        board.write(image, 0)
    assert str(caught.value) == flasher.MISMATCH


def test_a_board_that_stops_answering_mid_write_says_so():
    rom = Rom()
    board = connected(rom)
    rom.answers = 20
    with pytest.raises(FlashError) as caught:
        board.write(firmware(), 0)
    assert str(caught.value) == flasher.LOST


def test_other_chips_are_refused():
    assert refusal(Rom(chip_id=5)) == "This is not an ESP32-C5."
    assert refusal(Rom(chip_id=None, magic=0x00F01D83)) == "This is not an ESP32-C5."
    assert connected(Rom(chip_id=None)).flash_size == 4 << 20


def test_early_samples_small_flash_and_locked_boards_are_refused():
    assert refusal(Rom(revision=(0, 1))) == \
        "This ESP32-C5 is an early sample (revision 0.1) and cannot run the node firmware."
    assert refusal(Rom(flash_mb=2)) == "This board has 2 MB of flash. A node needs 4 MB or more."
    assert refusal(Rom(secure=True)) == flasher.LOCKED
    board = connected(Rom(revision=(1, 2), flash_mb=8))
    assert board.revision == (1, 2) and board.flash_size == 8 << 20


def test_a_flash_chip_that_does_not_answer_is_said_so():
    rom = Rom()
    rom.jedec = 0xFFFFFF
    assert refusal(rom) == "The flash chip of this board did not answer."


def test_a_board_that_does_not_answer_asks_for_boot_to_be_held():
    rom = Rom(enters=())
    assert refusal(rom) == "No ESP32-C5 answered. Hold BOOT, plug the board in again, and retry."
    assert not rom.commands
    assert connected(Rom(enters=(), held=True)).flash_size == 4 << 20


def test_a_running_node_is_asked_for_its_rom_loader_before_any_reset():
    rom = Rom(enters=(), node=True)
    connected(rom, native_usb=False)
    assert rom.in_rom and lines(rom.events)[:1] == [("wait", 0.05)]
    assert rom.asked_baud == 460800 * 40 // 48


def test_a_board_slow_to_sync_or_to_wake_from_a_reset_is_given_its_time():
    assert connected(Rom(deaf=4)).flash_size
    rom = Rom(enters=("usb",), misses=1)
    connected(rom, native_usb=True)
    assert lines(rom.events)[:2 * len(USB)] == USB * 2
    rom = Rom(enters=("classic",), en_delay=0.5)
    connected(rom, native_usb=False)
    slow = CLASSIC[:5] + [("wait", 0.55)] + CLASSIC[6:]
    assert lines(rom.events)[:2 * len(CLASSIC)] == CLASSIC + slow


def test_opening_the_port_never_shows_the_board_rts_alone():
    rom = Rom()
    flasher_for(rom).open()
    assert rom.events == [("dtr", True), ("rts", False), ("open", True, False), ("dtr", False)]
    assert rom.glitches == 0 and not rom.dtr and not rom.rts
    assert rom.port == "/dev/ttyACM0" and rom.baudrate == 115200 and rom.exclusive and not rom.in_rom


def test_the_usb_serial_jtag_reset_comes_first_on_a_native_port():
    rom = Rom()
    connected(rom, native_usb=True)
    assert lines(rom.events)[:len(USB) + 1] == USB + [("wait", 0.05)]


def test_the_classic_reset_comes_first_on_a_uart_bridge_and_the_other_follows():
    rom = Rom(enters=("usb",))
    connected(rom, native_usb=False)
    events = lines(rom.events)
    assert events[:len(CLASSIC)] == CLASSIC
    assert events[len(CLASSIC):len(CLASSIC) + 7] == CLASSIC[:5] + [("wait", 0.55), ("dtr", False)]
    assert events[2 * len(CLASSIC):3 * len(CLASSIC) - 2] == USB[:len(CLASSIC) - 2]
    assert rom.in_rom and 0x0F not in rom.commands


def test_a_uart_bridge_is_sped_up_for_the_crystal_the_rom_expects():
    rom = Rom(crystal=(48, 40))
    connected(rom, native_usb=False)
    assert rom.asked_baud == 460800 * 40 // 48 and rom.baudrate == 460800
    rom = Rom(crystal=(48, 48))
    connected(rom, native_usb=False)
    assert rom.asked_baud == 460800 and 0x0F in rom.commands


def test_the_watchdogs_are_calmed_on_every_port_and_a_native_one_keeps_its_speed():
    for native_usb in (True, False):
        rom = Rom()
        rom.regs[0x600B1C00] = 0xC0000000
        connected(rom, native_usb=native_usb)
        assert (0x0F in rom.commands) is not native_usb
        assert rom.regs[0x600B1C00] == 0 and rom.regs[0x600B1C1C] & 1 << 18
        assert rom.regs[0x600B1C18] == 0 and rom.regs[0x600B1C20] == 0


def test_the_hard_reset_pulses_en_longer_on_a_native_port():
    for native_usb, tail in ((False, []), (True, [("wait", 0.2)])):
        rom = Rom()
        board = connected(rom, native_usb=native_usb)
        del rom.events[:]
        board.hard_reset()
        hold = 0.2 if native_usb else 0.1
        assert rom.events == [("dtr", False), ("rts", True), ("wait", hold), ("rts", False)] + tail + [("close",)]
        assert rom.booted and board.port is None
        board.hard_reset()


def test_the_flash_is_read_only_through_the_spi_registers_as_the_rom_needs():
    assert refusal(Rom(busy=99)) == flasher.NO_FLASH
    assert connected(Rom(busy=5)).flash_size == 4 << 20
    rom = connected(Rom()).port
    assert rom.attached and rom.params == 4 << 20 and rom.regs[0x60003018] == 0x80000064


def test_failures_while_getting_ready_or_used_wrongly_are_plain_words(monkeypatch):
    assert refusal(Rom(refuse=(0x0D,))) == flasher.UNREADY
    assert refusal(Rom(revision=(2, 0))) == \
        "This ESP32-C5 is a newer revision (2.0) than the node firmware knows. Update Quartermaster."
    board = connected(Rom())
    with pytest.raises(FlashError, match="does not fit"):
        board.write(b"\x01" * 0x2000, (4 << 20) - 0x1000)
    board.hard_reset()
    with pytest.raises(FlashError) as caught:
        board.write(b"\x01", 0)
    assert str(caught.value) == flasher.LOST
    with pytest.raises(FlashError, match="stopped answering"):
        board.install(flasher.MANIFEST)
    monkeypatch.setitem(sys.modules, "serial", None)
    with pytest.raises(FlashError) as caught:
        Flasher("/dev/ttyACM0").open()
    assert str(caught.value) == flasher.NO_SERIAL


def test_the_bundled_node_firmware_matches_its_manifest():
    if not os.path.exists(flasher.MANIFEST):
        pytest.skip("no node firmware bundled")
    manifest, piece = flasher.load()
    for name in ("merged", "app"):
        data, offset = piece(name)
        assert offset == manifest[name]["offset"] and len(data) == manifest[name]["size"]
