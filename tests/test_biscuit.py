import json
import socket
import struct
import threading
import time

import pytest

from quartermaster import biscuit, radio_helper
from quartermaster.ui import biscuit_screen, screens
from tests import test_pi_words, test_radiod
from tests.test_pi_words import look, said
from tests.test_radiod import ask, radiod, session
from tests.test_ui import make_app, press

device = test_pi_words.device
radios = test_radiod.radios

PAGER = bytes.fromhex("00c0ca000002")
BISCUIT = bytes.fromhex("24ec4a0000b1")
OTHER = bytes.fromhex("24ec4a0000c2")
NET6 = bytes.fromhex("aa0000000006")
NET24 = bytes.fromhex("aa0000000024")


def radiotap(mhz, rssi):
    return struct.pack("<BBHI", 0, 0, 13, 1 << 3 | 1 << 5) + struct.pack("<HHb", mhz, 0, rssi)


def beacon(bssid, ssid, akm=8, he_channel=None):
    head = struct.pack("<HH6s6s6sH", 0x0080, 0, b"\xff" * 6, bssid, bssid, 0) + b"\0" * 8
    head += struct.pack("<HH", 100, 0x0011)
    rsn = struct.pack("<H", 1) + b"\x00\x0f\xac\x04" + struct.pack("<H", 1) + b"\x00\x0f\xac\x04" + \
        struct.pack("<H", 1) + b"\x00\x0f\xac" + bytes([akm]) + b"\0\0"
    out = head + bytes([0, len(ssid)]) + ssid + bytes([48, len(rsn)]) + rsn
    if he_channel:
        he = bytes([36]) + (1 << 17).to_bytes(3, "little") + b"\0\0\0" + bytes([he_channel, 0, 0, 0, 0])
        out += bytes([255, len(he)]) + he
    return out


class FakeBiscuit:
    def __init__(self, reply=b"wdbatch=1 nodecfg=1 ap6=1", mac=BISCUIT):
        self.reply, self.mac = reply, mac
        self.firmware, self.texts, self.acks, self.records = [], [], [], []
        self.assigned = False
        self.seq = 0

    def heard(self, dst, payload, out):
        if payload[4] == radiod.WARDRIVE_BATCH:
            if dst == self.mac:
                body, at = payload[8:], 0
                for _ in range(payload[7]):
                    size = body[at + 10]
                    self.records.append(body[at:at + 11 + size])
                    at += 11 + size
            return
        kind, counter, text = radiod.read_enow(payload)
        if kind == radiod.CORE_REQUEST and dst == radiod.BROADCAST:
            out.append(self.say(radiod.enow(radiod.CORE_REPLY, 0, self.reply)))
        if dst != self.mac:
            return
        if kind == radiod.FW_VERSION:
            self.firmware.append(text)
            if not self.assigned:
                self.assigned = True
                out.append(self.say(radiod.enow(radiod.ROLE_ASSIGN, 0, b"\x01TOKN")))
        elif kind == radiod.NODE_TEXT:
            self.texts.append(text)
        elif kind == radiod.ROLE_ACK:
            self.acks.append((counter, text))

    def say(self, payload, seq=None):
        self.seq += 1
        return radiotap(2437, -40) + radiod.espnow_frame(PAGER, self.mac, self.seq if seq is None else seq, payload,
                                                         nonce=b"1234")


class FakeRadio:
    def __init__(self, biscuits, limit=30.0):
        self.mac = PAGER
        self.biscuits, self.limit = biscuits, limit
        self.now, self.freq = 0.0, None
        self.tuned, self.sent, self.queue = [], [], []
        self.node = None
        self.closed = False

    def clock(self):
        return self.now

    def tune(self, freq):
        self.freq = freq
        self.tuned.append(freq)

    def send(self, dst, payload):
        self.sent.append((dst, payload, self.freq))
        if self.freq == 2437:
            for one in self.biscuits:
                one.heard(dst, payload, self.queue)
        return True

    def recv(self, timeout):
        self.now += max(timeout, 0.01)
        if self.node is not None and self.now > self.limit:
            self.node.stop()
        if self.freq == 2437:
            out, self.queue = self.queue, []
            return out + [radiotap(2437, -50) + beacon(NET24, b"two-four")]
        if self.freq == 5975:
            return [radiotap(5975, -60) + beacon(NET6, b"six-gig")]
        return []

    def close(self):
        self.closed = True


