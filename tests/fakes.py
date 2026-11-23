"""Stand-ins for the hardware, shared by the tests and the screenshot renderer."""

import functools
import hashlib
import io
import operator
import os
import random
import struct
import zlib

from quartermaster.wifi import band_of, channel_of

FIX = {"valid": True, "latitude": 25.07805, "longitude": -77.34312, "satellites": 14, "hdop": 0.8, "age_seconds": 0.3,
       "source": "CP210x USB GPS (/dev/ttyUSB0)", "altMSL": 96.0, "status": "fix"}
NO_FIX = {"valid": False, "source": "none", "status": "no_fix", "age_seconds": None}
BATTERY = {"percent": 87, "charging": False}
ADAPTERS = [
    {"interface": "wlan1", "phy": "phy1", "driver": "mt7921u", "external": True, "usb_id": "0e8d:7961",
     "label": "MT7921AU tri-band (ALFA AWUS036AXM)", "bands": ["2.4", "5", "6"], "band_label": "tri-band",
     "monitor": True},
    {"interface": "wlan2", "phy": "phy2", "driver": "rtw_8812au", "external": True, "usb_id": "0bda:8812",
     "label": "RTL8812AU dual-band", "bands": ["2.4", "5"], "band_label": "dual-band", "monitor": True},
    {"interface": "wlan0", "phy": "phy0", "driver": "brcmfmac", "external": False, "usb_id": "",
     "label": "Broadcom (built-in)", "bands": ["2.4"], "band_label": "single-band", "monitor": False},
]
NAMES = ["Black Pearl", "Flying Dutchman 5G", "xfinitywifi", "", "Tortuga Guest", "NETGEAR88-6G", "eduroam",
         "Jolly Roger IoT", "DIRECT-roku-471", "Davy Jones", "ATTm9xK2s", "Queen Anne's Revenge", "Kraken_6E"]
SECURITY = [("WPA2", "[WPA2-PSK-CCMP][ESS]"), ("WPA2", "[WPA2-PSK-CCMP][ESS]"), ("WPA3", "[RSN-SAE-CCMP][ESS]"),
            ("OPEN", "[ESS]"), ("EAP", "[WPA2-EAP-CCMP][ESS]")]


def networks(count=412, seed=7):
    rnd = random.Random(seed)
    found = []
    for i in range(count):
        freq = rnd.choice([2412, 2437, 2462, 5180, 5745, 5500, 6135, 5955, 2437, 5220])
        security, capabilities = rnd.choice(SECURITY)
        found.append({"bssid": "AA:BB:CC:%02X:%02X:%02X" % (i >> 8, i & 255, rnd.randrange(256)),
                      "ssid": NAMES[i % len(NAMES)], "rssi": rnd.randrange(-92, -38), "frequency": freq,
                      "channel": channel_of(freq), "band": band_of(freq), "security": security,
                      "capabilities": capabilities})
    return found


class Card(io.RawIOBase):
    """The file of a log on a card that takes a number of bytes more, refuses, and has room again after that."""

    def __init__(self, fd, room, error, then):
        self.fd, self.room, self.then = fd, room, then
        self.error = error or OSError(28, "No space left on device")

    def writable(self):
        return True

    def fileno(self):
        return self.fd

    def write(self, data):
        if not self.room:
            self.room, self.then = self.then, 0
            raise self.error
        taken = os.write(self.fd, bytes(data[:self.room]))
        self.room -= taken
        return taken

    def close(self):
        if not self.closed:
            os.close(self.fd)
        super().close()


def on_a_card(writer, room, error=None, then=0):
    """From now on the log is on a card with room for `room` bytes and, once it has refused, for `then` more."""
    card = Card(os.open(writer.path, os.O_WRONLY | os.O_APPEND), room, error, then)
    before = writer.file
    writer.file = io.TextIOWrapper(io.BufferedWriter(card), encoding="utf-8", newline="")
    before.close()


def cards(monkeypatch, room, error=None, then=0):
    """From now on every log and summary that is started is on such a card."""
    def fdopen(fd, *args, **kwargs):
        return io.TextIOWrapper(io.BufferedWriter(Card(fd, room, error, then)), encoding="utf-8", newline="")
    monkeypatch.setattr(os, "fdopen", fdopen)


