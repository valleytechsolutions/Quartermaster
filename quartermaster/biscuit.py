"""Biscuit node mode: Quartermaster joins a BiscuitShop Biscuit as its 6 GHz node.

The Biscuit (an ESP32-C5 wardriver) scans 2.4 and 5 GHz itself and takes nodes over ESP-NOW. In this mode one USB
Wi-Fi 6E adapter goes into monitor mode through the radio helper. It listens on the 6 GHz channels, comes back to
2.4 GHz channel 6 about once a second, and there sends the 6 GHz networks it heard to the Biscuit, which logs them
with its own GPS. Quartermaster logs them too, with the Cardputer's GPS.

6 GHz is only ever listened to. The only frames that are sent are the Biscuit's own short ESP-NOW messages, on
2.4 GHz channel 6, and the radio helper refuses anything else.

The reference is CodeHedge's Biscuit Pager Node payload (by GSHD and Hedge) for the Hak5 WiFi Pineapple Pager:
https://github.com/hak5/wifipineapplepager-payloads/pull/396. Every Quartermaster version works the way that payload
does and comes up on the Biscuit as a Pager, with the payload's firmware name, so a Biscuit owner sees one kind of
6 GHz node, not two. Its timings are the payload's too: 110 ms per 6 GHz channel, back on channel 6 for 150 ms after
about 0.9 s of listening, the 15 main 6 GHz channels on three sweeps and all 59 on every fourth, each network sent
again after 5 s at the soonest, and the Biscuit given up 20 s after its last radio ACK. The code is written separately
for Quartermaster.
"""

import queue
import struct
import threading
import time

from . import faults, monitor, radio_helper

HOME_FREQ = 2437
OUI = b"\x18\xfe\x34"
MAGIC = b"ENOW"
BROADCAST = b"\xff" * 6
TEXT_MAX = 200
MSG = {"CORE_REQUEST": 1, "CORE_REPLY": 2, "HEARTBEAT": 3, "TEXT": 4, "ROLE_ASSIGN": 5, "OTA_COMMAND": 6,
       "FW_VERSION": 7, "STOP_SCAN": 8, "STOP_ACK": 9, "CONFIG_UPDATE": 10, "CONFIG_ACK": 11, "CONFIG_REQUEST": 12,
       "ROLE_ACK": 15, "ENCRYPT_CONFIG": 16, "IDENTIFY": 17, "WARDRIVE_BATCH": 19, "RELEASE": 26}
ROLE_WIFI = 1
REC_AP6 = 0x04
BATCH_VERSION = 2
BATCH_BODY = 242
# The name CodeHedge's payload gives itself, so the Biscuit shows Quartermaster as a Pager node.
FW = "Pager:v1.1.0"

PSC6 = tuple(range(5, 234, 16))
ALL6 = tuple(range(1, 234, 4))
FULL_EVERY = 4
DWELL6, HOME_DWELL, SEARCH_DWELL = 110, 150, 1000
HOME_EVERY = 8
HALF = 32

REQUEST_EVERY, HEARTBEAT_EVERY, FW_EVERY, FW_TRIES = 0.3, 3.0, 2.0, 3
ACK_TIMEOUT = 20.0
RESEND = 5.0
DUPLICATE_WINDOW = 2.0
MAX_PENDING = 1024
MAX_OUT = 64
SEND_EACH_TURN = 12
TICK = 0.1
FORWARD_QUEUE = 64
DRIVERS = frozenset({"mt7921u", "mt7925u"})

SEARCHING, JOINED, SCANNING, SECURE = "searching", "joined", "scanning", "secure"
WORDS = {
    SEARCHING: "Looking for a Biscuit",
    JOINED: "Joined. Start a wardrive on the Biscuit.",
    SCANNING: "Sending 6 GHz to the Biscuit",
    SECURE: "Biscuit is in Secure mode: set it to Extended",
}
OLD_FIRMWARE = "Biscuit firmware has no 6 GHz nodes: update it"

# The Biscuit's security codes for a 6 GHz network.
OPEN, WEP, WPA_PSK, WPA2_PSK, WPA3_PSK, WPA_WPA2_PSK, WPA2_EAP, WPA3_EAP = 0, 1, 2, 3, 4, 5, 6, 7
WPA2_WPA3_PSK, UNKNOWN, OWE = 9, 0x0C, 0x0D


