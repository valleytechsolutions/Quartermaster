"""Quartermaster Nodes: ESP32-C5 boards on USB that scan Wi-Fi and Bluetooth LE for Quartermaster.

The serial line is described in firmware/PROTOCOL.md. A NodeManager finds the nodes plugged in, keeps one NodeLink
open per node, and shares the Wi-Fi channels between the nodes in use while a wardrive or Recon runs. What a node
hears reaches the wardrive in the same shape as a sweep of an adapter, a batch at a time.
"""

from collections import deque
import os
from pathlib import Path
import threading
import time

from . import faults, hardware, planner
from .config import MAX_NODES, NODE_NAME_MAX, node_entries
from .wifi import band_of, security_label

BAUD = 921600
READ_SECONDS = 0.2
PROBE_SECONDS = 1.5
HELLO_SECONDS = 10.0
SILENT_SECONDS = 35.0
SCAN_SECONDS = 3.0
BATCH_SECONDS = 0.5
BATCH_LINES = 64
NUDGE_SECONDS = 3.0
RETRIES = (0.0, 5.0, 30.0)
NODE_RETRY_SECONDS = 10.0
BRIDGE_SECONDS = 15.0
PROTOCOL = "1"
SEQUENCED = ("W", "B", "S", "A", "L", "C", "N", "J")
PLAN_FIELDS = ("args", "version", "country", "bitmap", "time", "ble")

NO_SERIAL = "Nodes need python3-serial. Reinstall Quartermaster."
UNPLUGGED = "Unplugged"
SILENT = "Stopped answering. Plug it in again."
NOT_PLUGGED = "Not plugged in"
UPDATING = "Being updated"
NEWER = "Update Quartermaster to use this node"
OLDER = "Needs a firmware update"
REFUSED = "Did not take its channels"
NO_BLE = "Bluetooth is off on this node: not enough memory"
FULL = "Quartermaster takes up to %d nodes" % MAX_NODES
NOT_A_NODE = "That is not a node"

AUTH = {0: "OPEN", 1: "WEP", 2: "WPA_PSK", 3: "WPA2_PSK", 4: "WPA_WPA2_PSK", 5: "WPA2_ENTERPRISE", 6: "WPA3_PSK",
        7: "WPA2_WPA3_PSK", 8: "WAPI_PSK", 9: "OWE", 10: "WPA3_ENT_192", 11: "WPA3_EXT_PSK",
        12: "WPA3_EXT_PSK_MIXED_MODE", 13: "DPP", 14: "WPA3_ENTERPRISE", 15: "WPA2_WPA3_ENTERPRISE",
        16: "WPA_ENTERPRISE", 17: "UNKNOWN"}
# What the scan of an adapter makes of the same network. A WAPI network shows only its privacy bit there.
_AUTH_PARTS = {
    "OPEN": (), "WEP": ("[WEP]",), "WPA_PSK": ("[WPA-PSK-%s]",), "WPA2_PSK": ("[WPA2-PSK-%s]",),
    "WPA_WPA2_PSK": ("[WPA-PSK-%s]", "[WPA2-PSK-%s]"), "WPA2_ENTERPRISE": ("[WPA2-EAP-%s]",),
    "WPA3_PSK": ("[RSN-SAE-%s]",), "WPA2_WPA3_PSK": ("[WPA2-PSK-%s]", "[RSN-SAE-%s]"), "WAPI_PSK": ("[WEP]",),
    "OWE": ("[RSN-OWE-%s]",), "WPA3_ENT_192": ("[WPA2-EAP-%s]",), "WPA3_EXT_PSK": ("[RSN-SAE-%s]",),
    "WPA3_EXT_PSK_MIXED_MODE": ("[RSN-SAE-%s]",), "DPP": ("[RSN-DPP-%s]",), "WPA3_ENTERPRISE": ("[WPA2-EAP-%s]",),
    "WPA2_WPA3_ENTERPRISE": ("[WPA2-EAP-%s]",), "WPA_ENTERPRISE": ("[WPA-EAP-%s]",), "UNKNOWN": (),
}
CIPHERS = {3: "TKIP", 4: "CCMP", 5: "CCMP+TKIP", 8: "GCMP", 9: "GCMP-256"}
_security = {}