class Gps:
    mode, power = "auto", True

    def __init__(self, fix=FIX):
        self.fix = fix

    def snapshot(self):
        return dict(self.fix)


class Display:
    def __init__(self):
        self.frames = 0
        self.last = None

    def show(self, image):
        self.frames += 1
        self.last = image

    def close(self):
        pass


class Hardware:
    def __init__(self, adapters=ADAPTERS, stalled=(), addresses=("192.168.1.50",), battery=BATTERY):
        self.adapters = list(adapters)
        self.battery = battery
        self.stalled = list(stalled)
        self.bluetooth = [{"name": "hci0", "external": False, "label": "Built-in Bluetooth"}]
        self.gps_ports = [{"path": "/dev/ttyUSB0", "label": "CP210x USB GPS", "kind": "usb", "likely": True},
                          {"path": "/dev/ttyS0", "label": "Cap / Grove UART", "kind": "uart", "likely": True}]
        self._addresses = list(addresses)

    def addresses(self):
        return list(self._addresses)

    def start(self):
        pass

    def stop(self):
        pass


class Fleet:
    """Reports one sweep of sample networks from the first adapter."""

    found = None

    def __init__(self, adapters, sink, interval):
        self.adapters, self.sink = adapters, sink

    def start(self):
        if self.adapters:
            self.sink(self.found if self.found is not None else networks(), self.adapters[0])

    def stop(self):
        pass

    def status(self):
        return [{"interface": a["interface"], "method": "iw", "sweeps": 12, "sweep_seconds": 3.1, "error": "",
                 "label": a.get("label", ""), "bands": a.get("bands", [])} for a in self.adapters]


class QuietFleet(Fleet):
    def start(self):
        pass


class Ble:
    def __init__(self, devices=()):
        self.devices = [{"address": address, "name": name, "rssi": rssi, "address_type": 1, "classic": False,
                         "manufacturer": {}} for address, name, rssi in devices]

    def scan(self):
        return [dict(device) for device in self.devices]

    def close(self):
        pass


class NoBle(Ble):
    pass


class Silent:
    def play(self):
        return False


