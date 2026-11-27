import json
import struct
import threading
import time

import pytest

from quartermaster import biscuit, config, radio_helper
from quartermaster.biscuit import MSG, Node, NodeMode
from tests.test_deepscan import ALFA, AC1200_WIFI5, BUILTIN, Bpf, beacon, he6, mac, radiotap, rsn
from tests.test_radiod import FakeCapture, Radios, ask, radiod, session

BISCUIT = mac("34:85:18:00:00:01")
OWN = mac("00:c0:ca:00:00:02")
OTHER = mac("34:85:18:00:00:09")
REPLY = b"wdbatch=1 nodecfg=1 ap6=1"


class Clock:
    def __init__(self, now=100.0):
        self.now = now

    def __call__(self):
        return self.now


def msg(kind, counter=0, text=b""):
    return biscuit.text_msg(MSG[kind], counter, text)


def kinds(sends):
    return [biscuit.parse_msg(payload)[0] for _, payload, _ in sends]


def joined(clock=None, reply=REPLY, role=True):
    clock = clock or Clock()
    node = Node(clock)
    node.outgoing()
    node.receive(BISCUIT, msg("CORE_REPLY", 0, reply))
    if role:
        node.receive(BISCUIT, msg("ROLE_ASSIGN", 0, bytes((1,)) + b"\x0a\x0b\x0c\x0d"))
    return node, clock


def net6(bssid="AC:84:C6:00:06:01", channel=37, rssi=-60.0, ssid="Harbor6", caps="[RSN-SAE-CCMP][ESS]"):
    return {"bssid": bssid, "band": "6", "channel": channel, "rssi": rssi, "ssid": ssid, "hidden": not ssid,
            "capabilities": caps, "privacy": True}


def records(payload):
    """(bssid, channel, rssi, code, ssid) of each AP6 record of a batch."""
    assert payload[:4] == b"ENOW" and payload[4] == MSG["WARDRIVE_BATCH"] and payload[5] == 2
    count, at, out = payload[7], 8, []
    for _ in range(count):
        assert payload[at] == biscuit.REC_AP6
        bssid = payload[at + 1:at + 7]
        channel, rssi, code, size = struct.unpack_from("<BbBB", payload, at + 7)
        out.append((bssid, channel, rssi, code, payload[at + 11:at + 11 + size]))
        at += 11 + size
    assert at == len(payload)
    return out


def test_messages_have_the_biscuits_framing_and_odd_ones_are_ignored():
    payload = msg("HEARTBEAT", 7, b"hi")
    assert payload[:4] == b"ENOW" and payload[4] == 3 and len(payload) == 4 + 1 + 4 + 2 + 201
    assert biscuit.parse_msg(payload) == (3, 7, b"hi")
    assert biscuit.parse_msg(msg("TEXT", 0, b"x" * 300))[2] == b"x" * 200
    for junk in (b"", b"ENOW", b"ENOW\x02\x00", b"XNOW" + payload[4:], b"ENOW\x02" + b"\xff" * 6):
        assert biscuit.parse_msg(junk) is None or biscuit.parse_msg(junk)[2] == b""
    node = Node(Clock())
    for junk in (b"", b"ENOW", b"\x00" * 300, msg("CORE_REPLY", 0, b"nothing useful")):
        node.receive(BISCUIT, junk)
    assert node.core is None and node.state == biscuit.SEARCHING


def test_a_node_asks_for_a_biscuit_until_one_answers_then_says_who_it_is():
    clock = Clock()
    node = Node(clock, fw="Quartermaster:v9")
    first = node.outgoing()
    assert [(dst, biscuit.parse_msg(p)) for dst, p, _ in first] == [(biscuit.BROADCAST, (1, 0, b"bsc=1"))]
    assert node.outgoing() == []
    clock.now += biscuit.REQUEST_EVERY
    assert kinds(node.outgoing()) == [MSG["CORE_REQUEST"]]
    node.receive(BISCUIT, msg("CORE_REPLY", 0, REPLY))
    assert node.core == BISCUIT and node.ap6 and node.state == biscuit.JOINED
    sends = node.outgoing()
    assert all(dst == BISCUIT for dst, _, _ in sends)
    assert kinds(sends)[:2] == [MSG["FW_VERSION"], MSG["HEARTBEAT"]]
    assert biscuit.parse_msg(sends[0][1])[2] == b"Quartermaster:v9"
    node.receive(OTHER, msg("CORE_REPLY", 0, REPLY))
    assert node.core == BISCUIT