def run_node(biscuits, limit=30.0, channels=None):
    radio = FakeRadio(biscuits, limit)
    reports = []
    node = radiod.BiscuitNode(radio.tune, radio, lambda kind, body: reports.append((kind, body)),
                              channels or set(radiod.ALL_6GHZ),
                              clock=radio.clock)
    radio.node = node
    node.run()
    return node, radio, reports


def test_frames_and_messages_round_trip():
    payload = radiod.enow(radiod.NODE_HEARTBEAT, 7, b"hi")
    assert len(payload) == 212 and radiod.read_enow(payload) == (radiod.NODE_HEARTBEAT, 7, b"hi")
    frame = radiod.espnow_frame(BISCUIT, PAGER, 5, payload, nonce=b"abcd")
    assert radiod.read_espnow(frame) == (BISCUIT, PAGER, payload)
    assert radiod.read_espnow(frame[:30]) is None
    with pytest.raises(ValueError):
        radiod.espnow_frame(BISCUIT, PAGER, 1, b"x" * 251)
    records = [(bytes([i]) * 6, radiod.ap6_record(bytes([i]) * 6, 37, -70, 4, b"x" * 32)) for i in range(20)]
    seq, packed = radiod.wardrive_batches(254, records)
    assert all(len(body) <= 250 for body, _ in packed) and sum(len(ids) for _, ids in packed) == 20
    assert seq == (254 + len(packed)) & 0xFF


def test_the_node_joins_the_biscuit_that_answers_with_no_pairing_on_the_node():
    good = FakeBiscuit(b"wdbatch=1 ap6=1 name=Pro", OTHER)
    node, radio, reports = run_node([FakeBiscuit(b"wdbatch=1 encrypt=1"), good], limit=6)
    assert node.core == OTHER and good.firmware[0] == b"Pager:v1.1.0"
    assert radio.sent[0][0] == radiod.BROADCAST and radiod.read_enow(radio.sent[0][1])[0] == radiod.CORE_REQUEST
    final = [body for kind, body in reports if kind == "status"][-1]
    assert final["biscuit"] == "24:EC:4A:00:00:C2" and final["name"] == "Pro"


def test_the_pager_name_is_sent_again_so_the_biscuit_keeps_it():
    fake = FakeBiscuit()
    node, _, _ = run_node([fake], limit=70)
    assert len(fake.firmware) >= 5 and set(fake.firmware) == {b"Pager:v1.1.0"}
    before = len(fake.firmware)
    node.handle(BISCUIT, radiod.ROLE_ASSIGN, 0, b"\x01TOKN")
    node.check_in(node.clock())
    assert len(fake.firmware) == before + 1


def test_a_repeated_frame_is_handled_once():
    fake = FakeBiscuit()
    node, radio, _ = run_node([fake], limit=1)
    acks = len(fake.acks)
    again = fake.say(radiod.enow(radiod.ROLE_ASSIGN, 0, b"\x01TOKN"), seq=900)
    node.frame(again)
    node.frame(again)
    assert len([p for d, p, _ in radio.sent if p[4] == radiod.ROLE_ACK]) == acks + 1


def test_the_node_reports_as_a_pager_then_scans_6ghz_only_for_its_own_biscuit():
    fake = FakeBiscuit()
    node, radio, reports = run_node([fake, FakeBiscuit(mac=OTHER)])
    assert node.core == BISCUIT and node.state == "scanning"
    assert fake.firmware[0] == b"Pager:v1.1.0" and fake.texts == []
    assert {p[4] for _, p, _ in radio.sent} <= {radiod.CORE_REQUEST, radiod.NODE_HEARTBEAT, radiod.FW_VERSION,
                                                radiod.ROLE_ACK, radiod.WARDRIVE_BATCH}
    assert fake.acks[0] == (1, b"TOKN")
    assert set(radio.tuned) - {2437} and all(f == 2437 or 5955 <= f <= 7115 for f in radio.tuned)
    assert all(freq == 2437 and dst in (BISCUIT, radiod.BROADCAST) for dst, _, freq in radio.sent)
    sent = [r[1:7] for r in fake.records]
    assert set(sent) == {NET6} and 1 < len(sent) <= 30 / radiod.RESEND_SECONDS + 1
    record = fake.records[0]
    assert record[0] == radiod.AP6_RECORD and record[7] == 5 and struct.unpack("b", record[8:9])[0] == -60
    assert record[9] == 4 and record[11:] == b"six-gig"
    final = [body for kind, body in reports if kind == "status"][-1]
    assert final["final"] and final["sent"] == 1 and final["heard"] == 1