def xor(data):
    """The XOR of every byte, folded in halves: far quicker in Python than a byte at a time."""
    size, value = len(data), int.from_bytes(data, "little")
    while size > 1:
        size = (size + 1) // 2
        value = (value & ((1 << 8 * size) - 1)) ^ (value >> 8 * size)
    return value


def command(name, *args):
    body = ",".join((name,) + args).encode("ascii")
    return b"#%s*%02X\n" % (body, xor(body))


def parse(line):
    """The fields of a node line, None for a line that is not one (boot text, logs), False for a damaged one."""
    if not line.startswith(b"@"):
        return None
    if line.endswith(b"\r"):
        line = line[:-1]
    if len(line) < 6 or line[-3:-2] != b"*":
        return False
    body = line[1:-3]
    try:
        if xor(body) != int(line[-2:], 16):
            return False
        return body.decode("ascii").split(",")
    except (ValueError, UnicodeDecodeError):
        return False


def security(auth, pairwise, wps):
    """(capabilities, short label) of a network as WiGLE and the live screen show it."""
    key = (auth, pairwise, wps)
    found = _security.get(key)
    if found is None:
        cipher = CIPHERS.get(pairwise, "CCMP")
        parts = [part % cipher if "%" in part else part for part in _AUTH_PARTS.get(AUTH.get(auth, "UNKNOWN"), ())]
        caps = "".join(dict.fromkeys(parts + (["[WPS]"] if wps else []) + ["[ESS]"]))
        found = _security[key] = (caps, security_label({"capabilities": caps}))
    return found


def wifi_record(fields):
    """An @W line as a network of an adapter's sweep."""
    if len(fields) != 12:
        return None
    try:
        age, rssi, channel, frequency = int(fields[2]), int(fields[4]), int(fields[5]), int(fields[6])
        auth, pairwise, flags = int(fields[7]), int(fields[8]), int(fields[10], 16)
        raw = bytes.fromhex(fields[11])
    except ValueError:
        return None
    bssid = fields[3].upper()
    if len(bssid) != 17:
        return None
    ssid = raw.decode("utf-8", "replace")[:32] if raw.strip(b"\x00") else ""
    caps, label = security(auth, pairwise, bool(flags & 2))
    return {"bssid": bssid, "ssid": ssid, "rssi": rssi, "channel": channel, "frequency": frequency,
            "band": band_of(frequency), "security": label, "capabilities": caps, "hidden": not ssid,
            "wps": bool(flags & 2), "age_ms": age}


def ble_record(fields):
    """A @B line as a device of a Bluetooth scan."""
    if len(fields) != 9:
        return None
    try:
        age, kind, rssi, flags = int(fields[2]), int(fields[4]), int(fields[5]), int(fields[6], 16)
        maker = int(fields[7], 16) if fields[7] else None
        name = bytes.fromhex(fields[8]).decode("utf-8", "replace")
    except ValueError:
        return None
    address = fields[3].upper()
    if len(address) != 17:
        return None
    return {"address": address, "address_type": 0 if kind == 0 else 1, "rssi": rssi, "name": name, "uuids": [],
            "manufacturer": {} if maker is None else {maker: b""}, "service_data": {}, "tx_power": None,
            "appearance": None, "classic": False, "connectable": bool(flags & 1), "age_ms": age}


def hello_record(fields):
    if len(fields) != 12 or len(fields[2]) != 12:
        return None
    try:
        int(fields[2], 16)
        return {"protocol": fields[1], "id": fields[2].lower(), "firmware": fields[3], "chip": fields[4],
                "flash_mb": int(fields[5]), "psram_mb": int(fields[6]), "board": fields[7],
                "capabilities": tuple(cap for cap in fields[8].split("+") if cap), "country": fields[9],
                "plan": int(fields[10]), "state": fields[11]}
    except ValueError:
        return None