def test_a_secure_biscuit_is_not_joined_and_says_why():
    node = Node(Clock())
    node.receive(BISCUIT, msg("CORE_REPLY", 0, b"wdbatch=1 encrypt=1 ap6=1"))
    assert node.core is None and node.state == biscuit.SECURE
    assert "Extended" in node.status()["words"]


def test_a_role_is_acknowledged_with_its_token_and_wifi_starts_the_sweeps():
    node, _ = joined(role=False)
    node.outgoing()
    assert not node.scanning and node.plan(0) == biscuit.home_plan()
    node.receive(BISCUIT, msg("ROLE_ASSIGN", 0, b"\x01\xaa\xbb\xcc\xdd"))
    assert node.scanning and node.state == biscuit.SCANNING
    ack = node.outgoing()[0]
    assert biscuit.parse_msg(ack[1]) == (MSG["ROLE_ACK"], 1, b"\xaa\xbb\xcc\xdd")
    node.receive(BISCUIT, msg("ROLE_ASSIGN", 2))
    assert not node.scanning and biscuit.parse_msg(node.outgoing()[0][1])[:2] == (MSG["ROLE_ACK"], 2)


def test_stop_release_config_and_ota_from_the_biscuit():
    node, _ = joined()
    node.outgoing()
    node.receive(BISCUIT, msg("CONFIG_UPDATE"))
    node.receive(BISCUIT, msg("OTA_COMMAND", 0, b"http://example.invalid/fw.bin"))
    assert kinds(node.outgoing()) == [MSG["CONFIG_ACK"]]
    node.offer([net6()])
    node.receive(BISCUIT, msg("STOP_SCAN"))
    assert not node.scanning and kinds(node.outgoing()) == [MSG["STOP_ACK"]]
    node.receive(BISCUIT, msg("RELEASE"))
    assert node.core is None and node.state == biscuit.SEARCHING


def test_6_ghz_networks_go_in_batches_strongest_sighting_once_per_resend_window():
    node, clock = joined()
    node.outgoing()
    node.offer([net6(rssi=-70), net6(rssi=-55), net6("AC:84:C6:00:06:02", 101, -80, "", "[RSN-OWE-CCMP][ESS]"),
                dict(net6("AC:84:C6:00:05:01"), band="5", channel=36), net6("AC:84:C6:00:06:03", channel=36),
                dict(net6("bad"), bssid="zz")])
    sends = node.outgoing()
    batches = [(p, body) for _, p, body in sends if body is not None]
    assert len(batches) == 1
    found = records(batches[0][0])
    assert found == [(mac("AC:84:C6:00:06:01"), 37, -55, biscuit.WPA3_PSK, b"Harbor6"),
                     (mac("AC:84:C6:00:06:02"), 101, -80, biscuit.OWE, b"")]
    node.sent_batch(batches[0][1])
    assert node.status()["sent_unique"] == 2 and node.status()["seen"] == 2
    node.offer([net6(rssi=-40)])
    assert not [b for _, _, b in node.outgoing() if b]
    clock.now += biscuit.RESEND
    node.offer([net6(rssi=-40)])
    assert records([p for _, p, b in node.outgoing() if b][0])[0][2] == -40


