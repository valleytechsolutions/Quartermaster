"""Quartermaster radio helper: the one privileged step Deep scan, direct scans and radio tuning need.

systemd starts it on the first connection to /run/quartermaster/radio.sock, as a dynamic user holding only
CAP_NET_ADMIN and CAP_NET_RAW. It takes a fixed set of JSON commands from members of the quartermaster-radio group
and checks every argument. It has no command for the regulatory domain, never touches a radio that is not on USB,
never sends a frame, and runs no shell. Everything a connection changes is put back when that connection closes, and
what a crash left behind is put back at the next start.
"""

import array
import grp
import json
import os
import pwd
import re
import select
import signal
import socket
import struct
import subprocess
import sys
import threading
import time

VERSION = 1
SOCKET_PATH = "/run/quartermaster/radio.sock"
GROUP = "quartermaster-radio"
SYS_NET = "/sys/class/net"
SYS_PHY = "/sys/class/ieee80211"
PROC_ROUTE = "/proc/net/route"
MAX_MESSAGE = 4096
MAX_CLIENTS = 2
MAX_MONITORS = 2
MAX_SCANS = 4
MAX_ID = 2 ** 31 - 1
MAX_PLAN = 64
MIN_DWELL, MAX_DWELL, IW_DWELL = 60, 1000, 250
IDLE_EXIT = 60.0
SCAN_TIMEOUT = 20
CHUNK = 60 * 1024
BATCH_BYTES, BATCH_SECONDS = 64 * 1024, 0.02
RCVBUF = 4 * 1024 * 1024
DRIVERS = frozenset({"mt7921u", "mt7925u", "mt76x2u", "mt76x0u", "rtw88_8822bu", "rtw88_8821cu", "ath9k_htc"})
COMMANDS = {"hello": (), "adapters": (), "scan": ("iface", "freqs"), "tune": ("iface",), "untune": ("iface",),
            "monitor_start": ("iface",), "hop_plan": ("vif", "plan"), "capture": ("vif", "mgmt_only"),
            "monitor_stop": ("vif",)}
REQUIRED = {"scan": ("iface",), "tune": ("iface",), "untune": ("iface",), "monitor_start": ("iface",),
            "hop_plan": ("vif", "plan"), "capture": ("vif",), "monitor_stop": ("vif",)}
TOOLS = {"iw": ("/usr/sbin/iw", "/sbin/iw", "/usr/bin/iw"), "ip": ("/usr/sbin/ip", "/sbin/ip", "/usr/bin/ip", "/bin/ip")}
_NAME = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,14}")
_VIF = re.compile(r"qmmon[0-9]")
_MAC = re.compile(r"[0-9a-f]{2}(?::[0-9a-f]{2}){5}")

FLOCK_OUIS = (
    0x70c94e, 0x3c9180, 0xd8f3bc, 0x803049, 0xb83532, 0x145afc, 0x744ca1, 0x083a88, 0x9c2f9d, 0xc03532,
    0x940853, 0xe4aaea, 0xf46add, 0xe00af6, 0x24b2b9, 0x00f48d, 0xd03957, 0xe8d0fc, 0xe04f43, 0xb81ea4,
    0x700894, 0x588e81, 0xec1bbd, 0x5800e3, 0x9035ea, 0x5c93a2, 0x646e69, 0x14b5cd, 0xb41e52, 0x00037f,
    0x826bf2)

NETLINK_GENERIC, GENL_ID_CTRL = 16, 0x10
CTRL_CMD_GETFAMILY, CTRL_ATTR_FAMILY_ID, CTRL_ATTR_FAMILY_NAME = 3, 1, 2
NL80211_CMD_SET_WIPHY = 2
NL80211_ATTR_IFINDEX, NL80211_ATTR_WIPHY_FREQ = 3, 38
NL80211_ATTR_CHANNEL_WIDTH, NL80211_ATTR_CENTER_FREQ1 = 159, 160
NL80211_CHAN_WIDTH_20_NOHT = 0
NLM_F_REQUEST, NLM_F_ACK, NLMSG_ERROR = 1, 4, 2
SO_ATTACH_FILTER, SO_LOCK_FILTER, SO_RCVBUFFORCE = 26, 44, 33
SOL_PACKET, PACKET_STATISTICS, ETH_P_ALL = 263, 6, 3


class Refused(Exception):
    pass