def status_record(fields):
    if len(fields) != 11:
        return None
    try:
        values = [int(value) for value in fields[2:10]]
    except ValueError:
        return None
    names = ("uptime_ms", "sweeps", "sweep_ms", "networks", "devices", "dropped", "plan", "heap")
    record = dict(zip(names, values))
    record["state"] = fields[10]
    return record


def open_port(path, factory):
    """Open a node's port without resetting the board: DTR stays up until RTS is down, so RTS is never up alone."""
    port = factory()
    port.port, port.baudrate, port.timeout, port.write_timeout = path, BAUD, READ_SECONDS, 0.5
    port.exclusive = True
    port.dtr = True
    port.rts = False
    port.open()
    port.dtr = False
    return port


class NodeLink:
    """One node on one serial port. pump() reads what has come, sends what is waiting and keeps the node awake."""

    def __init__(self, path, port, listener=None, clock=time.monotonic):
        self.path, self.port, self.listener, self.clock = path, port, listener, clock
        self.real = os.path.realpath(path)
        self.hello = self.status = None
        self.ready = False
        self.seq = None
        self.lost = self.bad = 0
        self.notes = deque(maxlen=4)
        self.acks = {}
        self.error = ""
        self.closed = False
        self.heard = self.said = clock()
        self._ending = None
        self._buffer = b""
        self._outbox = deque()
        self._wifi, self._ble, self._batch_at = [], [], None
        self._thread = None

    @property
    def node_id(self):
        return self.hello["id"] if self.hello else None

    def send(self, name, *args):
        """Queue a command. The reading thread writes it, so a slow port never holds up the caller."""
        if not self.closed:
            self._outbox.append(command(name, *args))

    def start(self):
        self._thread = threading.Thread(target=self._run, daemon=True, name="node-" + os.path.basename(self.path))
        self._thread.start()

    def _run(self):
        try:
            while self.pump():
                pass
        except Exception:
            faults.note("Node link")
            self._close(SILENT)

    def wait_hello(self, seconds):
        deadline = self.clock() + seconds
        while self.hello is None and self.pump() and self.clock() < deadline:
            pass
        return self.hello is not None

    def stop(self, error=""):
        """Send what is waiting and close the port. With a reading thread, waits up to a second for it to let go."""
        self._ending = error
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=1.0)
        if not self.closed:
            try:
                self._drain()
            except (OSError, ValueError, TypeError):
                pass
            self._close(error)

    def _drain(self):
        while self._outbox:
            self.port.write(self._outbox[0])
            self._outbox.popleft()
            self.said = self.clock()

    def pump(self):
        if self.closed:
            return False
        try:
            self._drain()
            if self._ending is not None:
                self._close(self._ending)
                return False
            data = self.port.read(max(1, self.port.in_waiting))
        except (OSError, ValueError, TypeError):
            self._close(UNPLUGGED)
            return False
        now = self.clock()
        if data:
            self._take(data, now)
        if self._batch_at is not None and now - self._batch_at >= BATCH_SECONDS:
            self._flush()
        if now - self.heard >= SILENT_SECONDS:
            self._close(SILENT)
            return False
        if now - self.said >= HELLO_SECONDS and not self._outbox:
            self.send("HELLO")
        return True

    def _take(self, data, now):
        buffer = self._buffer + data
        if b"\n" not in data:
            self._buffer = buffer if len(buffer) <= 4096 else b""
            return
        lines = buffer.split(b"\n")
        self._buffer = lines.pop()
        for line in lines:
            try:
                self._line(line, now)
            except Exception:
                faults.note("A node line")

    def _line(self, line, now):
        fields = parse(line)
        if fields is None:
            return
        self.heard = now
        if fields is False:
            self.bad += 1
            return
        kind = fields[0]
        if kind == "H":
            hello = hello_record(fields)
            if hello is None:
                self.bad += 1
            else:
                self.hello = hello
                if self.listener is not None:
                    self.listener.heard(self, hello["plan"], hello["state"])
            return
        if kind not in SEQUENCED:
            return
        if len(fields) < 2 or not fields[1].isdigit():
            self.bad += 1
            return
        seq = int(fields[1])
        if self.seq is not None:
            gap = (seq - self.seq - 1) & 0xFFFFFFFF
            if gap < 0x80000000:
                self.lost += gap
        if kind == "A" and len(fields) == 5:
            self.acks[fields[2]] = (fields[3] == "ok", fields[4], now)
            self.ready = True
            if self.listener is not None:
                self.listener.answered(self, fields[2], fields[3] == "ok", fields[4])
        if not self.ready:
            return
        self.seq = seq
        if kind == "W" or kind == "B":
            record = wifi_record(fields) if kind == "W" else ble_record(fields)
            if record is None:
                self.bad += 1
                return
            (self._wifi if kind == "W" else self._ble).append(record)
            if self._batch_at is None:
                self._batch_at = now
            if len(self._wifi) + len(self._ble) >= BATCH_LINES:
                self._flush()
        elif kind == "S":
            status = status_record(fields)
            if status is None:
                self.bad += 1
                return
            self.status = status
            if self.listener is not None:
                self.listener.heard(self, status["plan"], status["state"])
        elif kind == "L" and len(fields) >= 3:
            self.notes.append((now, ",".join(fields[2:])))

    def _flush(self):
        wifi, ble = self._wifi, self._ble
        self._wifi, self._ble, self._batch_at = [], [], None
        if self.listener is not None and (wifi or ble):
            self.listener.sightings(self, wifi, ble)

    def _close(self, error):
        if self.closed:
            return
        self.closed = True
        self.error = error
        self._outbox.clear()
        try:
            self.port.close()
        except (OSError, ValueError, TypeError):
            pass
        if self.listener is not None:
            self.listener.closed(self)