def text_msg(kind, counter=0, text=b""):
    text = bytes(text)[:TEXT_MAX]
    return struct.pack("<4sBIH", MAGIC, kind, counter & 0xFFFFFFFF, len(text)) + text.ljust(TEXT_MAX + 1, b"\0")


def parse_msg(payload):
    """(type, counter, text) of a message, or None. A cut-short or odd message is ignored, never raised on."""
    payload = bytes(payload)
    if len(payload) < 11 or payload[:4] != MAGIC:
        return None
    counter, length = struct.unpack_from("<IH", payload, 5)
    return payload[4], counter, payload[11:11 + min(length, TEXT_MAX)]


def auth_code(net):
    caps = (net.get("capabilities") or "").upper()
    sae, psk, eap = "SAE" in caps, "PSK" in caps, "EAP" in caps
    if "OWE" in caps:
        return OWE
    if eap:
        return WPA3_EAP if "SUITE" in caps or "GCMP-256" in caps else WPA2_EAP
    if sae and psk:
        return WPA2_WPA3_PSK
    if sae:
        return WPA3_PSK
    if "WPA2-PSK" in caps and "WPA-PSK" in caps:
        return WPA_WPA2_PSK
    if "WPA2-PSK" in caps:
        return WPA2_PSK
    if "WPA-PSK" in caps:
        return WPA_PSK
    if "WEP" in caps:
        return WEP
    if net.get("privacy"):
        return UNKNOWN
    return OPEN


def mac_bytes(text):
    try:
        raw = bytes.fromhex(str(text).replace(":", "").replace("-", ""))
    except ValueError:
        return None
    return raw if len(raw) == 6 else None


def mac_text(raw):
    return ":".join("%02x" % b for b in raw)


def valid6(channel):
    return isinstance(channel, int) and 1 <= channel <= 233 and (channel - 1) % 4 == 0


def ap6_record(bssid, channel, rssi, code, ssid):
    ssid = bytes(ssid)[:32]
    return bytes([REC_AP6]) + bytes(bssid) + struct.pack("<BbBB", channel, max(-128, min(127, int(rssi))), code,
                                                         len(ssid)) + ssid


def batch_msg(seq, records):
    return MAGIC + bytes([MSG["WARDRIVE_BATCH"], BATCH_VERSION, seq & 0xFF, len(records)]) + b"".join(records)


def espnow_payload(frame, own):
    """(source, payload) of an ESP-NOW frame for us or for everyone, from the 802.11 frame after radiotap; or None."""
    frame = bytes(frame)
    if len(frame) < 24 + 15 or frame[0] != 0xD0:
        return None
    dst, src = frame[4:10], frame[10:16]
    if src == own or dst not in (own, BROADCAST):
        return None
    body = frame[24:]
    if body[0] != 0x7F or body[1:4] != OUI or body[8] != 0xDD or body[10:13] != OUI or body[13] != 0x04:
        return None
    size = body[9]
    if size < 5 or 10 + size > len(body):
        return None
    return src, body[15:10 + size], struct.unpack_from("<H", frame, 22)[0] >> 4


def freq6(channel):
    return 5950 + 5 * channel


def home_plan(allowed=None):
    if allowed is not None and HOME_FREQ not in allowed:
        return []
    return [[HOME_FREQ, 20, SEARCH_DWELL]]


def sweep_channels(turn):
    """The 6 GHz channels of one plan. As in the Pager payload: the 15 main channels on three sweeps and all 59 on
    the fourth. The helper takes 64 steps a plan at most, so the full sweep goes in two plans one after the other."""
    step = turn % (FULL_EVERY + 1)
    if step < FULL_EVERY - 1:
        return list(PSC6)
    return list(ALL6[:HALF] if step == FULL_EVERY - 1 else ALL6[HALF:])


def scan_plan(turn, allowed=None):
    """One plan of the sweeps, back on channel 6 after every 8 channels (about 0.9 s of listening)."""
    freqs = [freq6(c) for c in sweep_channels(turn) if allowed is None or freq6(c) in allowed]
    if not freqs or allowed is not None and HOME_FREQ not in allowed:
        return []
    steps = []
    for i, freq in enumerate(freqs):
        if i % HOME_EVERY == 0:
            steps.append([HOME_FREQ, 20, HOME_DWELL])
        steps.append([freq, 20, DWELL6])
    return steps