def bpf_program(ouis=FLOCK_OUIS, mgmt_only=False):
    """The fixed classic BPF the capture socket gets, as (code, jt, jf, k) rows.

    X holds the radiotap length. Kept: beacons, probe requests and responses, deauth, disassoc and action frames at
    up to 2048 bytes; other management frames and data frames only when a Flock prefix sends them or, for data,
    is sent to, at header length. Everything else, control frames and protocol versions other than 0 are dropped
    in the kernel."""
    rows, labels = [], {}

    def op(code, k=0, jt=None, jf=None):
        rows.append([code, jt, jf, k])

    def label(name):
        labels[name] = len(rows)

    def addr_check(offset, match):
        op(0x40, offset)
        op(0x74, 8)
        for oui in ouis:
            op(0x15, oui, match, None)

    op(0x30, 3)
    op(0x64, 8)
    op(0x07)
    op(0x30, 2)
    op(0x4c)
    op(0x07)
    op(0x50, 0)
    op(0x45, 0x03, "drop", None)
    op(0x02, 0)
    op(0x54, 0x0c)
    op(0x15, 0x00, None, "notmgmt")
    op(0x60, 0)
    op(0x54, 0xf0)
    for subtype in (0x80, 0x50, 0x40, 0xc0, 0xa0, 0xd0):
        op(0x15, subtype, "full", None)
    addr_check(10, "head24")
    addr_check(16, "head24")
    op(0x06, 0)
    label("notmgmt")
    op(0x15, 0x08, None, "drop")
    if mgmt_only:
        op(0x06, 0)
    else:
        addr_check(10, "head32")
        op(0x50, 4)
        op(0x45, 0x01, "drop", None)
        addr_check(4, "head32")
    label("drop")
    op(0x06, 0)
    label("full")
    op(0x87)
    op(0x04, 2048)
    op(0x16)
    label("head24")
    op(0x87)
    op(0x04, 24)
    op(0x16)
    label("head32")
    op(0x87)
    op(0x04, 32)
    op(0x16)
    program = []
    for at, (code, jt, jf, k) in enumerate(rows):
        jumps = []
        for target in (jt, jf):
            if target is None:
                jumps.append(0)
            else:
                distance = labels[target] - at - 1
                if not 0 <= distance <= 255:
                    raise ValueError("jump too far")
                jumps.append(distance)
        program.append((code, jumps[0], jumps[1], k))
    return program


def _pack_program(program):
    return b"".join(struct.pack("HBBI", *row) for row in program)


def _tool(name):
    for path in TOOLS[name]:
        if os.path.exists(path):
            return path
    return TOOLS[name][0]