class NodeManager:
    """The nodes Quartermaster knows (in the settings) and the ones plugged in. The app owns one.

    A wardrive or Recon attaches to it while it runs: the nodes in use (in the settings and plugged in) then scan
    their share of the channels, and what they hear goes to the sinks given to attach()."""

    def __init__(self, cfg=None, serial_factory=None, sys_tty=hardware.SYS_TTY, dev=Path("/dev"),
                 by_id=hardware.BY_ID, clock=time.monotonic, threads=True, on_change=None, bridge_wait=BRIDGE_SECONDS):
        cfg = cfg or {}
        self.factory = serial_factory
        self.no_serial = False
        self.bridge_wait = bridge_wait
        self.gps_source = cfg.get("gps_source", "auto")
        self.sys_tty, self.dev, self.by_id = sys_tty, Path(dev), by_id
        self.clock = clock
        self.threads = threads
        self.on_change = on_change
        self.country = cfg.get("country", "US")
        self.configured = node_entries(cfg.get("nodes", []))
        self.error = ""
        self.lock = threading.RLock()
        self.links = {}
        self.twins = {}
        self.ports = {}
        self.tries = {}
        self.away = {}
        self.lent = {}
        self.last = {}
        self.plans = {}
        self.version = 0
        self.owner = None
        self.sinks = None
        self.ble = True
        self.bands = None
        self.given, self.started, self.refused, self.nudged, self.left = {}, set(), {}, {}, {}
        self._key = None
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread = None

    def start(self):
        if self._thread is None:
            self._stop.clear()
            self._thread = threading.Thread(target=self._loop, daemon=True, name="nodes")
            self._thread.start()

    def stop(self):
        """Stop looking for nodes and close every port."""
        self._stop.set()
        self._wake.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None
        with self.lock:
            links = list(self.links.values())
        for link in links:
            link.send("STOP")
            link.stop("")

    def _loop(self):
        while not self._stop.is_set():
            try:
                self.scan()
            except Exception:
                faults.note("Looking for nodes")
            self._wake.wait(SCAN_SECONDS)
            self._wake.clear()

    @property
    def available(self):
        return self._factory() is not None

    def _factory(self):
        if self.factory is None:
            if self.no_serial:
                return None
            try:
                import serial
            except ImportError:
                self.no_serial, self.error = True, NO_SERIAL
                return None
            self.factory = serial.Serial
        return self.factory

    def _present(self):
        """{device path: path to open} of every USB serial port, from two directory listings."""
        links = hardware.by_id_links(self.by_id)
        present = {}
        for name in hardware.usb_serial_names(self.sys_tty):
            real = os.path.realpath(str(self.dev / name))
            present[real] = (name, links.get(real, str(self.dev / name)))
        return present

    def scan(self):
        """One look at the USB serial ports: ask each new one that may be a node whether it is."""
        present = self._present()
        now = self.clock()
        gone = [real for real in self.ports if real not in present]
        hardware.forget_ports(gone)
        for real in gone:
            self.ports.pop(real, None)
            self.tries.pop(real, None)
            self.twins.pop(real, None)
        for real, (name, path) in present.items():
            if real not in self.ports or self.ports[real]["path"] != path:
                self.ports[real] = {"path": path, "usb_id": hardware.usb_id_of(name, self.sys_tty), "seen": now}
        gps = os.path.realpath(self.gps_source) if self.gps_source.startswith("/") else None
        with self.lock:
            for real, port in self.ports.items():
                if port["path"] in self.lent and real not in self.away:
                    self.away[real] = self.lent[port["path"]]
                    hardware.hold_port(real, "node")
            busy = {link.real for link in self.links.values()} | set(self.away) | set(self.twins)
        for real, port in sorted(self.ports.items(), key=lambda item: item[1]["usb_id"] != hardware.NODE_NATIVE):
            native = port["usb_id"] == hardware.NODE_NATIVE
            if self._stop.is_set():
                return
            if real in busy or not (native or port["usb_id"] in hardware.NODE_BRIDGES):
                continue
            if hardware.port_holder(real) is not None or not native and self._gps_port(real, port, gps):
                continue
            count, after = self.tries.get(real, (0, now))
            if now < after or count >= len(RETRIES) and hardware.port_mark(real) != "node":
                continue
            if self._factory() is None:
                return
            self.error = ""
            if not self._probe(real, port["path"]):
                retry = RETRIES[min(count + 1, len(RETRIES) - 1)]
                if hardware.port_mark(real) == "node":
                    retry = NODE_RETRY_SECONDS
                self.tries[real] = (count + 1, self.clock() + retry)

    def _gps_port(self, real, port, gps):
        """A bridge may be the GPS: it is left alone while the GPS layer has not had its look at it."""
        mark = hardware.port_mark(real)
        if mark == "gps" or real == gps:
            return True
        return mark not in ("node", "checked") and self.clock() - port["seen"] < self.bridge_wait

    def _probe(self, real, path):
        if not hardware.hold_port(real, "node"):
            return False
        try:
            port = open_port(path, self.factory)
        except (OSError, ValueError):
            hardware.let_go_port(real, "node")
            return False
        link = NodeLink(path, port, None, self.clock)
        link.send("HELLO")
        if not link.wait_hello(PROBE_SECONDS):
            link.stop("")
            hardware.let_go_port(real, "node")
            return False
        hardware.mark_port(real, "node")
        self.tries.pop(real, None)
        with self.lock:
            other = self.links.get(link.node_id)
            if other is not None and not other.closed:
                self.twins[real] = link.node_id
                link.stop("")
                hardware.let_go_port(real, "node")
                return True
            link.listener = self
            self.links[link.node_id] = link
            self.last.pop(link.node_id, None)
            entry = self._entry(link.node_id)
            if entry is not None and entry.get("led"):
                link.send("LED", *entry["led"].split(","))
            self._replan()
        if self.threads:
            link.start()
        return True

    def pump(self):
        """Read every port once. For tests and for a caller without threads."""
        with self.lock:
            links = list(self.links.values())
        for link in links:
            link.pump()

    def _entry(self, node_id):
        return next((entry for entry in self.configured if entry["id"] == node_id), None)

    def _members(self):
        """The nodes in use: in the settings, plugged in, and speaking this protocol."""
        return sorted(node_id for node_id, link in self.links.items()
                      if self._entry(node_id) is not None and link.hello and link.hello["protocol"] == PROTOCOL)

    def _replan(self):
        """Share the channels again if who is in use, their Bluetooth or the country changed. Call with the lock."""
        nodes = tuple((node_id, tuple(self.links[node_id].hello["capabilities"]),
                       self._entry(node_id)["ble"] and self.ble) for node_id in self._members())
        key = (self.country, nodes, self.bands)
        if key == self._key:
            return
        self._key = key
        plans = planner.make(self.country, nodes, self.plans, self.version + 1, bands=self.bands)
        if any(plan.version == self.version + 1 for plan in plans.values()):
            self.version += 1
        self.plans = plans
        self._push()

    def _push(self, only=None):
        """Give each node in use its plan and start it. Call with the lock."""
        if self.owner is None:
            return
        now = self.clock()
        for node_id, plan in self.plans.items():
            link = self.links.get(node_id)
            if link is None or only not in (None, node_id):
                continue
            if self.given.get(node_id) != plan.version and self.refused.get(node_id) != plan.version:
                link.send("PLAN", *planner.arguments(plan))
                self.given[node_id] = plan.version
            if node_id not in self.started:
                link.send("START")
                self.started.add(node_id)
            self.nudged[node_id] = now

    def heard(self, link, plan_version, state):
        """A node said which plan it scans with and whether it runs: bring it back in line when it is not."""
        with self.lock:
            node_id, now = link.node_id, self.clock()
            if self.links.get(node_id) is not link or now - self.nudged.get(node_id, now - NUDGE_SECONDS) \
                    < NUDGE_SECONDS:
                return
            plan = self.plans.get(node_id) if self.owner is not None else None
            if plan is None:
                if state == "run":
                    link.send("STOP")
                    self.nudged[node_id] = now
                return
            if plan_version != plan.version:
                self.given.pop(node_id, None)
            if state != "run":
                self.started.discard(node_id)
            if node_id not in self.given or node_id not in self.started:
                self._push(node_id)

    def answered(self, link, name, ok, detail):
        """A plan the node refused is not sent again; one garbled on the way is, at the next nudge."""
        if name == "PLAN" and not ok and detail in PLAN_FIELDS:
            with self.lock:
                plan = self.plans.get(link.node_id)
                if plan is not None:
                    self.refused[link.node_id] = plan.version

    def sightings(self, link, wifi, ble):
        with self.lock:
            sinks = self.sinks
            if sinks is None or link.node_id not in self.plans:
                return
            adapter = self._adapter(link.node_id) if wifi else None
        if wifi:
            sinks[0](wifi, adapter)
        if ble and sinks[1] is not None:
            sinks[1](ble)

    def closed(self, link):
        with self.lock:
            node_id = link.node_id
            if node_id is not None and self.links.get(node_id) is link:
                del self.links[node_id]
                self.last[node_id] = {"hello": link.hello, "status": link.status, "error": link.error,
                                      "heard": link.heard, "lost": link.lost, "bad": link.bad}
                self.given.pop(node_id, None)
                self.started.discard(node_id)
                if self.owner is not None and node_id in self.plans:
                    self.left[node_id] = link.error
                for real in [real for real, other in self.twins.items() if other == node_id]:
                    del self.twins[real]
                    self.tries[real] = (0, self.clock())
                self._replan()
            if link.real not in self.away:
                hardware.let_go_port(link.real, "node")
                self.tries[link.real] = (0, self.clock() + 1.0)
        self._wake.set()

    def attach(self, owner, wifi_sink, ble_sink=None, ble=True, bands=None):
        """A wardrive or Recon starts: the nodes in use get their plans and start. Bluetooth only with ble, Wi-Fi
        only on the bands kept (None: all)."""
        with self.lock:
            self.owner, self.sinks, self.ble = owner, (wifi_sink, ble_sink), bool(ble)
            self.bands = None if bands is None else frozenset(bands)
            self.given, self.started, self.refused, self.nudged, self.left = {}, set(), {}, {}, {}
            self._key = None
            self._replan()

    def detach(self, owner):
        with self.lock:
            if self.owner is not owner:
                return
            for node_id in self.started:
                link = self.links.get(node_id)
                if link is not None:
                    link.send("STOP")
            self.owner = self.sinks = self.bands = None
            self.ble = True
            self.started, self.given, self.left = set(), {}, {}
            self._key = None
            self._replan()

    def in_use(self):
        with self.lock:
            return self._members()

    def adapters(self):
        """Each node of the running wardrive or Recon as an adapter, for the screens that list them."""
        with self.lock:
            if self.owner is None:
                return []
            ids = sorted(set(self.plans) | set(self.left))
            return [self._adapter(node_id) for node_id in ids if self._entry(node_id) is not None]

    def _adapter(self, node_id):
        link = self.links.get(node_id)
        status = link.status if link is not None else None
        plan = self.plans.get(node_id)
        bands = sorted({band for band, _ in planner.share_of(plan)}, key=("2.4", "5").index) if plan else []
        label = "Node %s" % self._name(node_id)
        return {"interface": label, "label": label, "method": "node", "node": node_id,
                "sweeps": status["sweeps"] if status else 0,
                "sweep_seconds": status["sweep_ms"] / 1000 if status and status["sweeps"] else None,
                "error": self._error(node_id), "bands": bands}

    def _name(self, node_id):
        entry = self._entry(node_id)
        return entry["name"] if entry is not None and entry["name"] else node_id[-4:].upper()

    def _error(self, node_id):
        link = self.links.get(node_id)
        if link is None:
            if node_id in self.lent.values():
                return UPDATING
            return self.left.get(node_id) or (self.last.get(node_id) or {}).get("error") or NOT_PLUGGED
        if link.hello and link.hello["protocol"] != PROTOCOL:
            protocol = link.hello["protocol"]
            return OLDER if protocol.isdigit() and int(protocol) < int(PROTOCOL) else NEWER
        plan = self.plans.get(node_id)
        if plan is not None and self.refused.get(node_id) == plan.version:
            return REFUSED
        entry = self._entry(node_id)
        if entry is not None and entry["ble"] and self.ble and "ble" not in link.hello["capabilities"]:
            return NO_BLE
        return ""

    def node(self, node_id):
        """Everything the screens show about one node, plugged in or not."""
        with self.lock:
            link, entry = self.links.get(node_id), self._entry(node_id)
            last = self.last.get(node_id) or {}
            hello = link.hello if link is not None else last.get("hello")
            status = link.status if link is not None else last.get("status")
            heard = link.heard if link is not None else last.get("heard")
            plan = self.plans.get(node_id)
            share = planner.share_of(plan) if plan else []
            state = (status or hello)["state"] if link is not None else ""
            info = dict.fromkeys(("board", "firmware", "chip", "flash_mb", "psram_mb"))
            info.update((key, hello[key]) for key in info if hello)
            info.update({
                "id": node_id, "name": self._name(node_id), "configured": entry is not None,
                "connected": link is not None, "port": link.path if link is not None else "", "state": state,
                "capabilities": list(hello["capabilities"]) if hello else [],
                "ble": bool(entry and entry["ble"]), "led": entry.get("led", "") if entry else "",
                "in_use": self.owner is not None and link is not None and node_id in self.plans,
                "plan_version": plan.version if plan else 0, "plan_channels": [ch for _, ch in share],
                "plan_bands": sorted({band for band, _ in share}, key=("2.4", "5").index),
                "networks": status["networks"] if status else 0, "devices": status["devices"] if status else 0,
                "sweeps": status["sweeps"] if status else 0,
                "sweep_seconds": status["sweep_ms"] / 1000 if status and status["sweeps"] else None,
                "last_heard": self.clock() - heard if heard is not None else None,
                "dropped": status["dropped"] if status else 0,
                "lost": link.lost if link is not None else last.get("lost", 0),
                "bad": link.bad if link is not None else last.get("bad", 0),
                "error": self._error(node_id), "note": link.notes[-1][1] if link is not None and link.notes else "",
                "updating": node_id in self.lent.values()})
            return info

    def known(self):
        """The nodes in the settings, in their order."""
        with self.lock:
            ids = [entry["id"] for entry in self.configured]
        return [self.node(node_id) for node_id in ids]

    def found(self):
        """Nodes plugged in that are not in the settings yet."""
        with self.lock:
            ids = sorted(node_id for node_id in self.links if self._entry(node_id) is None)
        return [self.node(node_id) for node_id in ids]

    def _changed(self):
        """Call with the lock, then give what it returns to _save() without it."""
        self._key = None
        self._replan()
        return [dict(entry) for entry in self.configured]

    def _save(self, entries):
        if entries is not None and self.on_change is not None:
            self.on_change(entries)

    def configure(self, cfg):
        """Take the nodes and the country from the settings again."""
        with self.lock:
            self.configured = node_entries(cfg.get("nodes", []))
            self.country = cfg.get("country", self.country)
            self.gps_source = cfg.get("gps_source", self.gps_source)
            self._key = None
            self._replan()

    def add(self, node_id, name=""):
        """Put a node in the settings. Raises ValueError with words for the screen when there is no room."""
        entry = node_entries([{"id": str(node_id), "name": name}])
        if not entry:
            raise ValueError(NOT_A_NODE)
        with self.lock:
            if self._entry(entry[0]["id"]) is not None:
                return
            if len(self.configured) >= MAX_NODES:
                raise ValueError(FULL)
            self.configured.append(entry[0])
            entries = self._changed()
        self._save(entries)

    def remove(self, node_id):
        with self.lock:
            self.configured = [entry for entry in self.configured if entry["id"] != node_id]
            entries = self._changed()
        self._save(entries)

    def rename(self, node_id, name):
        self._set(node_id, "name", " ".join(str(name).split())[:NODE_NAME_MAX])

    def set_ble(self, node_id, on):
        self._set(node_id, "ble", bool(on))

    def set_led(self, node_id, led):
        """led: the #LED arguments, such as "gpio,27,1", "ws2812,27" or "none". The node keeps it."""
        if not node_entries([{"id": node_id, "led": led}])[0].get("led"):
            raise ValueError("That is not an LED this node can use")
        self._set(node_id, "led", led)
        with self.lock:
            link = self.links.get(node_id)
            if link is not None:
                link.send("LED", *led.split(","))

    def _set(self, node_id, key, value):
        entries = None
        with self.lock:
            entry = self._entry(node_id)
            if entry is not None and entry.get(key) != value:
                entry[key] = value
                entries = self._changed()
        self._save(entries)

    def set_country(self, country):
        with self.lock:
            if country != self.country:
                self.country = country
                self._replan()

    def identify(self, node_id, seconds=5):
        """Blink the node's LED. False when it is not plugged in."""
        with self.lock:
            link = self.links.get(node_id)
            if link is None:
                return False
            link.send("IDENT", str(int(seconds)))
            return True

    def release(self, node_id):
        """Let go of a node's port so that it can be flashed. Returns the path to flash, or None.

        Its channels go to the other nodes at once. Waits up to a second for the port to close: call it off the
        screen thread. Nothing opens its ports, nor the port it comes back on, until take_back()."""
        with self.lock:
            link = self.links.get(node_id)
            if link is None:
                return None
            self.lent[link.path] = node_id
            self.away[link.real] = node_id
            for real, other in self.twins.items():
                if other == node_id:
                    self.away[real] = node_id
                    hardware.hold_port(real, "node")
        link.stop(UPDATING)
        return link.path

    def take_back(self, path):
        """The flashing is over: look at the node's ports again at once, whatever is on them now and wherever the
        board came back."""
        real = os.path.realpath(path)
        with self.lock:
            node_id = self.lent.pop(path, None) or self.away.get(real)
            self.lent = {lent: owner for lent, owner in self.lent.items() if owner != node_id}
            for other in [other for other, owner in self.away.items() if owner == node_id or other == real]:
                del self.away[other]
                hardware.let_go_port(other, "node")
                self.tries.pop(other, None)
        self._wake.set()