def cycle_seconds(steps):
    return sum(step[2] for step in steps) / 1000.0 + 0.004 * len(steps)


class Node:
    """The node's side of the Biscuit's protocol, with no radio: messages in, messages out."""

    def __init__(self, clock=time.monotonic, fw=FW):
        self.clock, self.fw = clock, fw.encode()
        self.core = None
        self.state = SEARCHING
        self.ap6 = False
        self.role, self.token = 0, b"\0\0\0\0"
        self.hb = self.seq = self.fw_tries = 0
        self.replies = []
        self.out = []
        self.pending = {}
        self.last_sent = {}
        self.seen6 = set()
        self.sent = 0
        self.sent_unique = set()
        self.held = 0
        self.dropped = 0
        self.last_contact = None
        self.last_ack = None
        self.joined_at = None
        self.due = {"request": 0.0, "fw": 0.0, "hb": 0.0}
        self._recent = {}

    @property
    def scanning(self):
        return self.core is not None and self.role == ROLE_WIFI

    def contact(self, now=None):
        self.last_contact = self.clock() if now is None else now

    def ack(self, now=None):
        """A radio ACK to the node while it has a Biscuit. As in the Pager payload, the Biscuit is given up only
        20 s after the last ACK that was heard; a Biscuit that was never heard to ACK is kept."""
        if self.core is not None:
            self.last_ack = self.clock() if now is None else now

    def receive(self, src, payload, seq=None, now=None):
        now = self.clock() if now is None else now
        src = bytes(src)
        if seq is not None:
            key = (src, seq)
            if now - self._recent.get(key, -1e9) < DUPLICATE_WINDOW:
                return
            self._recent[key] = now
            if len(self._recent) > 256:
                self._recent = {k: v for k, v in self._recent.items() if now - v < DUPLICATE_WINDOW}
        msg = parse_msg(payload)
        if msg is None:
            return
        kind, counter, text = msg
        if kind == MSG["OTA_COMMAND"]:
            return
        if kind == MSG["CORE_REPLY"] and self.core is None:
            if b"wdbatch=" not in text and b"nodecfg=" not in text:
                return
            if b"encrypt=1" in text:
                self.state = SECURE
                return
            self.core, self.ap6 = src, b"ap6=1" in text
            self.state, self.role = JOINED, 0
            self.fw_tries = 0
            self.joined_at = now
            self.last_ack = None
            self.contact(now)
            self.due.update(fw=now, hb=now)
            return
        if self.core is None or src != self.core:
            return
        self.contact(now)
        if kind == MSG["ROLE_ASSIGN"]:
            if len(text) == 5:
                self.role, self.token = text[0], text[1:5]
                token = self.token
            else:
                self.role, token = (text[0] if text else counter & 0xFF), b""
            self.state = SCANNING if self.role == ROLE_WIFI else JOINED
            self.replies.append(text_msg(MSG["ROLE_ACK"], self.role, token))
        elif kind == MSG["CONFIG_UPDATE"]:
            self.replies.append(text_msg(MSG["CONFIG_ACK"]))
        elif kind == MSG["STOP_SCAN"]:
            self.role, self.state = 0, JOINED
            self.out.clear()
            self.replies.append(text_msg(MSG["STOP_ACK"]))
        elif kind == MSG["RELEASE"]:
            self._search()

    def _search(self):
        self.core, self.role, self.state = None, 0, SEARCHING
        self.out.clear()
        self.replies.clear()

    def offer(self, networks, now=None):
        """Networks heard: the 6 GHz ones wait for the next send, strongest sighting of each."""
        now = self.clock() if now is None else now
        for net in networks:
            if net.get("band") != "6" or not valid6(net.get("channel")):
                continue
            bssid = mac_bytes(net.get("bssid"))
            rssi = net.get("rssi")
            if bssid is None or not isinstance(rssi, (int, float)):
                continue
            self.seen6.add(bssid)
            if now - self.last_sent.get(bssid, -1e9) < RESEND:
                continue
            held = self.pending.get(bssid)
            if held is None and len(self.pending) >= MAX_PENDING:
                self.dropped += 1
                continue
            if held is None or rssi > held[2]:
                ssid = (net.get("ssid") or "").encode("utf-8", "replace")[:32] if not net.get("hidden") else b""
                self.pending[bssid] = (net["channel"], ssid, rssi, auth_code(net))

    def _batches(self, now):
        if not self.scanning:
            return
        if not self.ap6:
            self.held = len(self.seen6)
            self.pending.clear()
            return
        body, size = [], 0
        for bssid, (channel, ssid, rssi, code) in list(self.pending.items()):
            record = ap6_record(bssid, channel, rssi, code, ssid)
            if size + len(record) > BATCH_BODY:
                self.out.append((body, batch_msg(self.seq, [r for _, r in body])))
                self.seq += 1
                body, size = [], 0
            body.append((bssid, record))
            size += len(record)
            self.last_sent[bssid] = now
        if body:
            self.out.append((body, batch_msg(self.seq, [r for _, r in body])))
            self.seq += 1
        self.pending.clear()
        if len(self.out) > MAX_OUT:
            self.dropped += sum(len(b) for b, _ in self.out[:-MAX_OUT])
            del self.out[:-MAX_OUT]
        if len(self.last_sent) > 4 * MAX_PENDING:
            self.last_sent = {k: v for k, v in self.last_sent.items() if now - v < RESEND}

    def outgoing(self, now=None):
        """[(destination, payload, batch)] to send now: replies, then the regular messages, then the networks."""
        now = self.clock() if now is None else now
        if self.core is not None and self.last_ack is not None and now - self.last_ack > ACK_TIMEOUT:
            self._search()
            self.last_ack = None
        if self.core is None:
            if now >= self.due["request"]:
                self.due["request"] = now + REQUEST_EVERY
                return [(BROADCAST, text_msg(MSG["CORE_REQUEST"], 0, b"bsc=1"), None)]
            return []
        sends = [(self.core, reply, None) for reply in self.replies]
        self.replies = []
        if self.fw_tries < FW_TRIES and now >= self.due["fw"]:
            self.fw_tries += 1
            self.due["fw"] = now + FW_EVERY
            sends.append((self.core, text_msg(MSG["FW_VERSION"], 0, self.fw), None))
        if now >= self.due["hb"]:
            self.due["hb"] = now + HEARTBEAT_EVERY
            sends.append((self.core, text_msg(MSG["HEARTBEAT"], self.hb), None))
            self.hb += 1
        self._batches(now)
        while self.out and len(sends) < SEND_EACH_TURN:
            body, payload = self.out.pop(0)
            sends.append((self.core, payload, body))
        return sends

    def sent_batch(self, body):
        self.sent += len(body)
        if len(self.sent_unique) < 50000:
            self.sent_unique.update(bssid for bssid, _ in body)

    def unsent(self, body, payload):
        """A batch the helper could not take goes back to the front."""
        self.out.insert(0, (body, payload))

    def plan(self, turn, allowed=None):
        return scan_plan(turn, allowed) if self.scanning else home_plan(allowed)

    def status(self):
        words = WORDS[self.state]
        if self.scanning and not self.ap6:
            words = OLD_FIRMWARE
        return {"state": self.state, "words": words, "biscuit": mac_text(self.core) if self.core else "",
                "ap6": self.ap6, "seen": len(self.seen6), "sent": self.sent, "sent_unique": len(self.sent_unique),
                "queued": sum(len(b) for b, _ in self.out) + len(self.pending), "held": self.held,
                "dropped": self.dropped}