def _read(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            return handle.read().strip()
    except OSError:
        return ""


def parse_phy(text):
    """{"monitor": bool, "freqs": {MHz: {"dbm": float or None, "no_ir": bool}}} of the enabled channels."""
    freqs, modes, section = {}, set(), None
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("Supported interface modes:"):
            section = "modes"
            continue
        if section == "modes":
            if stripped.startswith("* "):
                modes.add(stripped[2:].strip())
                continue
            section = None
        match = re.match(r"\* (\d+)(?:\.\d+)? MHz \[\d+\](.*)", stripped)
        if match:
            rest = match.group(2)
            if "disabled" in rest:
                continue
            power = re.search(r"\(([0-9.]+) dBm\)", rest)
            freqs[int(match.group(1))] = {"dbm": float(power.group(1)) if power else None,
                                          "no_ir": "no IR" in rest or "passive scanning" in rest}
    return {"monitor": "monitor" in modes, "freqs": freqs}


def parse_dev(text):
    info = {"type": "", "freq": None, "txpower": None, "ifindex": None, "addr": ""}
    for line in text.splitlines():
        words = line.split()
        if not words:
            continue
        if words[0] == "type" and len(words) > 1:
            info["type"] = words[1]
        elif words[0] == "ifindex" and len(words) > 1 and words[1].isdigit():
            info["ifindex"] = int(words[1])
        elif words[0] == "addr" and len(words) > 1:
            info["addr"] = words[1].lower()
        elif words[0] == "channel":
            match = re.search(r"\((\d+) MHz\)", line)
            if match:
                info["freq"] = int(match.group(1))
        elif words[0] == "txpower" and len(words) > 1:
            try:
                info["txpower"] = float(words[1])
            except ValueError:
                pass
    return info


class Nl80211:
    """Channel hops over nl80211, from one socket: no process per hop."""

    def __init__(self):
        self.sock = socket.socket(socket.AF_NETLINK, socket.SOCK_RAW, NETLINK_GENERIC)
        self.sock.bind((0, 0))
        self.sock.settimeout(1.0)
        self.seq = 0
        self.family = self._family()

    @staticmethod
    def _attr(kind, payload):
        size = 4 + len(payload)
        return struct.pack("HH", size, kind) + payload + b"\0" * ((4 - size % 4) % 4)

    def _send(self, kind, cmd, attrs):
        self.seq += 1
        body = struct.pack("BBH", cmd, 1, 0) + b"".join(attrs)
        self.sock.send(struct.pack("IHHII", 16 + len(body), kind, NLM_F_REQUEST | NLM_F_ACK, self.seq, 0) + body)
        return self.seq

    def _family(self):
        seq = self._send(GENL_ID_CTRL, CTRL_CMD_GETFAMILY, [self._attr(CTRL_ATTR_FAMILY_NAME, b"nl80211\0")])
        while True:
            data = self.sock.recv(65536)
            at = 0
            while at + 16 <= len(data):
                length, kind, _, got, _ = struct.unpack_from("IHHII", data, at)
                if got == seq and kind == NLMSG_ERROR:
                    code = struct.unpack_from("i", data, at + 16)[0]
                    if code:
                        raise OSError(-code, "nl80211 not found")
                elif got == seq:
                    pos = at + 20
                    while pos + 4 <= at + length:
                        size, attr = struct.unpack_from("HH", data, pos)
                        if attr == CTRL_ATTR_FAMILY_ID:
                            return struct.unpack_from("H", data, pos + 4)[0]
                        pos += (size + 3) & ~3
                at += (length + 3) & ~3

    def set_freq(self, ifindex, freq):
        u32 = lambda value: struct.pack("I", value)
        seq = self._send(self.family, NL80211_CMD_SET_WIPHY, [
            self._attr(NL80211_ATTR_IFINDEX, u32(ifindex)), self._attr(NL80211_ATTR_WIPHY_FREQ, u32(freq)),
            self._attr(NL80211_ATTR_CHANNEL_WIDTH, u32(NL80211_CHAN_WIDTH_20_NOHT)),
            self._attr(NL80211_ATTR_CENTER_FREQ1, u32(freq))])
        while True:
            data = self.sock.recv(65536)
            _, kind, _, got, _ = struct.unpack_from("IHHII", data, 0)
            if got == seq and kind == NLMSG_ERROR:
                code = struct.unpack_from("i", data, 16)[0]
                if code:
                    raise OSError(-code, os.strerror(-code))
                return

    def close(self):
        self.sock.close()


class Capture:
    """A receive-only packet socket on one monitor interface. It has no way to send."""

    def __init__(self, vif, program):
        sock = socket.socket(socket.AF_PACKET, socket.SOCK_RAW, 0)
        try:
            code = array.array("B", _pack_program(program))
            fprog = struct.pack("HP", len(program), code.buffer_info()[0])
            sock.setsockopt(socket.SOL_SOCKET, SO_ATTACH_FILTER, fprog)
            sock.setsockopt(socket.SOL_SOCKET, SO_LOCK_FILTER, 1)
            try:
                sock.setsockopt(socket.SOL_SOCKET, SO_RCVBUFFORCE, RCVBUF)
            except OSError:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, RCVBUF)
            sock.bind((vif, socket.htons(ETH_P_ALL)))
        except BaseException:
            sock.close()
            raise
        self._sock = sock

    def fileno(self):
        return self._sock.fileno()

    def recv_into(self, buffer):
        return self._sock.recv_into(buffer)

    def stats(self):
        try:
            received, dropped = struct.unpack("II", self._sock.getsockopt(SOL_PACKET, PACKET_STATISTICS, 8))
        except OSError:
            return 0, 0
        return received, dropped

    def close(self):
        self._sock.close()