def test_a_secure_biscuit_and_old_firmware_get_no_scan():
    node, radio, _ = run_node([FakeBiscuit(b"wdbatch=1 encrypt=1")], limit=3)
    assert node.core is None and node.state == "secure"
    old = FakeBiscuit(b"wdbatch=1")
    node, radio, _ = run_node([old], limit=6)
    assert node.state == "old" and set(radio.tuned) == {2437} and old.records == []


def test_6ghz_beacons_are_read_with_their_channel_and_security():
    assert radiod.six_ghz_network(beacon(NET6, b"a"), -60, 5975)[1:4] == (5, -60, 4)
    assert radiod.six_ghz_network(beacon(NET6, b"a", he_channel=37), -60, 5975)[1] == 37
    assert radiod.six_ghz_network(beacon(NET6, b"a", akm=18), -60, 6135)[3] == 0x0D
    assert radiod.six_ghz_network(beacon(NET6, b"a", akm=1), -60, 6135)[3] == 0x06
    assert radiod.six_ghz_network(beacon(NET6, b"a", akm=12), -60, 6135)[3] == 0x07
    assert radiod.six_ghz_network(beacon(NET6, b"a"), -60, 2437) is None
    assert radiod.six_ghz_network(beacon(NET6, b"a")[:30], -60, 5975) is None


def bpf(code, packet):
    a = x = pc = 0
    while True:
        op, jt, jf, k = code[pc]
        pc += 1
        if op == 0x30:
            a = packet[k]
        elif op == 0x50:
            a = packet[x + k]
        elif op == 0x64:
            a = (a << k) & 0xFFFFFFFF
        elif op == 0x0C:
            a = (a + x) & 0xFFFFFFFF
        elif op == 0x07:
            x = a
        elif op == 0x15:
            pc += jt if a == k else jf
        elif op == 0x06:
            return k
        else:
            raise AssertionError("opcode %#x" % op)


def test_the_node_filter_keeps_only_what_the_node_reads():
    assert bpf(radiod.NODE_FILTER, radiotap(5975, -60) + beacon(NET6, b"x"))
    assert bpf(radiod.NODE_FILTER, radiotap(2437, -40) + radiod.espnow_frame(PAGER, BISCUIT, 1, radiod.enow(1)))
    assert bpf(radiod.NODE_FILTER, radiotap(2437, -40) + b"\xd4\x00\x00\x00" + PAGER)
    assert not bpf(radiod.NODE_FILTER, radiotap(2437, -40) + b"\x08\x00" + b"\0" * 30)
    assert not bpf(radiod.NODE_FILTER, radiotap(2437, -40) + b"\x40\x00" + b"\0" * 30)


class QuietSocket:
    def __init__(self, mac):
        self.mac, self.closed, self.sent = mac, False, []

    def send(self, dst, payload):
        self.sent.append(payload)
        return True

    def recv(self, timeout):
        time.sleep(min(timeout, 0.01))
        return []

    def close(self):
        self.closed = True