def pick(adapters, helper=()):
    """The USB adapter that becomes the node: a Wi-Fi 6E radio with monitor mode, on no network. (adapter, why)."""
    live = {a.get("interface"): a for a in helper or ()}
    why = "Needs a USB Wi-Fi 6E adapter (MT7921AU or MT7925U)"
    found = []
    for a in adapters:
        state = live.get(a.get("interface"), {})
        if not a.get("external") or a.get("driver") not in DRIVERS or "6" not in (a.get("bands") or ()):
            continue
        if not a.get("monitor") or state.get("monitor") is False:
            why = "%s has no monitor mode" % a.get("label", a.get("interface"))
            continue
        if state.get("connected") or state.get("default_route"):
            why = "%s is on a network: disconnect it first" % a.get("label", a.get("interface"))
            continue
        freqs = state.get("freqs")
        if freqs is not None and (HOME_FREQ not in freqs or not any(freq6(c) in freqs for c in PSC6)):
            why = "No 6 GHz channels: set the Wi-Fi country"
            continue
        found.append(a)
    if not found:
        return None, why
    return found[0], ""


class NodeMode:
    """The radio helper session of Biscuit node mode during one wardrive. Everything is put back at stop, and the
    helper puts it back by itself if Quartermaster stops without saying so."""

    def __init__(self, cfg, connect=radio_helper.connect, clock=time.monotonic, fw=FW):
        self.cfg = cfg
        self._connect = connect
        self.clock = clock
        self.client = None
        self.state, self.detail = "off", ""
        self.adapter = None
        self.vif = None
        self.allowed = None
        self.unavailable = ""
        self.error = ""
        self.node = Node(clock, fw)
        self.own = b""
        self.sink = None
        self.turn = 0
        self.plan_kind = None
        self.plan_at = 0.0
        self.plan_due = 0.0
        self.frames = 0
        self.forward_dropped = 0
        self.refused = 0
        self._worker = monitor.Worker("biscuit", clock=clock)
        self._forward = queue.Queue(maxsize=FORWARD_QUEUE)
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._threads = []

    def open(self, adapters):
        """Connect to the helper and pick the adapter. False when there is none; `unavailable` says why."""
        self.client, self.state, self.detail = self._connect()
        if self.client is None:
            self.unavailable = radio_helper.words(self.state, self.detail)
            return False
        try:
            helper = self.client.request("adapters")["adapters"]
        except (radio_helper.HelperError, KeyError, TypeError) as exc:
            self.unavailable = "Radio helper failed: %s" % str(exc)[:60]
            return False
        self.adapter, self.unavailable = pick(adapters, helper)
        if self.adapter is None:
            return False
        freqs = next((h.get("freqs") for h in helper if h.get("interface") == self.adapter["interface"]), None)
        self.allowed = set(freqs) if freqs else None
        return True

    def start(self, sink):
        """Monitor mode on the node adapter, the first plan and the capture. False when that failed: the adapter is
        then left to scan normally."""
        self.sink = sink
        if self.client is None or self.adapter is None:
            return False
        try:
            self.vif = self.client.request("monitor_start", iface=self.adapter["interface"])["vif"]
            self.own = mac_bytes(self.adapter.get("mac") or "") or b""
            self._send_plan(force=True)
            self.client.frames = self._receive
            self.client.request("capture", vif=self.vif, mgmt_only=True, node=True)
        except (radio_helper.HelperError, OSError, KeyError, TypeError) as exc:
            self.error = "Biscuit node failed: %s" % str(exc)[:60]
            self._stop_monitor()
            return False
        self._threads = [threading.Thread(target=self._read, daemon=True, name="biscuit-frames"),
                         threading.Thread(target=self._talk, daemon=True, name="biscuit-node")]
        for thread in self._threads:
            thread.start()
        return True

    @property
    def running(self):
        return self.vif is not None

    def follow(self, adapters, present=None):
        """The adapters that still scan normally: never the node adapter or a monitor interface. When the node adapter
        went away, the node stops and says so."""
        names = {a["interface"] for a in (adapters if present is None else present)}
        if self.vif is not None and self.vif not in names and present is not None:
            self.error = "The node adapter went away. Biscuit node stopped."
            self._stop_monitor()
        keep = []
        for a in adapters:
            if a.get("type") == "803" or a["interface"] == self.vif:
                continue
            if self.vif is not None and self.adapter is not None and a["interface"] == self.adapter["interface"]:
                continue
            keep.append(a)
        return keep

    def _receive(self, body):
        try:
            self._forward.put_nowait(bytes(body))
        except queue.Full:
            self.forward_dropped += 1

    def _read(self):
        while not self._stop.is_set():
            try:
                body = self._forward.get(timeout=0.5)
            except queue.Empty:
                self._flush()
                continue
            try:
                self._batch(body)
                self._flush()
            except Exception:
                faults.note("Biscuit node frames")

    def _batch(self, body):
        batch = radio_helper.split_batch(body)
        if batch is None:
            return
        _, _, freq, _, frames = batch
        now = self.clock()
        for raw in frames:
            self.frames += 1
            head = monitor.radiotap(raw)
            if head is None:
                continue
            length, end, _, heard = head
            frame = raw[length:end]
            if len(frame) >= 10 and frame[0] == 0xD4:
                if self.own and bytes(frame[4:10]) == self.own:
                    with self._lock:
                        self.node.ack(now)
                continue
            if len(frame) >= 39 and frame[0] == 0xD0:
                found = espnow_payload(frame, self.own)
                if found is not None:
                    with self._lock:
                        self.node.receive(found[0], found[1], found[2], now)
                continue
            self._worker.feed(raw, now, heard or freq)

    def _flush(self):
        networks = self._worker.tick()
        if not networks:
            return
        with self._lock:
            self.node.offer(networks)
        sink = self.sink
        if sink is not None:
            try:
                sink(networks, dict(self.adapter or {}, interface=self.vif or "", role="biscuit"))
            except Exception:
                faults.note("Biscuit node networks")

    def _talk(self):
        while not self._stop.wait(TICK):
            client = self.client
            if self.vif is None or client is None:
                return
            closed = getattr(client, "closed", None)
            if isinstance(closed, threading.Event) and closed.is_set():
                if not self._stop.is_set():
                    self.error = "The radio helper stopped. Biscuit node stopped."
                self.vif = None
                return
            try:
                self._send_plan()
                with self._lock:
                    sends = self.node.outgoing()
                for dst, payload, body in sends:
                    if self._stop.is_set():
                        return
                    if not self._send(dst, payload):
                        if body is not None:
                            with self._lock:
                                self.node.unsent(body, payload)
                        break
                    if body is not None:
                        with self._lock:
                            self.node.sent_batch(body)
            except radio_helper.HelperError as exc:
                if not self._stop.is_set():
                    self.error = "Biscuit node: %s" % str(exc)[:60]
            except Exception:
                if not self._stop.is_set():
                    faults.note("Biscuit node")

    def _send(self, dst, payload):
        client, vif = self.client, self.vif
        if client is None or vif is None:
            return False
        try:
            client.request("node_send", vif=vif, dst=mac_text(dst), payload=payload.hex(), timeout=5)
        except radio_helper.HelperError as exc:
            self.refused += 1
            if "full" not in str(exc):
                self.error = "Biscuit node: %s" % str(exc)[:60]
            return False
        return True

    def _send_plan(self, force=False):
        """Searching: stay on channel 6. Scanning: one sweep after another, each with another share of the channels."""
        now = self.clock()
        kind = "scan" if self.node.scanning else "home"
        if not force and kind == self.plan_kind and (kind == "home" or now < self.plan_due):
            return
        if kind == "scan" and self.plan_kind == "scan":
            self.turn += 1
        steps = self.node.plan(self.turn, self.allowed)
        if not steps:
            raise radio_helper.HelperError("no 6 GHz channels: set the Wi-Fi country")
        client, vif = self.client, self.vif
        if client is None or vif is None:
            return
        client.request("hop_plan", vif=vif, plan=steps)
        self.plan_kind, self.plan_at = kind, now
        self.plan_due = now + max(1.0, cycle_seconds(steps) - 0.3)

    def _stop_monitor(self):
        vif, self.vif = self.vif, None
        if self.client is not None:
            self.client.frames = None
            if vif is not None:
                try:
                    self.client.request("monitor_stop", vif=vif)
                except radio_helper.HelperError:
                    pass

    def stop(self):
        self._stop.set()
        self._stop_monitor()
        for thread in self._threads:
            if thread is not threading.current_thread():
                thread.join(timeout=3)
        if self.client is not None:
            self.client.close()
            self.client = None

    def status(self):
        with self._lock:
            found = self.node.status()
        found.update(running=self.running, vif=self.vif or "", unavailable=self.unavailable, error=self.error,
                     adapter=(self.adapter or {}).get("label", "") if self.adapter else "",
                     original=(self.adapter or {}).get("interface", "") if self.adapter else "",
                     frames=self.frames, turn=self.turn)
        return found

    def radio_status(self):
        if not self.running:
            return []
        found = self.status()
        return [{"interface": self.vif, "method": "biscuit", "sweeps": self.turn, "sweep_seconds": None,
                 "error": self.error, "label": found["adapter"], "bands": ["6"], "original": found["original"],
                 "biscuit": found}]