class System:
    """What the helper reads and runs. Every command is a fixed tool with a list of checked words: never a shell."""

    def __init__(self, sys_net=SYS_NET, route=PROC_ROUTE, runner=subprocess.run, sys_phy=SYS_PHY):
        self.sys_net, self.route, self.runner, self.sys_phy = sys_net, route, runner, sys_phy
        self._nl = None
        self._nl_lock = threading.Lock()

    def run(self, tool, *words, timeout=10):
        args = [_tool(tool)] + [str(word) for word in words]
        for word in args[1:]:
            if not word or word.startswith("-") or any(c in word for c in "\0\n;|&$`<>\\'\" "):
                raise Refused("bad argument")
        result = self.runner(args, capture_output=True, text=True, errors="replace", timeout=timeout, check=False)
        return result.returncode, result.stdout or "", (result.stderr or "").strip()

    def wireless(self):
        try:
            return sorted(n for n in os.listdir(self.sys_net) if os.path.exists(os.path.join(self.sys_net, n, "phy80211")))
        except OSError:
            return []

    def phy(self, iface):
        return _read(os.path.join(self.sys_net, iface, "phy80211", "name"))

    def driver(self, iface):
        try:
            return os.path.basename(os.path.realpath(os.path.join(self.sys_net, iface, "device", "driver")))
        except OSError:
            return ""

    def usb(self, iface):
        node = os.path.realpath(os.path.join(self.sys_net, iface, "device"))
        for _ in range(8):
            if os.path.exists(os.path.join(node, "idVendor")):
                return True
            parent = os.path.dirname(node)
            if parent == node:
                break
            node = parent
        return False

    def mac(self, iface):
        return _read(os.path.join(self.sys_net, iface, "address")).lower()

    def exists(self, iface):
        return os.path.exists(os.path.join(self.sys_net, iface))

    def phy_exists(self, phy):
        return os.path.exists(os.path.join(self.sys_phy, phy))

    def default_route(self, iface):
        try:
            with open(self.route, encoding="ascii", errors="replace") as handle:
                for line in handle.readlines()[1:]:
                    words = line.split()
                    if len(words) > 2 and words[0] == iface and words[1] == "00000000":
                        return True
        except OSError:
            pass
        return False

    def connected(self, iface):
        code, out, _ = self.run("iw", "dev", iface, "link")
        return code == 0 and out.lstrip().startswith("Connected")

    def phy_info(self, phy):
        code, out, _ = self.run("iw", "phy", phy, "info")
        return parse_phy(out) if code == 0 else {"monitor": False, "freqs": {}}

    def dev_info(self, iface):
        code, out, _ = self.run("iw", "dev", iface, "info")
        return parse_dev(out) if code == 0 else parse_dev("")

    def power_save(self, iface):
        code, out, _ = self.run("iw", "dev", iface, "get", "power_save")
        if code == 0 and "off" in out.lower():
            return "off"
        return "on" if code == 0 else None

    def regdom(self):
        code, out, _ = self.run("iw", "reg", "get")
        match = re.search(r"country ([A-Z0-9]{2}):", out) if code == 0 else None
        return match.group(1) if match else ""

    def set_freq(self, vif, ifindex, freq):
        """True over nl80211, False after falling back to iw (slower, so the dwell must be longer)."""
        try:
            if ifindex is None:
                raise OSError("no interface index")
            with self._nl_lock:
                if self._nl is None:
                    self._nl = Nl80211()
                self._nl.set_freq(ifindex, freq)
            return True
        except OSError:
            code, _, err = self.run("iw", "dev", vif, "set", "freq", freq)
            if code:
                raise Refused("channel change failed: %s" % err[:60])
            return False

    def capture(self, vif, mgmt_only):
        return Capture(vif, bpf_program(mgmt_only=mgmt_only))


class Hopper(threading.Thread):
    """One per monitor. A new plan starts when the running one has finished its cycle, so no step is cut short."""

    def __init__(self, system, vif, ifindex, plan):
        super().__init__(daemon=True, name="hop-" + vif)
        self.system, self.vif, self.ifindex, self.plan = system, vif, ifindex, plan
        self.pending = None
        self.stopped = threading.Event()
        self.freq, self.switched_at, self.error = 0, 0.0, ""
        self.cycle_ms = None

    def replace(self, plan):
        self.pending = plan

    def run(self):
        while not self.stopped.is_set():
            pending, self.pending = self.pending, None
            if pending is not None:
                self.plan = pending
            began = time.monotonic()
            for freq, dwell in self.plan:
                if self.stopped.is_set():
                    return
                try:
                    fast = self.system.set_freq(self.vif, self.ifindex, freq)
                except Exception as exc:
                    self.error = str(exc)[:80]
                    self.stopped.wait(1.0)
                    continue
                self.freq, self.switched_at = freq, time.monotonic()
                self.stopped.wait((dwell if fast else max(dwell, IW_DWELL)) / 1000.0)
            self.cycle_ms = round(1000 * (time.monotonic() - began))

    def stop(self):
        self.stopped.set()