def test_the_helper_runs_a_node_only_on_its_own_free_monitor_and_stops_it_with_the_monitor(radios):  # noqa: F811
    sent = []
    _, sess = session(radios, sent)
    sockets = []
    sess.system.node_socket = lambda vif: sockets.append(QuietSocket(PAGER)) or sockets[-1]
    assert ask(sess, cmd="biscuit_node", vif="qmmon0", device="cardputer")["ok"] is False
    vif = ask(sess, cmd="monitor_start", iface="wlan2")["vif"]
    for bad in ({"device": "laptop"}, {}, {"device": "pi", "pair": True}, {"device": "pi", "target": "24:EC:4A:00:00:B1"},
                {"device": "pi", "text": "hi"}):
        assert ask(sess, cmd="biscuit_node", vif=vif, **bad)["ok"] is False, bad
    assert ask(sess, cmd="hop_plan", vif=vif, plan=[[2412, 20, 100]])["ok"]
    assert ask(sess, cmd="biscuit_node", vif=vif, device="pi")["error"] == "the monitor is busy"
    ask(sess, cmd="monitor_stop", vif=vif)
    vif = ask(sess, cmd="monitor_start", iface="wlan2")["vif"]
    answer = ask(sess, cmd="biscuit_node", vif=vif, device="pi")
    assert answer == {"ok": True, "channels": 2}
    node = sess.monitors[vif]["node"]
    assert node.channels == {1, 33}
    deadline = time.monotonic() + 3
    while not any(m.startswith(b"B") for m in sent) and time.monotonic() < deadline:
        time.sleep(0.02)
    status = json.loads(next(m for m in sent if m.startswith(b"B"))[1:])
    assert status["kind"] == "status" and status["state"] == "searching"
    assert sockets[-1].sent and all(p[4] == radiod.CORE_REQUEST for p in sockets[-1].sent)
    assert (vif, 2437) in sess.system.hops
    sess.restore()
    assert not node.is_alive() and sockets[-1].closed and vif not in sess.monitors


def test_the_helper_needs_6ghz_and_channel_6_on_the_adapter(radios):  # noqa: F811
    _, sess = session(radios)
    sess.system.node_socket = lambda vif: QuietSocket(PAGER)
    vif = ask(sess, cmd="monitor_start", iface="wlan2")["vif"]
    real = sess.system.phy_info

    def no_six(phy):
        info = real(phy)
        info["freqs"] = {f: v for f, v in info["freqs"].items() if f < 5900}
        return info
    sess.system.phy_info = no_six
    assert "no 6 GHz channels" in ask(sess, cmd="biscuit_node", vif=vif, device="pi")["error"]


def test_the_client_hands_biscuit_messages_to_its_sink():
    ours, theirs = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
    client = radio_helper.Client(connect=lambda path: ours).open()
    got = []
    client.biscuit = got.append
    theirs.send(b"B" + json.dumps({"kind": "status", "state": "scanning"}).encode())
    theirs.send(b"Bnot json")
    theirs.send(b"B[1]")
    deadline = time.monotonic() + 2
    while not got and time.monotonic() < deadline:
        time.sleep(0.01)
    client.close()
    theirs.close()
    assert got == [{"kind": "status", "state": "scanning"}]


ADAPTERS6 = [{"interface": "wlan0", "usb": False, "monitor": False, "driver": "brcmfmac", "freqs": [2412, 2437],
              "connected": True, "default_route": True},
             {"interface": "wlan1", "usb": True, "monitor": True, "driver": "mt7921u", "freqs": [2437, 5180, 5955],
              "connected": False, "default_route": False}]


def test_picking_the_adapter():
    assert biscuit.pick_adapter(ADAPTERS6)["interface"] == "wlan1"
    assert biscuit.pick_adapter(ADAPTERS6[:1]) is None and "Plug in" in biscuit.why_none(ADAPTERS6[:1])
    five = [dict(ADAPTERS6[1], freqs=[2437, 5180])]
    assert biscuit.pick_adapter(five) is None and "6 GHz" in biscuit.why_none(five)
    busy = [dict(ADAPTERS6[1], connected=True)]
    assert biscuit.pick_adapter(busy) is None and "in use" in biscuit.why_none(busy)
    assert radiod.biscuit_name(BISCUIT) == "Biscuit 00B1"
    assert radiod.biscuit_name(BISCUIT, b"wdbatch=1 name=Pro") == "Pro"


class FakeClient:
    def __init__(self, script=()):
        self.closed = threading.Event()
        self.biscuit = None
        self.requests = []
        self.script = list(script)

    def request(self, cmd, timeout=None, **args):
        self.requests.append(dict(args, cmd=cmd))
        if cmd == "adapters":
            return {"adapters": ADAPTERS6}
        if cmd == "monitor_start":
            return {"vif": "qmmon0"}
        if cmd == "biscuit_node":
            def talk():
                for message in self.script:
                    time.sleep(0.02)
                    if self.biscuit and not self.closed.is_set():
                        self.biscuit(message)
            threading.Thread(target=talk, daemon=True).start()
            return {"channels": 59}
        raise radio_helper.HelperError("refused")

    def close(self):
        self.closed.set()