def test_batches_stay_under_the_esp_now_size_and_long_names_are_cut():
    node, _ = joined()
    node.outgoing()
    node.offer([net6("AC:84:C6:00:%02X:%02X" % (i // 256, i % 256), 5 + 16 * (i % 15), -60, "N" * 40)
                for i in range(40)])
    payloads = []
    for _ in range(10):
        payloads += [p for _, p, b in node.outgoing() if b]
    assert all(len(p) <= 250 for p in payloads)
    assert sum(len(records(p)) for p in payloads) == 40
    assert all(len(r[4]) == 32 for p in payloads for r in records(p))


def test_old_biscuit_firmware_holds_6_ghz_and_says_to_update():
    node, _ = joined(reply=b"wdbatch=1 nodecfg=1")
    node.outgoing()
    node.offer([net6()])
    assert not [b for _, _, b in node.outgoing() if b]
    assert node.status()["words"] == biscuit.OLD_FIRMWARE and node.status()["held"] == 1


def test_a_silent_biscuit_is_given_up_and_looked_for_again():
    node, clock = joined()
    node.outgoing()
    clock.now += biscuit.CONTACT_TIMEOUT - 1
    node.contact()
    clock.now += biscuit.CONTACT_TIMEOUT - 1
    assert node.core == BISCUIT and node.outgoing()
    clock.now += 2
    assert kinds(node.outgoing()) == [MSG["CORE_REQUEST"]] and node.core is None


def test_retries_of_one_message_count_once():
    node, _ = joined(role=False)
    node.outgoing()
    for _ in range(3):
        node.receive(BISCUIT, msg("CONFIG_UPDATE"), seq=42)
    assert kinds(node.outgoing()).count(MSG["CONFIG_ACK"]) == 1


def test_security_codes():
    code = biscuit.auth_code
    assert code({"capabilities": "[ESS]"}) == biscuit.OPEN
    assert code({"capabilities": "[RSN-OWE-CCMP][ESS]"}) == biscuit.OWE
    assert code({"capabilities": "[RSN-SAE-CCMP][ESS]"}) == biscuit.WPA3_PSK
    assert code({"capabilities": "[WPA2-PSK-CCMP][RSN-SAE-CCMP][ESS]"}) == biscuit.WPA2_WPA3_PSK
    assert code({"capabilities": "[WPA2-EAP-CCMP][ESS]"}) == biscuit.WPA2_EAP
    assert code({"capabilities": "[WPA2-EAP-GCMP-256][ESS]"}) == biscuit.WPA3_EAP
    assert code({"capabilities": "[WPA-PSK-TKIP][WPA2-PSK-CCMP][ESS]"}) == biscuit.WPA_WPA2_PSK
    assert code({"capabilities": "[WEP][ESS]"}) == biscuit.WEP
    assert code({"capabilities": "", "privacy": True}) == biscuit.UNKNOWN


def test_the_sweeps_cover_every_6_ghz_channel_and_come_back_to_channel_6_about_once_a_second():
    heard = set()
    for turn in range(biscuit.REST_SHARE):
        steps = biscuit.scan_plan(turn)
        assert len(steps) <= 64 and all(60 <= d <= 1000 and w == 20 for _, w, d in steps)
        assert steps[0][0] == biscuit.HOME_FREQ
        gap = 0.0
        for freq, _, dwell in steps:
            gap = 0.0 if freq == biscuit.HOME_FREQ else gap + dwell / 1000.0
            assert gap <= 0.9
        heard |= {f for f, _, _ in steps if f != biscuit.HOME_FREQ}
        assert {biscuit.freq6(c) for c in biscuit.PSC6} <= heard
    assert heard == {biscuit.freq6(c) for c in biscuit.ALL6}
    assert biscuit.scan_plan(0, allowed={2437, 5975}) == [[2437, 20, 150], [5975, 20, 110]]
    assert biscuit.scan_plan(0, allowed={2412, 5975}) == [] and biscuit.scan_plan(0, allowed={2437}) == []


def esp_now(src, dst, payload, seq=5):
    body = b"\x7f" + biscuit.OUI + b"\x01\x02\x03\x04" + b"\xdd" + bytes([len(payload) + 5]) + biscuit.OUI + \
        b"\x04\x01" + payload
    return struct.pack("<HH6s6s6sH", 0x00D0, 0, dst, src, b"\xff" * 6, seq << 4) + body


def test_esp_now_frames_for_the_node_are_read_and_others_left():
    payload = msg("CONFIG_UPDATE")
    assert biscuit.espnow_payload(esp_now(BISCUIT, OWN, payload), OWN) == (BISCUIT, payload, 5)
    assert biscuit.espnow_payload(esp_now(BISCUIT, biscuit.BROADCAST, payload), OWN)[0] == BISCUIT
    assert biscuit.espnow_payload(esp_now(BISCUIT, OTHER, payload), OWN) is None
    assert biscuit.espnow_payload(esp_now(OWN, OWN, payload), OWN) is None
    frame = esp_now(BISCUIT, OWN, payload)
    for cut in range(0, len(frame)):
        biscuit.espnow_payload(frame[:cut], OWN)
    assert biscuit.espnow_payload(frame[:30], OWN) is None


def test_the_node_adapter_is_a_free_wifi_6e_radio_with_monitor_mode():
    assert biscuit.pick([BUILTIN, ALFA], [])[0] is ALFA
    assert biscuit.pick([BUILTIN, AC1200_WIFI5], [])[0] is None
    assert "on a network" in biscuit.pick([ALFA], [{"interface": "wlan1", "connected": True}])[1]
    assert "country" in biscuit.pick([ALFA], [{"interface": "wlan1", "freqs": [2412, 2437, 5180]}])[1]
    assert biscuit.pick([ALFA], [{"interface": "wlan1", "freqs": [2437, 5975, 6215]}])[0] is ALFA
    assert "monitor" in biscuit.pick([dict(ALFA, monitor=False)], [])[1]


def test_the_setting_is_off_by_default_and_needs_the_helper():
    assert config.DEFAULTS["biscuit_node"] is False
    assert "biscuit_node" in config.HELPER_SETTINGS
    assert not radio_helper.wanted({"biscuit_node": True}) and radio_helper.needed({"biscuit_node": True})


class FakeClient:
    def __init__(self, helper=None):
        self.calls = []
        self.frames = None
        self.closed = threading.Event()
        self.helper = helper if helper is not None else [{"interface": "wlan1", "freqs": [2437, 5975, 6055]}]
        self.full = False

    def request(self, cmd, timeout=None, **args):
        self.calls.append((cmd, args))
        if cmd == "adapters":
            return {"adapters": self.helper}
        if cmd == "monitor_start":
            return {"vif": "qmmon0"}
        if cmd == "node_send" and self.full:
            raise radio_helper.HelperError("node queue full")
        return {"ok": True}

    def close(self):
        self.closed.set()


def node_mode(client=None, clock=None):
    client = client or FakeClient()
    mode = NodeMode({"biscuit_node": True}, connect=lambda: (client, "ready", ""), clock=clock or Clock())
    return mode, client


def relayed(*frames, freq=2437):
    body = struct.pack("<IIId", 1, 0, freq, 1.0)
    for frame in frames:
        body += struct.pack("<H", len(frame)) + frame
    return body


def test_node_mode_takes_the_alfa_out_of_normal_scans_and_listens_on_channel_6():
    mode, client = node_mode()
    alfa = dict(ALFA, mac="00:C0:CA:00:00:02")
    assert mode.open([BUILTIN, alfa])
    assert mode.start(lambda nets, adapter: None)
    try:
        assert [c for c, _ in client.calls][:4] == ["adapters", "monitor_start", "hop_plan", "capture"]
        assert client.calls[2][1]["plan"] == biscuit.home_plan()
        assert client.calls[3][1] == {"vif": "qmmon0", "mgmt_only": True, "node": True}
        monitor_vif = {"interface": "qmmon0", "type": "803", "bands": ["6"]}
        assert mode.follow([BUILTIN, monitor_vif], [BUILTIN, monitor_vif]) == [BUILTIN]
    finally:
        mode.stop()
    assert ("monitor_stop", {"vif": "qmmon0"}) in client.calls and client.closed.is_set()


def test_node_mode_answers_the_biscuit_and_hands_6_ghz_networks_to_the_log_and_the_biscuit():
    clock = Clock()
    logged = []
    mode, client = node_mode(clock=clock)
    mode.open([dict(ALFA, mac="00:C0:CA:00:00:02")])
    mode.sink = lambda nets, adapter: logged.extend(nets)
    mode.vif, mode.own, mode.allowed = "qmmon0", OWN, None
    mode._batch(relayed(radiotap(2437) + esp_now(BISCUIT, OWN, msg("CORE_REPLY", 0, REPLY)),
                        radiotap(2437) + esp_now(BISCUIT, OWN, msg("ROLE_ASSIGN", 0, b"\x01abcd"), seq=6)))
    assert mode.node.scanning
    six = beacon(bssid="AC:84:C6:00:06:01", ssid=b"Harbor6", channel=0, extra=rsn(akms=(8,)) + he6(37))
    mode._batch(relayed(radiotap(freq=6135, rssi=-58) + six, freq=6135))
    clock.now += 2
    mode._flush()
    assert [n["bssid"] for n in logged] == ["AC:84:C6:00:06:01"] and logged[0]["channel"] == 37
    mode.client.calls.clear()
    with mode._lock:
        sends = mode.node.outgoing()
    for dst, payload, body in sends:
        assert mode._send(dst, payload)
    sent = [args for cmd, args in client.calls if cmd == "node_send"]
    assert all(args["vif"] == "qmmon0" and args["dst"] == "34:85:18:00:00:01" for args in sent)
    batch = [bytes.fromhex(args["payload"]) for args in sent if bytes.fromhex(args["payload"])[4] == 19]
    assert records(batch[0])[0][:2] == (mac("AC:84:C6:00:06:01"), 37)


def test_an_ack_to_the_node_keeps_the_biscuit_and_one_to_others_does_not():
    clock = Clock()
    mode, _ = node_mode(clock=clock)
    mode.own = OWN
    mode.node.receive(BISCUIT, msg("CORE_REPLY", 0, REPLY))
    ack = bytes((0xd4, 0, 0, 0)) + OWN
    clock.now += biscuit.CONTACT_TIMEOUT - 1
    mode._batch(relayed(radiotap(2437) + ack, radiotap(2437) + bytes((0xd4, 0, 0, 0)) + OTHER))
    clock.now += biscuit.CONTACT_TIMEOUT - 1
    assert mode.node.outgoing() and mode.node.core == BISCUIT


def test_a_full_helper_queue_keeps_the_batch_for_the_next_turn():
    mode, client = node_mode()
    mode.vif = "qmmon0"
    client.full = True
    assert not mode._send(BISCUIT, msg("HEARTBEAT"))
    assert mode.error == ""
    mode.node, _ = joined()
    mode.node.outgoing()
    mode.node.offer([net6()])
    sends = mode.node.outgoing()
    body = [(p, b) for _, p, b in sends if b][0]
    mode.node.unsent(body[1], body[0])
    assert mode.node.status()["queued"] == 1


def test_no_usable_adapter_leaves_every_adapter_scanning():
    mode, client = node_mode(FakeClient(helper=[]))
    assert not mode.open([BUILTIN, AC1200_WIFI5])
    assert "Wi-Fi 6E" in mode.unavailable
    assert mode.follow([BUILTIN, AC1200_WIFI5]) == [BUILTIN, AC1200_WIFI5]
    assert mode.radio_status() == []


# The radio helper's side.

@pytest.fixture
def helper_radios(tmp_path):
    return Radios(tmp_path)


def node_session(radios):
    helper, sess = session(radios)
    sent = []

    class Sender:
        def send(self, frame):
            sent.append(frame)
            return len(frame)

        def close(self):
            pass
    sess.system.sender = lambda vif: Sender()
    vif = ask(sess, cmd="monitor_start", iface="wlan2")["vif"]
    return helper, sess, vif, sent


def test_the_helper_sends_only_node_messages_and_only_with_channel_6_in_the_plan(helper_radios):
    _, sess, vif, sent = node_session(helper_radios)
    good = msg("HEARTBEAT").hex()
    assert ask(sess, cmd="node_send", vif=vif, dst="34:85:18:00:00:01", payload=good)["error"] == \
        "the hop plan has no channel 6"
    ask(sess, cmd="hop_plan", vif=vif, plan=[[5955, 20, 110]])
    assert ask(sess, cmd="node_send", vif=vif, dst="34:85:18:00:00:01", payload=good)["ok"] is False
    assert ask(sess, cmd="hop_plan", vif=vif, plan=[[2437, 20, 150], [5955, 20, 110]])["ok"]
    for dst, payload in (("34:85:18:00:00:01", "zz"), ("34:85:18:00:00:01", "00" * 300),
                         ("34:85:18:00:00:01", (b"XNOW" + msg("HEARTBEAT")[4:]).hex()),
                         ("34:85:18:00:00:01", msg("CORE_REPLY").hex()), ("34:85:18:00:00:01", msg("TEXT").hex()),
                         ("ff:ff:ff:ff:ff:ff", good), ("35:85:18:00:00:01", good), ("34:85:18:00:00", good),
                         ("34:85:18:00:00:01", good.upper()), (7, good), ("34:85:18:00:00:01", None)):
        assert ask(sess, cmd="node_send", vif=vif, dst=dst, payload=payload)["ok"] is False, (dst, payload)
    assert ask(sess, cmd="node_send", vif=vif, dst="34:85:18:00:00:01", payload=good) == {"ok": True, "queued": 1}
    assert ask(sess, cmd="node_send", vif=vif, dst="ff:ff:ff:ff:ff:ff", payload=msg("CORE_REQUEST").hex())["ok"]
    other = sess.helper.open_session(lambda data: None)
    assert ask(other, cmd="node_send", vif=vif, dst="34:85:18:00:00:01", payload=good)["ok"] is False
    sess.monitors[vif]["hopper"].node = None
    answers = [ask(sess, cmd="node_send", vif=vif, dst="34:85:18:00:00:01", payload=good)
               for _ in range(radiod.NODE_QUEUE + 40)]
    assert answers[-1].get("error") == "node queue full"
    sess.restore()


def test_node_messages_go_on_air_only_while_on_channel_6_from_the_adapters_own_address(helper_radios):
    _, sess, vif, sent = node_session(helper_radios)
    ask(sess, cmd="hop_plan", vif=vif, plan=[[2437, 20, 150], [5955, 20, 110]])
    for _ in range(3):
        ask(sess, cmd="node_send", vif=vif, dst="34:85:18:00:00:01", payload=msg("HEARTBEAT").hex())
    node = sess.monitors[vif]["node"]
    deadline = time.monotonic() + 3
    while len(sent) < 3 and time.monotonic() < deadline:
        time.sleep(0.02)
    sess.restore()
    assert len(sent) == 3 and node.sent == 3
    for frame in sent:
        assert frame[:12] == radiod.NODE_RADIOTAP
        dot11 = frame[12:]
        assert dot11[:2] == b"\xd0\x00" and dot11[4:10] == mac("34:85:18:00:00:01")
        assert dot11[10:16] == mac("00:c0:ca:00:00:02") and dot11[16:22] == b"\xff" * 6
        found = biscuit.espnow_payload(dot11, mac("34:85:18:00:00:01"))
        assert found is not None and found[1] == msg("HEARTBEAT")


def test_the_node_drain_stops_before_the_channel_changes_and_keeps_the_rate():
    class Sender:
        def __init__(self):
            self.frames = []

        def send(self, frame):
            self.frames.append(frame)

        def close(self):
            pass
    clock = Clock(0.0)
    sender = Sender()
    queue = radiod.NodeQueue(sender, OWN, clock)
    for _ in range(radiod.NODE_QUEUE):
        queue.put(BISCUIT, msg("HEARTBEAT"))

    class Stopped:
        def is_set(self):
            return False

        def wait(self, seconds):
            clock.now += seconds
            return False
    queue.ready.wait = lambda seconds: Stopped().wait(seconds)
    queue.drain(0.15, Stopped())
    assert 0 < len(sender.frames) < radiod.NODE_QUEUE and clock.now <= 0.15
    clock.now = 10.0
    queue.drain(11.5, Stopped())
    assert len(sender.frames) == radiod.NODE_QUEUE


def test_node_capture_also_keeps_acks_to_the_node_and_nothing_else_new():
    plain, node = Bpf(radiod.bpf_program()), Bpf(radiod.bpf_program(ack_to=OWN))
    rt = radiotap()
    x = len(rt)
    to_me, to_other = rt + bytes((0xd4, 0, 0, 0)) + OWN, rt + bytes((0xd4, 0, 0, 0)) + OTHER
    assert plain.run(to_me) == 0 and node.run(to_me) == x + 24
    assert node.run(to_other) == 0
    assert node.run(rt + bytes((0xc4, 0, 0, 0)) + OWN) == 0
    assert node.run(rt + bytes((0xd4, 0, 0, 0)) + OWN[:4]) == 0
    for frame in (beacon(), esp_now(BISCUIT, OWN, msg("ROLE_ASSIGN"))):
        assert node.run(rt + frame) == plain.run(rt + frame) == x + 2048
    with pytest.raises(ValueError):
        radiod.bpf_program(ack_to=b"\x00" * 5)


def test_node_capture_asks_for_the_adapters_own_acks(helper_radios):
    _, sess, vif, _ = node_session(helper_radios)
    seen = {}
    sess.system.capture = lambda vif, mgmt_only, ack_to=None: seen.update(ack_to=ack_to) or FakeCapture([], mgmt_only)
    assert ask(sess, cmd="capture", vif=vif, mgmt_only=True, node=True)["ok"]
    assert seen["ack_to"] == mac("00:c0:ca:00:00:02")
    assert ask(sess, cmd="capture", vif=vif, node="yes")["ok"] is False
    sess.restore()


def test_the_settings_help_says_what_node_mode_does():
    help_text = {key: words for key, _, _, words in config.SETTINGS}
    assert "6 GHz node" in help_text["biscuit_node"] and "Extended" in help_text["biscuit_node"]
    assert json.dumps(config.DEFAULTS["biscuit_node"]) == "false"