class Session:
    """One client connection and everything it changed."""

    def __init__(self, helper, send, uid=0):
        self.helper, self.system, self._send = helper, helper.system, send
        self.uid = uid
        self.tuned = {}
        self.monitors = {}
        self.scanning = set()
        self.lock = threading.Lock()

    def send(self, kind, payload):
        with self.lock:
            self._send(kind + payload)

    def reply(self, answer):
        """One answer, its chunks sent together so that no other answer comes in between."""
        body = json.dumps(answer, separators=(",", ":")).encode()
        if len(body) <= CHUNK:
            messages = [b"J" + body]
        else:
            text = answer.pop("text", "").encode()
            pieces = [text[i:i + CHUNK] for i in range(0, len(text), CHUNK)]
            messages = [b"J" + json.dumps(dict(answer, chunks=len(pieces)), separators=(",", ":")).encode()]
            messages += [b"C" + piece for piece in pieces]
        with self.lock:
            for message in messages:
                self._send(message)

    def answer(self, raw):
        self.reply(self.handle(raw))

    def answer_quietly(self, raw):
        try:
            self.answer(raw)
        except OSError:
            pass

    def handle(self, raw):
        rid = request_id(raw)
        try:
            answer = dict(self.command(parse_request(raw)), ok=True)
        except Refused as exc:
            answer = {"ok": False, "error": str(exc)[:120]}
        except (OSError, subprocess.SubprocessError, ValueError) as exc:
            answer = {"ok": False, "error": ("%s: %s" % (type(exc).__name__, exc))[:120]}
        if rid is not None:
            answer["id"] = rid
        return answer

    def command(self, request):
        cmd = request["cmd"]
        return getattr(self, "do_" + cmd)(**{k: v for k, v in request.items() if k not in ("cmd", "id")})

    def do_hello(self):
        return {"version": VERSION, "caps": sorted(COMMANDS), "regdom": self.system.regdom()}

    def do_adapters(self):
        found = []
        for iface in self.system.wireless():
            phy = self.system.phy(iface)
            info = self.system.phy_info(phy) if phy else {"monitor": False, "freqs": {}}
            found.append({"interface": iface, "phy": phy, "driver": self.system.driver(iface),
                          "usb": self.system.usb(iface), "connected": self.system.connected(iface),
                          "default_route": self.system.default_route(iface), "monitor": info["monitor"],
                          "freqs": sorted(info["freqs"])})
        return {"adapters": found}

    def _external(self, iface):
        iface = check_name(iface)
        if _VIF.fullmatch(iface) or iface not in self.system.wireless():
            raise Refused("not a Wi-Fi adapter")
        if not self.system.usb(iface):
            raise Refused("not a USB adapter")
        return iface

    def do_scan(self, iface, freqs=None):
        iface = self._external(iface)
        words = ["dev", iface, "scan"]
        if freqs is not None:
            allowed = self.system.phy_info(self.system.phy(iface))["freqs"]
            words += ["freq"] + [str(f) for f in check_freqs(freqs, allowed)]
        with self.helper.lock:
            if iface in self.scanning or len(self.scanning) >= MAX_SCANS:
                raise Refused("busy: a scan is running")
            self.scanning.add(iface)
        try:
            code, out, err = self.system.run("iw", *words, timeout=SCAN_TIMEOUT)
        finally:
            with self.helper.lock:
                self.scanning.discard(iface)
        if code:
            raise Refused(err.splitlines()[-1][:100] if err else "scan failed")
        return {"text": out}

    def do_tune(self, iface):
        iface = self._external(iface)
        if iface not in self.tuned:
            self.tuned[iface] = {"power_save": self.system.power_save(iface) or "on"}
            self.helper.remember()
        self.system.run("iw", "dev", iface, "set", "power_save", "off")
        self.system.run("iw", "dev", iface, "set", "txpower", "auto")
        info = self.system.dev_info(iface)
        freqs = self.system.phy_info(self.system.phy(iface))["freqs"]
        limits = [f["dbm"] for f in freqs.values() if f["dbm"] is not None]
        here = freqs.get(info["freq"], {}).get("dbm") if info["freq"] else None
        limit = here if here is not None else (max(limits) if limits else None)
        if limit is not None and (info["txpower"] is None or info["txpower"] < limit):
            self.system.run("iw", "dev", iface, "set", "txpower", "limit", int(round(limit * 100)))
            info = self.system.dev_info(iface)
        return {"power_save": "off", "txpower": info["txpower"], "limit": limit}

    def do_untune(self, iface):
        iface = check_name(iface)
        saved = self.tuned.pop(iface, None)
        if saved is None:
            return {}
        self._untune(iface, saved)
        self.helper.remember()
        return {}

    def _untune(self, iface, saved):
        if not self.system.exists(iface):
            return
        self.system.run("iw", "dev", iface, "set", "txpower", "auto")
        self.system.run("iw", "dev", iface, "set", "power_save", saved.get("power_save", "on"))

    def do_monitor_start(self, iface):
        iface = self._external(iface)
        if self.system.driver(iface) not in DRIVERS:
            raise Refused("driver not allowed for monitor mode")
        phy = self.system.phy(iface)
        if not phy or not self.system.phy_info(phy)["monitor"]:
            raise Refused("no monitor mode")
        if self.system.connected(iface) or self.system.default_route(iface):
            raise Refused("connected to a network")
        with self.helper.lock:
            if self.helper.monitor_count() >= MAX_MONITORS:
                raise Refused("too many monitors")
            taken = {name for s in list(self.helper.sessions) for name in list(s.monitors)}
            vif = next((name for name in ("qmmon%d" % n for n in range(10))
                        if name not in taken and not self.system.exists(name)), None)
            if vif is None:
                raise Refused("no free monitor name")
            entry = {"phy": phy, "name": iface, "mac": self.system.mac(iface), "type": "managed", "vif": vif}
            self.monitors[vif] = entry
        self.helper.remember()
        code, _, err = self.system.run("iw", "dev", iface, "del")
        if code:
            self.monitors.pop(vif, None)
            self.helper.remember()
            raise Refused("could not free the adapter: %s" % err[:60])
        code, _, err = self.system.run("iw", "phy", phy, "interface", "add", vif, "type", "monitor")
        if code == 0:
            code, _, err = self.system.run("ip", "link", "set", vif, "up")
        if code:
            self._monitor_stop(vif)
            raise Refused("monitor mode failed: %s" % err[:60])
        entry["ifindex"] = self.system.dev_info(vif)["ifindex"]
        return {"vif": vif}

    def _owned(self, vif):
        vif = check_name(vif)
        if vif not in self.monitors:
            raise Refused("not this connection's monitor")
        return vif, self.monitors[vif]

    def do_hop_plan(self, vif, plan):
        vif, entry = self._owned(vif)
        allowed = self.system.phy_info(entry["phy"])["freqs"]
        steps = check_plan(plan, allowed)
        hopper = entry.get("hopper")
        if hopper is not None and hopper.is_alive():
            hopper.replace(steps)
        else:
            hopper = entry["hopper"] = Hopper(self.system, vif, entry.get("ifindex"), steps)
            hopper.start()
        return {"steps": len(steps), "cycle_ms": hopper.cycle_ms}

    def do_capture(self, vif, mgmt_only=False):
        vif, entry = self._owned(vif)
        if not isinstance(mgmt_only, bool):
            raise Refused("bad mgmt_only")
        old = entry.pop("relay", None)
        if old is not None:
            old.stop()
        relay = entry["relay"] = Relay(self, self.system.capture(vif, mgmt_only), entry)
        relay.start()
        return {}

    def do_monitor_stop(self, vif):
        vif, _ = self._owned(vif)
        self._monitor_stop(vif)
        return {}

    def _monitor_stop(self, vif):
        entry = self.monitors.get(vif)
        if entry is None:
            return
        for part in ("relay", "hopper"):
            worker = entry.pop(part, None)
            if worker is not None:
                worker.stop()
        undo_monitor(self.system, entry)
        self.monitors.pop(vif, None)
        self.helper.remember()

    def restore(self):
        """Put back everything this connection changed, newest first: monitors, then tuning."""
        for vif in list(self.monitors)[::-1]:
            try:
                self._monitor_stop(vif)
            except Exception:
                self.monitors.pop(vif, None)
        for iface in list(self.tuned)[::-1]:
            try:
                self._untune(iface, self.tuned.pop(iface))
            except Exception:
                pass
        self.helper.remember()

    def undo_entries(self):
        return ([{"kind": "monitor", **{k: v for k, v in e.items() if k in ("phy", "name", "mac", "vif")}}
                 for e in self.monitors.values()] +
                [{"kind": "tune", "iface": iface, **saved} for iface, saved in self.tuned.items()])