class Rom:
    """An ESP32-C5 behind a serial port that is not open yet. It is in its ROM loader after a reset sequence named in
    enters ("classic", "usb"), after #BOOTLOADER at 921600 baud when it runs the node firmware, or all along while
    BOOT is held. Its flash is a bytearray. misses: resets it sleeps through; deaf: syncs it ignores after each reset;
    en_delay: how long its EN line needs before IO0 is let go; busy: reads of the SPI command register before the
    flash answers (99: never); glitches: times opening showed it RTS alone."""

    CLASSIC = (("dtr", False), ("rts", True), ("dtr", True), ("rts", False), ("dtr", False))
    USB = (("rts", False), ("dtr", False), ("dtr", True), ("rts", False), ("rts", True), ("dtr", False), ("rts", True),
           ("dtr", False), ("rts", False))

    def __init__(self, chip_id=23, magic=0x1101406F, revision=(1, 0), flash_mb=4, enters=("classic", "usb"),
                 held=False, secure=False, deflate=True, corrupt=0, garble=0, answers=None, crystal=(48, 40),
                 node=False, refuse=(), misses=0, deaf=0, en_delay=0.0, busy=0):
        self.chip_id, self.magic, self.secure, self.deflate = chip_id, magic, secure, deflate
        self.enters, self.held, self.corrupt, self.garble, self.answers = enters, held, corrupt, garble, answers
        self.node, self.refuse, self.misses, self.deaf = node, refuse, misses, deaf
        self.en_delay, self.busy = en_delay, busy
        self.crystal, self.rom_baud, self._deaf, self._pause, self.glitches = crystal, 115200, deaf, 0.0, 0
        self._wake, self._busy = 0.0, 0
        self.attached, self.params, self.begins = False, None, []
        self.flash = bytearray(b"\xff" * int(flash_mb * (1 << 20)))
        self.regs = {0x40001000: magic, 0x600B4800 + 0x44 + 8: revision[0] << 4 | revision[1],
                     0x60096110: crystal[1] << 24, 0x60000014: round(crystal[0] * 1e6 / 115200),
                     0x60003018: 0x80000064, 0x60003020: 0x70000003, 0x60003058: 0x12345678}
        self.spi_usr = (0x80000064, 0x70000003)
        self.jedec = 0x4020 | (len(self.flash).bit_length() - 1) << 16
        self.now, self.events, self.commands, self.asked_baud = 0.0, [], [], None
        self.port, self.baudrate, self.timeout, self.write_timeout, self.exclusive = None, 9600, None, None, False
        self.is_open, self.in_rom, self.booted = False, held, False
        self._dtr = self._rts = True
        self._recent, self._in, self._out, self._session = [], bytearray(), bytearray(), None

    def clock(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds
        self._pause += seconds
        self.events.append(("wait", seconds))

    @property
    def dtr(self):
        return self._dtr

    @dtr.setter
    def dtr(self, value):
        self._line("dtr", value)

    @property
    def rts(self):
        return self._rts

    @rts.setter
    def rts(self, value):
        self._line("rts", value)

    def _line(self, name, value):
        fell = name == "rts" and self._rts and not value
        setattr(self, "_" + name, value)
        self.events.append((name, value))
        pause, self._pause = self._pause, 0.0
        if not self.is_open:
            return
        self._recent = (self._recent + [(name, value)])[-len(self.USB):]
        sequences = {"classic": self.CLASSIC, "usb": self.USB}
        matched = [kind for kind in self.enters if tuple(self._recent[-len(sequences[kind]):]) == sequences[kind]]
        if matched and (matched[0] == "usb" or pause >= self.en_delay):
            if self.misses:
                self.misses -= 1
            else:
                self._enter_rom()
        elif fell and not self._dtr:
            self.in_rom, self.booted = self.held, not self.held

    def _enter_rom(self, delay=0.0):
        self.in_rom, self.booted, self.rom_baud, self._deaf = True, False, 115200, self.deaf
        self._wake = self.now + delay
        self._out.clear()
        self._out += b"ESP-ROM:esp32c5-eco2-20250121\r\n"

    def open(self):
        self.is_open = True
        self.events.append(("open", self._dtr, self._rts))
        self.glitches += (False, True) in ((True, True), (self._dtr, True), (self._dtr, self._rts))

    def flush(self):
        pass

    def close(self):
        self.is_open = False
        self.events.append(("close",))

    @property
    def in_waiting(self):
        return len(self._out)

    def read(self, size=1):
        if not self._out:
            self.now += self.timeout or 0
            return b""
        data = bytes(self._out[:size])
        del self._out[:size]
        return data

    def reset_input_buffer(self):
        self._out.clear()

    def write(self, data):
        if self.node and not self.in_rom and self.baudrate == 921600 and b"#BOOTLOADER\n" in data:
            self._enter_rom(1.0)
            return len(data)
        self._in += data
        while True:
            start = self._in.find(0xC0)
            end = self._in.find(0xC0, start + 1)
            if start < 0 or end < 0:
                return len(data)
            frame = bytes(self._in[start + 1:end]).replace(b"\xdb\xdc", b"\xc0").replace(b"\xdb\xdd", b"\xdb")
            del self._in[:end + 1]
            if self.in_rom and self.answers != 0 and self.baudrate == self.rom_baud and self.now >= self._wake:
                if self.answers is not None:
                    self.answers -= 1
                op, size, chk = struct.unpack_from("<xBHI", frame)
                self.commands.append(op)
                self._command(op, frame[8:8 + size], chk)

    def _answer(self, op, value=0, data=b"", status=0, reason=0):
        packet = struct.pack("<BBHI", 1, op, len(data) + 4, value) + data + bytes((status, reason, 0, 0))
        self._out += b"\xc0" + packet.replace(b"\xdb", b"\xdb\xdd").replace(b"\xc0", b"\xdb\xdc") + b"\xc0"

    def _rdid(self):
        usr, usr2, dlen = self.regs[0x60003018], self.regs[0x60003020], self.regs.get(0x60003028)
        if usr & 0x90000000 == 0x90000000 and usr2 == 0x7000009F and dlen == 23:
            self.regs[0x60003058] = self.jedec
        self.regs[0x60003000] = 0

    def _command(self, op, data, chk):
        ready = self.attached and self.params == len(self.flash) and self.spi_usr == (
            self.regs[0x60003018], self.regs[0x60003020])
        if op in self.refuse or op in (0x02, 0x10, 0x13) and not ready:
            self._answer(op, status=1, reason=0x05)
        elif op == 0x08 and self._deaf:
            self._deaf -= 1
        elif op == 0x08 and data == b"\x07\x07\x12\x20" + b"\x55" * 32:
            for _ in range(8):
                self._answer(op, 0x20120707)
        elif op == 0x0A:
            address = struct.unpack("<I", data)[0]
            if address == 0x60003000 and self._busy:
                self._busy -= 1
                if not self._busy:
                    self._rdid()
            self._answer(op, self.regs.get(address, 0))
        elif op == 0x09:
            address, value = struct.unpack_from("<II", data)
            self.regs[address] = value
            if address == 0x60003000 and value & 1 << 18:
                self._busy = self.busy
                if not self._busy:
                    self._rdid()
            self._answer(op)
        elif op == 0x14 and self.chip_id is not None:
            self._answer(op, data=struct.pack("<I8BII", self.secure << 2, *[0] * 8, self.chip_id, 0))
        elif op == 0x0D:
            self.attached = True
            self._answer(op)
        elif op == 0x0B:
            self.params = struct.unpack_from("<II", data)[1]
            self._answer(op)
        elif op == 0x0F:
            self.asked_baud = struct.unpack_from("<I", data)[0]
            self._answer(op)
            self.rom_baud = self.asked_baud * self.crystal[0] // self.crystal[1]
        elif op in (0x02, 0x10) and (op == 0x02 or self.deflate):
            size, blocks, block, offset, encrypted = struct.unpack("<5I", data)
            if encrypted or op == 0x10 and size % 0x400:
                return self._answer(op, status=1, reason=0x05)
            self.begins.append((op, offset))
            end = offset + -(-size // 4096) * 4096
            self.flash[offset:end] = b"\xff" * (end - offset)
            self._session = {"offset": offset, "at": offset, "seq": 0, "block": block,
                             "unpack": zlib.decompressobj() if op == 0x10 else None, "garble": self.garble > 0}
            self.garble -= 1
            self._answer(op)
        elif op in (0x03, 0x11) and self._session:
            length, seq = struct.unpack_from("<II", data)
            block, session = data[16:16 + length], self._session
            short = op == 0x03 and length != session["block"]
            if self.corrupt or short or chk != functools.reduce(operator.xor, block, 0xEF) or seq != session["seq"]:
                self.corrupt = max(0, self.corrupt - 1)
                return self._answer(op, status=1, reason=0x07)
            out = session["unpack"].decompress(block) if session["unpack"] else block
            if session["garble"] and session["at"] == session["offset"]:
                out = bytes((out[0] ^ 1,)) + out[1:]
            self.flash[session["at"]:session["at"] + len(out)] = out
            session["at"] += len(out)
            session["seq"] += 1
            self._answer(op)
        elif op == 0x13:
            offset, size = struct.unpack_from("<II", data)
            self._answer(op, data=hashlib.md5(bytes(self.flash[offset:offset + size])).hexdigest().encode())
        else:
            self._answer(op, status=1, reason=0x05)


NODE_COUNTRIES = ("01", "AT", "AU", "BE", "BG", "BR", "CA", "CH", "CN", "CY", "CZ", "DE", "DK", "EE", "ES", "FI", "FR",
                  "GB", "GR", "HK", "HR", "HU", "IE", "IN", "IS", "IT", "JP", "KR", "LI", "LT", "LU", "LV", "MT", "MX",
                  "NL", "NO", "NZ", "PL", "PT", "RO", "SE", "SI", "SK", "TW", "US")
GHZ5 = (36, 40, 44, 48, 52, 56, 60, 64, 100, 104, 108, 112, 116, 120, 124, 128, 132, 136, 140, 144, 149, 153, 157, 161,
        165, 169, 173, 177)
GGA = b"$GPGGA,123519,4807.038,N,01131.000,E,1,08,0.9,545.4,M,46.9,M,,*47\r\n"


def line_xor(text):
    return functools.reduce(operator.xor, text.encode("ascii"), 0)


class Clock:
    """Time for the fakes. It moves when a test says so, and while a port waits for something to read."""

    def __init__(self, now=1000.0):
        self.now = now

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


class FakeNode:
    """A Quartermaster Node as firmware/PROTOCOL.md describes it, on the far side of a FakePort.

    wifi() and ble() put a sighting on the air: the node reports it only while it runs, only on a channel of its
    plan, and Bluetooth only when the plan asks for it. garble: the next lines carry a wrong checksum. lose: the next
    lines are counted but never sent."""

    def __init__(self, node_id="a0b1c2d3e4f5", clock=None, caps="wifi24+wifi5+ble", board="c5-8mb", flash_mb=8,
                 psram_mb=8, firmware="1.0.0", protocol=1):
        self.node_id, self.caps, self.board, self.firmware, self.protocol = node_id, caps, board, firmware, protocol
        self.flash_mb, self.psram_mb = flash_mb, psram_mb
        self.clock = clock or Clock()
        self.plugged, self.taken = True, False
        self.country, self.led = "01", None
        self.commands, self.idents, self.resets = [], [], 0
        self.garble = self.lose = 0
        self.out, self._in = bytearray(), b""
        self._boot()

    def _boot(self):
        self.seq, self.plan, self.running = 0, None, False
        self.sweeps = self.networks = self.devices = self.dropped = 0
        self.last_host = self.last_status = self.last_hello = self.clock()
        self.out += b"ESP-ROM:esp32c5-eco2-20250121\r\nrst:0x1 (POWERON),boot:0x8 (SPI_FAST_FLASH_BOOT)\r\n"
        self._hello()

    def reset(self):
        self.resets += 1
        self._boot()

    def unplug(self):
        self.plugged = False

    def replug(self):
        self.plugged, self.taken = True, False
        self.out.clear()
        self.reset()

    def _emit(self, kind, *fields, seq=True):
        if seq:
            fields = (str(self.seq),) + fields
            self.seq = (self.seq + 1) & 0xFFFFFFFF
        body = ",".join((kind,) + fields)
        if self.lose:
            self.lose -= 1
            return
        check = line_xor(body)
        if self.garble:
            self.garble -= 1
            check ^= 0x5A
        self.out += ("@%s*%02X\n" % (body, check)).encode("ascii")

    def _hello(self):
        self.last_hello = self.clock()
        self._emit("H", str(self.protocol), self.node_id, self.firmware, "1.0", str(self.flash_mb), str(self.psram_mb),
                   self.board, self.caps, self.country, str(self.plan["version"] if self.plan else 0),
                   "run" if self.running else "idle", seq=False)

    def _ack(self, name, ok, detail=""):
        self._emit("A", name, "ok" if ok else "err", detail)

    def tick(self):
        now = self.clock()
        if self.running and now - self.last_host >= 30:
            self.running = False
            self._emit("L", "no host for 30 s_ stopped")
        if self.running and now - self.last_status >= 2:
            self.last_status = now
            self.sweeps += 1
            self._emit("S", str(int(now * 1000) % 100000000), str(self.sweeps), "1800", str(self.networks),
                       str(self.devices), str(self.dropped), str(self.plan["version"]), "180224", "run")
        if now - self.last_hello >= (30 if self.running else 10):
            self._hello()

    def receive(self, data):
        self._in += data
        *lines, self._in = self._in.split(b"\n")
        for raw in lines:
            line = raw.decode("ascii", "replace").rstrip("\r ")
            if not line.startswith("#") or len(line) < 2:
                continue
            body, star, check = line[1:].partition("*")
            self.last_host = self.clock()
            name, *args = body.split(",")
            if star and (len(check) != 2 or not all(c in "0123456789abcdefABCDEF" for c in check)
                         or int(check, 16) != line_xor(body)):
                self._ack(name, False, "checksum")
                continue
            self.commands.append(name)
            self._command(name, args)

    def _command(self, name, args):
        if name == "HELLO":
            self._ack(name, True)
            self._hello()
        elif name == "PLAN":
            refused = self._plan(args)
            self._ack(name, not refused, refused or args[0])
        elif name == "START":
            if self.plan is None:
                self.plan = {"version": 0, "country": self.country, "ghz2": 0x7FFE, "ghz5": 0x1FFFFFFE, "ble": False}
            self.running, self.sweeps, self.networks, self.devices = True, 0, 0, 0
            self.last_status = self.clock()
            self._ack(name, True)
        elif name == "STOP":
            self.running = False
            self._ack(name, True)
        elif name == "IDENT":
            seconds = args[0] if args else "5"
            ok = len(args) <= 1 and seconds.isdigit() and 1 <= int(seconds) <= 60
            if ok:
                self.idents.append(int(seconds))
            self._ack(name, ok, seconds if ok else "seconds")
        elif name == "LED":
            self.led = args
            self._ack(name, True)
        elif name in ("BOOTLOADER", "REBOOT"):
            self._ack(name, True)
            self.reset()
        else:
            self._ack(name, False, "unknown")

    def _plan(self, args):
        """None for a plan the node takes, else the first field it refuses, as proto_plan() does."""
        def number(text, base, top):
            digits = "0123456789" if base == 10 else "0123456789abcdefABCDEF"
            if not text or len(text) > 10 or any(c not in digits for c in text) or int(text, base) > top:
                return None
            return int(text, base)
        if len(args) != 8:
            return "args"
        version = number(args[0], 10, 0xFFFFFFFF)
        if not version:
            return "version"
        if args[1] not in NODE_COUNTRIES:
            return "country"
        ghz2, ghz5 = number(args[2], 16, 0x7FFF), number(args[3], 16, 0x1FFFFFFF)
        if ghz2 is None or ghz5 is None:
            return "bitmap"
        times = [number(value, 10, 1500) for value in args[4:7]]
        if None in times or times[1] == 0 or times[0] > times[1] or times[2] == 0:
            return "time"
        if args[7] not in ("0", "1"):
            return "ble"
        self.plan = {"version": version, "country": args[1], "ghz2": ghz2, "ghz5": ghz5, "ble": args[7] == "1",
                     "times": tuple(times)}
        self.country = args[1]
        self.networks = self.devices = 0
        return None

    def scans(self, channel):
        plan = self.plan
        if channel <= 14:
            return not plan["ghz2"] & 1 and bool(plan["ghz2"] >> channel & 1)
        return channel in GHZ5 and not plan["ghz5"] & 1 and bool(plan["ghz5"] >> (GHZ5.index(channel) + 1) & 1)

    def wifi(self, bssid, ssid=b"", rssi=-60, channel=6, auth=3, pairwise=4, group=4, flags=0, age=200):
        """Hear a network. True when the node reports it."""
        if not self.running or not self.scans(channel):
            return False
        freq = 2484 if channel == 14 else 2407 + 5 * channel if channel < 14 else 5000 + 5 * channel
        self.networks += 1
        self._emit("W", str(age), bssid.upper(), str(rssi), str(channel), str(freq), str(auth), str(pairwise),
                   str(group), "%x" % flags, ssid.hex())
        return True

    def ble(self, address, rssi=-70, kind=1, flags=1, maker=None, name=b"", age=100):
        if not self.running or not self.plan["ble"]:
            return False
        self.devices += 1
        self._emit("B", str(age), address.upper(), str(kind), str(rssi), "%x" % flags,
                   "" if maker is None else "%04x" % maker, name.hex())
        return True


class Talker:
    """Something on a USB serial port that is not a node: it says its text over and over and records what it got."""

    def __init__(self, text=GGA):
        self.text, self.plugged, self.taken = text, True, False
        self.out, self.heard = bytearray(), bytearray()

    def tick(self):
        if len(self.out) < 4 * len(self.text):
            self.out += self.text

    def receive(self, data):
        self.heard += data

    def reset(self):
        pass


class FakeBus:
    """The devices on the USB serial ports by path. factory() builds an unopened port, as serial.Serial() does."""

    def __init__(self, clock=None):
        self.clock = clock or Clock()
        self.devices, self.ports = {}, []

    def add(self, path, device):
        self.devices[os.path.realpath(str(path))] = device
        return device

    def factory(self):
        port = FakePort(self)
        self.ports.append(port)
        return port


class FakePort:
    """Just enough of pyserial's Serial. RTS up while DTR is down resets the device, as on an ESP32-C5 board."""

    def __init__(self, bus):
        self.bus, self.device = bus, None
        self.port, self.baudrate, self.timeout, self.write_timeout, self.exclusive = None, 9600, None, None, False
        self._dtr = self._rts = True
        self.is_open, self.events = False, []

    @property
    def dtr(self):
        return self._dtr

    @dtr.setter
    def dtr(self, value):
        self._dtr = value
        self._lines()

    @property
    def rts(self):
        return self._rts

    @rts.setter
    def rts(self, value):
        self._rts = value
        self._lines()

    def _lines(self):
        self.events.append((self._dtr, self._rts))
        if self.is_open and self._rts and not self._dtr:
            self.device.reset()

    def open(self):
        device = self.bus.devices.get(os.path.realpath(str(self.port)))
        if device is None or not device.plugged or self.exclusive and device.taken:
            raise OSError(2, "could not open port %s" % self.port)
        self.device, self.is_open, device.taken = device, True, True
        self.events.append(("open", self.baudrate))
        self._lines()

    def close(self):
        if self.is_open:
            self.is_open, self.device.taken = False, False

    def _alive(self):
        if not self.is_open or not self.device.plugged:
            raise OSError(5, "Input/output error")
        self.device.tick()

    @property
    def in_waiting(self):
        self._alive()
        return len(self.device.out)

    def read(self, size=1):
        self._alive()
        if not self.device.out:
            self.bus.clock.advance(self.timeout or 0)
            self.device.tick()
        data = bytes(self.device.out[:size])
        del self.device.out[:size]
        self.bus.clock.advance(len(data) * 10 / 921600)
        return data

    def write(self, data):
        self._alive()
        self.device.receive(bytes(data))
        return len(data)


def usb_serial(root, ports):
    """A /sys/class/tty, /dev and /dev/serial/by-id with USB serial ports, given as {name: (usb id, by-id name)}."""
    sys_tty, dev, by_id = root / "tty", root / "dev", root / "by-id"
    for folder in (sys_tty, dev, by_id):
        folder.mkdir(parents=True, exist_ok=True)
    for name, (usb_id, link) in ports.items():
        usb = root / "usb" / name
        (usb / "1-1:1.0").mkdir(parents=True)
        vendor, product = usb_id.split(":")
        (usb / "idVendor").write_text(vendor + "\n")
        (usb / "idProduct").write_text(product + "\n")
        (sys_tty / name).mkdir()
        (sys_tty / name / "device").symlink_to(usb / "1-1:1.0")
        (dev / name).write_text("")
        if link:
            (by_id / link).symlink_to(dev / name)
    return sys_tty, dev, by_id


class Boards(Hardware):
    """Hardware with the USB serial ports an ESP32-C5 board may be on, as HardwareWatch lists them."""

    def __init__(self, serial_ports=(), **kwargs):
        super().__init__(**kwargs)
        self.serial_ports = [dict(port) for port in serial_ports]


class FakeFlasher:
    """Stands in for flasher.Flasher. Each step goes in log; error is raised at the step named in at. While gate is
    set, writing waits half way for it to open."""

    def __init__(self, log, error="", at="connect", gate=None, size=1136288, during=None):
        self.log, self.error, self.at, self.gate, self.size, self.during = log, error, at, gate, size, during
        self.path = None

    def __call__(self, path):
        self.path = path
        self.log.append(("open", path))
        return self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.log.append(("close",))

    def connect(self, native_usb):
        self.log.append(("connect", native_usb, self.during() if self.during else None))
        self._fail("connect")

    def install(self, manifest_path, mode="first", progress=None):
        self.log.append(("install", mode))
        progress(self.size // 2, self.size)
        if self.gate is not None:
            self.gate.wait(5)
        self._fail("install")
        progress(self.size, self.size)

    def _fail(self, step):
        from quartermaster.flasher import FlashError
        if self.at == step and self.error:
            raise FlashError(self.error)