def wait_for(check, seconds=3):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if check():
            return True
        time.sleep(0.01)
    return False


def test_the_link_runs_and_stops_through_the_helper():
    clients = []

    def connect(script):
        def opened():
            clients.append(FakeClient(script))
            return clients[-1], "ready", ""
        return opened
    link = biscuit.BiscuitLink(device="pi", connect=connect([{"kind": "status", "state": "scanning", "heard": 4,
                                                              "sent": 3, "name": "Biscuit Pro"}]))
    assert link.start()
    assert wait_for(lambda: link.state == biscuit.SCANNING)
    assert clients[-1].requests[-1] == {"cmd": "biscuit_node", "vif": "qmmon0", "device": "pi"}
    assert link.status()["heard"] == 4 and link.status()["adapter"] == "wlan1" and link.name == "Biscuit Pro"
    clients[-1].close()
    assert wait_for(lambda: link.state == biscuit.FAILED) and "helper" in link.error
    link.connect = connect([])
    assert link.start()
    assert wait_for(lambda: link.state == biscuit.SEARCHING)
    link.stop(wait=True)
    assert link.state == biscuit.OFF and clients[-1].closed.is_set()


def test_a_missing_helper_is_said_in_the_words_of_the_device(tmp_path):
    link = biscuit.BiscuitLink(connect=lambda: (None, "missing", ""), diy=True)
    assert link.start()
    assert wait_for(lambda: link.state == biscuit.FAILED)
    assert link.error == radio_helper.words("missing", diy=True)


class Link:
    """Node mode in the state a test puts it in."""

    def __init__(self, state=biscuit.OFF, **live):
        self.state, self.live = state, live
        self.name, self.error, self.device, self.diy = "", "", "cardputer", False
        self.started = 0

    @property
    def running(self):
        return self.state not in (biscuit.OFF, biscuit.FAILED)

    def status(self):
        return dict(self.live, state=self.state, error=self.error, adapter="wlan1", seconds=95)

    def start(self):
        self.started += 1
        self.state = biscuit.SEARCHING
        return True

    def stop(self, wait=False):
        self.state = biscuit.OFF


def test_the_biscuit_screen_in_every_state_on_every_screen(device, tmp_path, drawn):
    app = make_app(tmp_path)
    app.biscuit = Link()
    app.open("Settings")
    settings = app.screen
    settings.index = next(i for i, item in enumerate(settings.items) if item[0] == "biscuit_node")
    assert settings.value(app, "biscuit_node") == "Off"
    press(app, "ENTER")
    assert isinstance(app.screen, biscuit_screen.BiscuitNode)
    look(app)
    press(app, "ENTER")
    assert app.biscuit.started == 1 and app.biscuit.state == biscuit.SEARCHING
    look(app)
    assert settings.value(app, "biscuit_node") == "Waiting"
    app.biscuit.state, app.biscuit.name = biscuit.SCANNING, "Biscuit Pro"
    app.biscuit.live = {"heard": 148, "sent": 141, "updates": 233, "queued": 7, "channel": 37}
    look(app)
    assert settings.value(app, "biscuit_node") == "Scanning"
    app.start_wardrive()
    assert not app.wardriving and app.toast_text.startswith("Biscuit node mode is on")
    for state in (biscuit.JOINED, biscuit.SECURE, biscuit.OLD):
        app.biscuit.state = state
        look(app)
    press(app, "ENTER")
    assert app.biscuit.state == biscuit.OFF
    app.biscuit.state, app.biscuit.error = biscuit.FAILED, radio_helper.words("missing", diy=bool(app.square))
    look(app)
    said(device, drawn, [])
    assert any("Pager:v1.1.0" in text for text in drawn) and any("Biscuit Pro" in text for text in drawn)


def test_node_mode_waits_for_a_wardrive_or_recon_to_stop(tmp_path):
    app = make_app(tmp_path)
    app.biscuit = Link()
    app.start_wardrive()
    app.push(biscuit_screen.BiscuitNode())
    press(app, "ENTER")
    assert app.biscuit.started == 0 and app.toast_text == biscuit_screen.BUSY
    app.stop_wardrive()
    assert isinstance(app.screen, (screens.Summary, screens.Home, biscuit_screen.BiscuitNode))