def undo_monitor(system, entry):
    """The adapter back to one managed interface, on its own phy only: a name another device took is left alone."""
    vif, name, phy = entry["vif"], entry["name"], entry.get("phy")
    if system.exists(vif) and system.phy(vif) == phy:
        system.run("ip", "link", "set", vif, "down")
        system.run("iw", "dev", vif, "del")
    if not phy or not system.phy_exists(phy):
        return
    here = next((n for n in system.wireless() if system.phy(n) == phy and not _VIF.fullmatch(n)), None)
    if here is None:
        here = name if not system.exists(name) else next(
            (n for n in ("wlan%d" % i for i in range(16)) if not system.exists(n)), None)
        if here is None:
            return
        system.run("iw", "phy", phy, "interface", "add", here, "type", "managed")
        if not system.exists(here):
            return
    mac = entry.get("mac")
    if mac and _MAC.fullmatch(mac) and system.mac(here) != mac:
        system.run("ip", "link", "set", here, "address", mac)


class Relay(threading.Thread):
    """Frames from the capture socket to the client, in batches: a header, then u16 length + bytes per frame."""

    HEADER = struct.Struct("<IIId")

    def __init__(self, session, capture, entry):
        super().__init__(daemon=True, name="relay-" + entry["vif"])
        self.session, self.capture, self.entry = session, capture, entry
        self.stopped = threading.Event()
        self.received = self.dropped = self.lost_batches = 0

    def run(self):
        buffer = bytearray(65536)
        view = memoryview(buffer)
        batch, started = bytearray(), time.monotonic()
        try:
            while not self.stopped.is_set():
                ready, _, _ = select.select([self.capture], [], [], BATCH_SECONDS)
                size = self.capture.recv_into(buffer) if ready else 0
                now = time.monotonic()
                if batch and (len(batch) + size + 2 > BATCH_BYTES - self.HEADER.size or
                              now - started >= BATCH_SECONDS):
                    self.flush(batch)
                    batch = bytearray()
                if not batch:
                    started = now
                if 0 < size <= BATCH_BYTES - self.HEADER.size - 2:
                    batch += struct.pack("<H", size)
                    batch += view[:size]
        except OSError:
            pass
        finally:
            self.capture.close()

    def flush(self, batch):
        received, dropped = self.capture.stats()
        self.received += received
        self.dropped += dropped
        hopper = self.entry.get("hopper")
        freq, switched = (hopper.freq, hopper.switched_at) if hopper is not None else (0, 0.0)
        try:
            self.session.send(b"F", self.HEADER.pack(self.received, self.dropped, freq, switched) + bytes(batch))
        except socket.timeout:
            self.lost_batches += 1
        except OSError:
            self.stopped.set()

    def stop(self):
        self.stopped.set()


def check_name(value):
    if not isinstance(value, str) or not _NAME.fullmatch(value):
        raise Refused("bad interface name")
    return value


def _int(value):
    if isinstance(value, bool) or not isinstance(value, int):
        raise Refused("not a number")
    return value


def check_freqs(freqs, allowed):
    if not isinstance(freqs, list) or not 0 < len(freqs) <= MAX_PLAN:
        raise Refused("bad frequency list")
    out = []
    for freq in freqs:
        if _int(freq) not in allowed:
            raise Refused("frequency %s not allowed on this radio" % freq)
        out.append(freq)
    return out


def check_plan(plan, allowed):
    if not isinstance(plan, list) or not 0 < len(plan) <= MAX_PLAN:
        raise Refused("bad hop plan")
    steps = []
    for step in plan:
        if not isinstance(step, list) or len(step) != 3:
            raise Refused("bad hop step")
        freq, width, dwell = (_int(v) for v in step)
        if freq not in allowed:
            raise Refused("frequency %d not allowed on this radio" % freq)
        if width != 20:
            raise Refused("only 20 MHz")
        if not MIN_DWELL <= dwell <= MAX_DWELL:
            raise Refused("dwell must be %d to %d ms" % (MIN_DWELL, MAX_DWELL))
        steps.append((freq, dwell))
    return steps


def parse_request(raw):
    if len(raw) > MAX_MESSAGE:
        raise Refused("message too long")
    try:
        request = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise Refused("not JSON")
    if not isinstance(request, dict) or not isinstance(request.get("cmd"), str) or request["cmd"] not in COMMANDS:
        raise Refused("unknown command")
    allowed = set(COMMANDS[request["cmd"]]) | {"cmd", "id"}
    if set(request) - allowed:
        raise Refused("unexpected argument")
    if "id" in request and request_id(raw) is None:
        raise Refused("bad id")
    if any(key not in request for key in REQUIRED.get(request["cmd"], ())):
        raise Refused("missing argument")
    return request


def request_id(raw):
    """The id a request carries, which its answer echoes, or None."""
    if len(raw) > MAX_MESSAGE:
        return None
    try:
        request = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None
    rid = request.get("id") if isinstance(request, dict) else None
    if isinstance(rid, bool) or not isinstance(rid, int) or not 0 < rid <= MAX_ID:
        return None
    return rid


def is_scan(raw):
    try:
        return parse_request(raw)["cmd"] == "scan"
    except Refused:
        return False


def peer_allowed(uid, gid, members=None):
    """Only a regular user in the quartermaster-radio group, besides the socket's own 0660 mode."""
    if uid < 1000 or uid >= 60000:
        return False
    try:
        target = grp.getgrnam(GROUP).gr_gid
        if members is None:
            members = os.getgrouplist(pwd.getpwuid(uid).pw_name, gid)
    except (KeyError, OSError):
        return False
    return target in members


class Helper:
    def __init__(self, system=None, state_path=None):
        self.system = system or System()
        self.state_path = state_path
        self.sessions = []
        self.lock = threading.RLock()
        self.last_client = time.monotonic()

    def monitor_count(self):
        return sum(len(s.monitors) for s in self.sessions)

    def open_session(self, send, uid=0):
        with self.lock:
            if len(self.sessions) >= MAX_CLIENTS:
                return None
            session = Session(self, send, uid)
            self.sessions.append(session)
            return session

    def close_session(self, session):
        try:
            session.restore()
        finally:
            with self.lock:
                if session in self.sessions:
                    self.sessions.remove(session)
                self.last_client = time.monotonic()
            self.remember()

    def remember(self):
        if not self.state_path:
            return
        with self.lock:
            entries = [e for s in self.sessions for e in s.undo_entries()]
        tmp = self.state_path + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as handle:
                json.dump(entries, handle)
            os.replace(tmp, self.state_path)
        except OSError:
            pass

    def recover(self):
        """Put back what an earlier run left when it was killed."""
        if not self.state_path:
            return
        try:
            with open(self.state_path, encoding="utf-8") as handle:
                entries = json.load(handle)
        except (OSError, ValueError):
            return
        for entry in entries if isinstance(entries, list) else ():
            try:
                if not isinstance(entry, dict):
                    continue
                if entry.get("kind") == "monitor" and _VIF.fullmatch(str(entry.get("vif"))) and \
                        _NAME.fullmatch(str(entry.get("name"))) and _NAME.fullmatch(str(entry.get("phy"))):
                    undo_monitor(self.system, entry)
                elif entry.get("kind") == "tune" and _NAME.fullmatch(str(entry.get("iface"))) and \
                        entry.get("power_save") in ("on", "off") and self.system.exists(entry["iface"]):
                    self.system.run("iw", "dev", entry["iface"], "set", "txpower", "auto")
                    self.system.run("iw", "dev", entry["iface"], "set", "power_save", entry["power_save"])
            except Exception:
                pass
        self.remember()

    def restore_all(self):
        for session in list(self.sessions):
            self.close_session(session)

    def serve_connection(self, conn):
        try:
            creds = conn.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
            _, uid, gid = struct.unpack("3i", creds)
        except OSError:
            conn.close()
            return
        if not peer_allowed(uid, gid):
            _quiet_send(conn, b"J" + b'{"ok":false,"error":"not in the quartermaster-radio group"}')
            conn.close()
            return
        conn.settimeout(5.0)
        session = self.open_session(conn.send, uid)
        if session is None:
            _quiet_send(conn, b"J" + b'{"ok":false,"error":"busy"}')
            conn.close()
            return
        try:
            while True:
                try:
                    ready, _, _ = select.select([conn], [], [], 1.0)
                    if not ready:
                        continue
                    raw = conn.recv(MAX_MESSAGE + 1)
                except socket.timeout:
                    continue
                if not raw:
                    break
                if is_scan(raw):
                    threading.Thread(target=session.answer_quietly, args=(raw,), daemon=True, name="scan").start()
                else:
                    session.answer(raw)
        except OSError:
            pass
        finally:
            self.close_session(session)
            conn.close()

    def serve(self, listener):
        listener.settimeout(1.0)
        threads = []
        while True:
            threads = [t for t in threads if t.is_alive()]
            if not threads and time.monotonic() - self.last_client > IDLE_EXIT:
                return
            try:
                conn, _ = listener.accept()
            except socket.timeout:
                continue
            self.last_client = time.monotonic()
            thread = threading.Thread(target=self.serve_connection, args=(conn,), daemon=True)
            thread.start()
            threads.append(thread)


def _quiet_send(conn, data):
    try:
        conn.send(data)
    except OSError:
        pass


def _listener():
    if os.environ.get("LISTEN_PID") == str(os.getpid()) and os.environ.get("LISTEN_FDS") == "1":
        return socket.socket(fileno=3)
    path = os.environ.get("QM_RADIO_SOCKET", SOCKET_PATH)
    try:
        os.unlink(path)
    except OSError:
        pass
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET)
    sock.bind(path)
    os.chmod(path, 0o660)
    sock.listen(MAX_CLIENTS)
    return sock


def main():
    runtime = os.environ.get("RUNTIME_DIRECTORY", "").split(":")[0]
    helper = Helper(state_path=os.path.join(runtime, "state.json") if runtime else None)
    helper.recover()

    def finish(*_):
        helper.restore_all()
        sys.exit(0)
    signal.signal(signal.SIGTERM, finish)
    signal.signal(signal.SIGINT, finish)
    try:
        helper.serve(_listener())
    finally:
        helper.restore_all()


if __name__ == "__main__":
    main()
