"""Quartermaster radio helper: the one privileged step Deep scan, direct scans, radio tuning and Biscuit node mode
need.

systemd starts it on the first connection to /run/quartermaster/radio.sock, as a dynamic user holding only
CAP_NET_ADMIN and CAP_NET_RAW. It takes a fixed set of JSON commands from members of the quartermaster-radio group
and checks every argument. It has no command for the regulatory domain, never touches a radio that is not on USB,
and runs no shell. The only frames it sends are a Biscuit node's own messages on 2.4 GHz channel 6, built here, while
Biscuit node mode runs. Everything a connection changes is put back when that connection closes, and what a crash
left behind is put back at the next start.
"""

import array
import fcntl
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
SYS_USB = "/sys/bus/usb/devices"
DEV_USB = "/dev/bus/usb"
PROC_ROUTE = "/proc/net/route"
MAX_MESSAGE = 4096
MAX_CLIENTS = 2
MAX_MONITORS = 6
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
RESET_IDS = frozenset({"0e8d:7961", "0e8d:7925", "0e8d:7612", "0e8d:7610", "0bda:b812", "0bda:c811", "0cf3:9271"})
RESET_GAP, RESET_MAX, RESET_WINDOW = 5.0, 3, 600.0
USBDEVFS_RESET = 0x5514
COMMANDS = {"hello": (), "adapters": (), "scan": ("iface", "freqs"), "tune": ("iface",), "untune": ("iface",),
            "monitor_start": ("iface",), "hop_plan": ("vif", "plan"), "capture": ("vif", "mgmt_only"),
            "monitor_stop": ("vif",), "usb_reset": ("port",), "biscuit_node": ("vif", "target", "pair", "device")}
REQUIRED = {"scan": ("iface",), "tune": ("iface",), "untune": ("iface",), "monitor_start": ("iface",),
            "hop_plan": ("vif", "plan"), "capture": ("vif",), "monitor_stop": ("vif",), "usb_reset": ("port",),
            "biscuit_node": ("vif", "device")}
TOOLS = {"iw": ("/usr/sbin/iw", "/sbin/iw", "/usr/bin/iw"), "ip": ("/usr/sbin/ip", "/sbin/ip", "/usr/bin/ip", "/bin/ip")}
_NAME = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,14}")
_VIF = re.compile(r"qmmon[0-9]")
_MAC = re.compile(r"[0-9a-f]{2}(?::[0-9a-f]{2}){5}")
_PORT = re.compile(r"[1-9][0-9]{0,2}-[1-9][0-9]{0,2}(?:\.[1-9][0-9]{0,2}){0,5}")

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

    def __init__(self, sys_net=SYS_NET, route=PROC_ROUTE, runner=subprocess.run, sys_phy=SYS_PHY, sys_usb=SYS_USB,
                 dev_usb=DEV_USB):
        self.sys_net, self.route, self.runner, self.sys_phy = sys_net, route, runner, sys_phy
        self.sys_usb, self.dev_usb = sys_usb, dev_usb
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

    def node_socket(self, vif):
        return NodeSocket(vif, bytes.fromhex(self.mac(vif).replace(":", "")))

    def usb_device(self, port):
        """(vendor:product, bus number, device number, has a network interface) of a USB device, or None."""
        node = os.path.join(self.sys_usb, port)
        usb_id = "%s:%s" % (_read(os.path.join(node, "idVendor")).lower(),
                            _read(os.path.join(node, "idProduct")).lower())
        bus, dev = _read(os.path.join(node, "busnum")), _read(os.path.join(node, "devnum"))
        if not bus.isdigit() or not dev.isdigit():
            return None
        try:
            parts = [p for p in os.listdir(node) if p.startswith(port + ":")]
        except OSError:
            return None
        working = any(os.path.isdir(os.path.join(node, p, sub)) and os.listdir(os.path.join(node, p, sub))
                      for p in parts for sub in ("net", "ieee80211"))
        return usb_id, int(bus), int(dev), working

    def usb_reset(self, bus, dev):
        """USBDEVFS_RESET on the device's own node, which the udev rule lets this helper's group write."""
        fd = os.open(os.path.join(self.dev_usb, "%03d" % bus, "%03d" % dev), os.O_WRONLY | os.O_CLOEXEC)
        try:
            fcntl.ioctl(fd, USBDEVFS_RESET, 0)
        finally:
            os.close(fd)


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

    def do_biscuit_node(self, vif, device, target=None, pair=False):
        """Biscuit node mode on a monitor this connection owns, with nothing else running on it. Its messages come
        back as B frames: {"kind": "status" | "found" | "error", ...}."""
        vif, entry = self._owned(vif)
        if device not in BISCUIT_LABELS:
            raise Refused("bad device")
        if not isinstance(pair, bool):
            raise Refused("bad pair")
        if target is not None and (not isinstance(target, str) or not _TARGET.fullmatch(target)):
            raise Refused("bad target")
        if pair == (target is not None):
            raise Refused("pair, or give a target")
        for part in ("relay", "hopper", "node"):
            worker = entry.get(part)
            if worker is not None and worker.is_alive():
                raise Refused("the monitor is busy")
        allowed = self.system.phy_info(entry["phy"])["freqs"]
        if BISCUIT_HOME not in allowed:
            raise Refused("channel 6 is not allowed on this radio")
        channels = {(f - 5950) // 5 for f in allowed if 5955 <= f <= 7115 and (f - 5955) % 20 == 0}
        if not channels:
            raise Refused("no 6 GHz channels: this adapter or the Wi-Fi country has none")
        radio = self.system.node_socket(vif)
        ifindex = entry.get("ifindex")

        def report(kind, payload):
            try:
                self.send(b"B", json.dumps(dict(payload, kind=kind), separators=(",", ":")).encode())
            except socket.timeout:
                pass
            except OSError:
                node.stop()
        node = entry["node"] = BiscuitNode(lambda freq: self.system.set_freq(vif, ifindex, freq), radio, report,
                                           channels, target=bytes.fromhex(target.replace(":", "")) if target else None,
                                           pair=pair, label=BISCUIT_LABELS[device])
        node.start()
        return {"channels": len(channels)}

    def do_usb_reset(self, port):
        """Reset a known USB Wi-Fi adapter that is on the bus but has no network interface: what a brownout leaves.
        A working adapter, any other device and the built-in radio are refused, and resets are spaced out."""
        port = check_port(port)
        found = self.system.usb_device(port)
        if found is None:
            raise Refused("no USB device there")
        usb_id, bus, dev, working = found
        if usb_id not in RESET_IDS:
            raise Refused("not a known Wi-Fi adapter")
        if working:
            raise Refused("the adapter is working")
        now = self.helper.clock()
        with self.helper.lock:
            done = [at for at in self.helper.resets.get(port, ()) if now - at < RESET_WINDOW]
            if done and now - done[-1] < RESET_GAP or len(done) >= RESET_MAX:
                raise Refused("reset again later")
            self.helper.resets[port] = done + [now]
        self.system.usb_reset(bus, dev)
        return {}

    def do_monitor_stop(self, vif):
        vif, _ = self._owned(vif)
        self._monitor_stop(vif)
        return {}

    def _monitor_stop(self, vif):
        entry = self.monitors.get(vif)
        if entry is None:
            return
        for part in ("relay", "hopper", "node"):
            worker = entry.pop(part, None)
            if worker is not None:
                worker.stop()
                if part == "node" and worker is not threading.current_thread():
                    worker.join(2)
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


BISCUIT_FIRMWARE = b"Pager:v1.1.0"
BISCUIT_LABELS = {"cardputer": b"Pager 6GHz node: Quartermaster, CardputerZero",
                  "pi": b"Pager 6GHz node: Quartermaster, DIY Pi"}
BISCUIT_HOME = 2437
ESPRESSIF = b"\x18\xfe\x34"
BROADCAST = b"\xff" * 6
ENOW_TEXT = 200
CORE_REQUEST, CORE_REPLY, NODE_HEARTBEAT, NODE_TEXT, ROLE_ASSIGN, OTA_COMMAND, FW_VERSION = 1, 2, 3, 4, 5, 6, 7
STOP_SCAN, STOP_ACK, CONFIG_UPDATE, CONFIG_ACK, CONFIG_REQUEST, ROLE_ACK = 8, 9, 10, 11, 12, 15
WARDRIVE_BATCH, RELEASE = 19, 26
SCAN_ROLE, AP6_RECORD, BATCH_ROOM, SEND_PER_VISIT = 1, 0x04, 242, 12
PSC_6GHZ = tuple(range(5, 230, 16))
ALL_6GHZ = tuple(range(1, 234, 4))
NODE_DWELL, NODE_SCAN, NODE_HOME = 0.11, 0.9, 0.15
REQUEST_GAP, HEARTBEAT_GAP, CONFIG_GAP, FIRMWARE_GAP = 0.3, 3.0, 30.0, 2.0
RESEND_SECONDS, RESEND_GAIN, LOST_SECONDS, ACK_WINDOW, STATUS_GAP = 30.0, 6, 20.0, 0.05, 1.0
NODE_RADIOTAP = struct.pack("<BBHIBxH", 0, 0, 12, 1 << 2 | 1 << 15, 2, 0)
NODE_FILTER = ((0x30, 0, 0, 3), (0x64, 0, 0, 8), (0x07, 0, 0, 0), (0x30, 0, 0, 2), (0x0C, 0, 0, 0),
               (0x07, 0, 0, 0), (0x50, 0, 0, 0), (0x15, 3, 0, 0x80), (0x15, 2, 0, 0x50), (0x15, 1, 0, 0xD0),
               (0x15, 0, 1, 0xD4), (0x06, 0, 0, 4096), (0x06, 0, 0, 0))
AKM_8021X, AKM_PSK, AKM_SAE, AKM_OWE, AKM_SUITE_B = {1, 3, 5}, {2, 4, 6}, {8, 9, 24, 25}, {18}, {11, 12}
_TARGET = re.compile(r"[0-9A-F]{2}(?::[0-9A-F]{2}){5}")


def enow(kind, counter=0, text=b""):
    text = bytes(text)[:ENOW_TEXT]
    return struct.pack("<4sBIH", b"ENOW", kind, counter & 0xFFFFFFFF, len(text)) + text.ljust(ENOW_TEXT + 1, b"\0")


def read_enow(payload):
    if len(payload) < 11 or payload[:4] != b"ENOW":
        return None
    counter, size = struct.unpack_from("<IH", payload, 5)
    return payload[4], counter, bytes(payload[11:11 + min(size, ENOW_TEXT)])


def espnow_frame(dst, src, seq, payload, nonce=None):
    if not 5 <= len(payload) <= 250:
        raise ValueError("ESP-NOW payloads are 5 to 250 bytes")
    nonce = os.urandom(4) if nonce is None else nonce
    header = struct.pack("<HH6s6s6sH", 0x00D0, 0, dst, src, BROADCAST, (seq & 0xFFF) << 4)
    return header + b"\x7f" + ESPRESSIF + nonce + b"\xdd" + bytes([len(payload) + 5]) + ESPRESSIF + b"\x04\x01" + \
        payload


def read_espnow(frame):
    if len(frame) < 39 or frame[0] & 0xFC != 0xD0:
        return None
    body = frame[24:]
    if body[0] != 0x7F or body[1:4] != ESPRESSIF or body[8] != 0xDD or body[10:13] != ESPRESSIF or body[13] != 4:
        return None
    end = 10 + body[9]
    if end > len(body) or body[9] < 5:
        return None
    return bytes(frame[4:10]), bytes(frame[10:16]), bytes(body[15:end])


def ap6_record(bssid, channel, rssi, code, ssid):
    ssid = bytes(ssid)[:32]
    return bytes([AP6_RECORD]) + bytes(bssid) + struct.pack("<BbBB", channel, max(-128, min(127, int(rssi))), code,
                                                            len(ssid)) + ssid


def wardrive_batches(seq, records):
    out, body, ids, used = [], [], [], 0
    for bssid, raw in records:
        if body and used + len(raw) > BATCH_ROOM:
            out.append((b"ENOW" + bytes((WARDRIVE_BATCH, 2, seq & 0xFF, len(body))) + b"".join(body), ids))
            seq, body, ids, used = seq + 1, [], [], 0
        body.append(raw)
        ids.append(bssid)
        used += len(raw)
    if body:
        out.append((b"ENOW" + bytes((WARDRIVE_BATCH, 2, seq & 0xFF, len(body))) + b"".join(body), ids))
        seq += 1
    return seq & 0xFF, out


def radiotap_head(buf):
    """(header length, frame end, dBm or None, MHz or None) of a radiotap frame, or None."""
    n = len(buf)
    if n < 8 or buf[0] != 0:
        return None
    length = buf[2] | buf[3] << 8
    if length < 8 or length > n:
        return None
    first = present = int.from_bytes(buf[4:8], "little")
    at = 8
    while present & 0x80000000:
        if at + 4 > length:
            return None
        present = int.from_bytes(buf[at:at + 4], "little")
        at += 4
    flags, freq, rssi = 0, None, None
    for bit, size, align in ((0, 8, 8), (1, 1, 1), (2, 1, 1), (3, 4, 2), (4, 2, 1), (5, 1, 1)):
        if not first >> bit & 1:
            continue
        at = (at + align - 1) & ~(align - 1)
        if at + size > length:
            return None
        if bit == 1:
            flags = buf[at]
        elif bit == 3:
            freq = buf[at] | buf[at + 1] << 8
        elif bit == 5:
            rssi = buf[at] - 256 if buf[at] > 127 else buf[at]
        at += size
    if flags & 0x40:
        return None
    end = n - 4 if flags & 0x10 else n
    return (length, end, rssi, freq) if end >= length else None


def _akms(body):
    if len(body) < 8:
        return set()
    at = 6
    count = body[at] | body[at + 1] << 8
    at += 2 + 4 * count
    if len(body) < at + 2:
        return set()
    count = body[at] | body[at + 1] << 8
    at += 2
    return {body[at + 4 * i + 3] for i in range(count) if len(body) >= at + 4 * i + 4 and
            body[at + 4 * i:at + 4 * i + 3] == b"\x00\x0f\xac"}


def six_ghz_network(frame, rssi, freq):
    """(bssid, channel, rssi, security code, ssid bytes) of a 6 GHz beacon or probe response, or None."""
    if len(frame) < 36 or frame[0] not in (0x80, 0x50) or rssi is None or not 5925 <= (freq or 0) <= 7125:
        return None
    privacy = bool((frame[34] | frame[35] << 8) & 0x10)
    ssid, rsn, wpa, wpa_eap, he = b"", None, False, False, None
    at, n = 36, len(frame)
    while at + 2 <= n:
        ie, size = frame[at], frame[at + 1]
        body = frame[at + 2:at + 2 + size]
        if len(body) < size:
            break
        if ie == 0:
            ssid = bytes(body[:32]) if bytes(body[:32]).strip(b"\0") else b""
        elif ie == 48:
            rsn = _akms(bytes(body))
        elif ie == 221 and body[:4] == b"\x00\x50\xf2\x01":
            wpa = True
            wpa_eap = b"\x00\x50\xf2\x01" in bytes(body[4:])[12:]
        elif ie == 255 and size >= 7 and body[0] == 36:
            params = body[1] | body[2] << 8 | body[3] << 16
            o = 7 + (3 if params >> 14 & 1 else 0) + (1 if params >> 15 & 1 else 0)
            if params >> 17 & 1 and size >= o + 5:
                he = body[o]
        at += 2 + size
    channel = he or (freq - 5950) // 5
    if not 1 <= channel <= 233 or (channel - 1) % 4:
        return None
    if rsn is not None:
        if rsn & AKM_OWE:
            code = 0x0D
        elif rsn & AKM_SUITE_B:
            code = 0x07
        elif rsn & AKM_8021X:
            code = 0x0F if rsn & AKM_SAE else 0x06
        elif rsn & AKM_SAE:
            code = 0x09 if rsn & AKM_PSK else 0x04
        else:
            code = 0x05 if wpa else 0x03
    elif wpa:
        code = 0x0E if wpa_eap else 0x02
    else:
        code = 0x01 if privacy else 0x00
    return bytes(frame[16:22]), channel, rssi, code, ssid


class NodeSocket:
    """The node's own packet socket on its monitor interface: it reads beacons, probe responses, action and ACK
    frames, and sends only what the node builds."""

    def __init__(self, vif, mac):
        self.mac = mac
        sock = socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.htons(ETH_P_ALL))
        try:
            code = array.array("B", _pack_program(NODE_FILTER))
            sock.setsockopt(socket.SOL_SOCKET, SO_ATTACH_FILTER,
                            struct.pack("HP", len(NODE_FILTER), code.buffer_info()[0]))
            sock.setsockopt(socket.SOL_SOCKET, SO_LOCK_FILTER, 1)
            sock.bind((vif, socket.htons(ETH_P_ALL)))
        except BaseException:
            sock.close()
            raise
        self.sock = sock
        self.seq = 0

    def send(self, dst, payload):
        self.seq = (self.seq + 1) & 0xFFF
        try:
            self.sock.send(NODE_RADIOTAP + espnow_frame(dst, self.mac, self.seq, payload))
            return True
        except OSError:
            return False

    def recv(self, timeout):
        ready, _, _ = select.select([self.sock], [], [], max(0.0, timeout))
        out = []
        for _ in range(64 if ready else 0):
            try:
                out.append(self.sock.recv(4096, socket.MSG_DONTWAIT))
            except BlockingIOError:
                break
        return out

    def close(self):
        self.sock.close()


class BiscuitNode(threading.Thread):
    """A Biscuit Pager node on one monitor interface: it reports as a Pager node, scans 6 GHz and nothing else,
    and goes to channel 6 only to talk to the Biscuit. `pair` only lists the Biscuits that answer."""

    def __init__(self, tune, radio, report, channels, target=None, pair=False, label=b"", clock=time.monotonic):
        super().__init__(daemon=True, name="biscuit-node")
        self.tune_to, self.radio, self.report, self.channels = tune, radio, report, channels
        self.target, self.pair, self.label, self.clock = target, pair, label, clock
        self.stopped = threading.Event()
        self.core, self.ap6, self.role = None, False, 0
        self.state = "pairing" if pair else "searching"
        self.hb = self.seq = self.sent = self.tx_errors = self.acks = self.sweeps = self.tune_failures = 0
        self.outbox, self.sent_ids, self.heard, self.pending, self.last_sent, self.found = [], set(), set(), {}, {}, {}
        self.last_unicast, self.last_ack, self.last_heard = -1e9, None, None
        self.firmware_tries, self.told, self.channel, self.freq = 0, False, None, None
        self.plan, self.at = [], 0
        self.next = {"request": 0.0, "firmware": 0.0, "heartbeat": 0.0, "config": 0.0, "status": 0.0}

    def tune(self, freq):
        if freq == self.freq:
            return True
        try:
            self.tune_to(freq)
        except Exception:
            self.tune_failures += 1
            return False
        self.freq = freq
        return True

    def send(self, dst, payload):
        if self.radio.send(dst, payload):
            if dst != BROADCAST:
                self.last_unicast = self.clock()
            return True
        self.tx_errors += 1
        return False

    def frame(self, raw):
        head = radiotap_head(raw)
        if head is None:
            return
        length, end, rssi, freq = head
        fr = raw[length:end]
        if len(fr) >= 10 and fr[0] == 0xD4:
            if fr[4:10] == self.radio.mac and self.clock() - self.last_unicast < ACK_WINDOW:
                self.acks += 1
            return
        if fr[:1] in (b"\x80", b"\x50"):
            if self.state == "scanning":
                found = six_ghz_network(fr, rssi, freq)
                if found is not None:
                    self.sighting(*found)
            return
        got = read_espnow(fr)
        if got is None:
            return
        dst, src, payload = got
        if src == self.radio.mac or dst not in (self.radio.mac, BROADCAST):
            return
        message = read_enow(payload)
        if message is not None:
            self.handle(src, *message)

    def sighting(self, bssid, channel, rssi, code, ssid):
        self.heard.add(bssid)
        last = self.last_sent.get(bssid)
        if last is not None and self.clock() - last[0] < RESEND_SECONDS and rssi < last[1] + RESEND_GAIN:
            return
        held = self.pending.get(bssid)
        if held is None or rssi > held[1]:
            self.pending[bssid] = (channel, rssi, code, ssid)

    def handle(self, src, kind, counter, text):
        now = self.clock()
        if kind == CORE_REPLY:
            if b"wdbatch=" not in text and b"nodecfg=" not in text:
                return
            secure = b"encrypt=1" in text
            if self.pair:
                if src not in self.found:
                    self.found[src] = {"mac": ":".join("%02X" % b for b in src), "secure": secure,
                                       "ap6": b"ap6=1" in text, "text": text.decode("ascii", "replace")[:80]}
                    self.report("found", self.found[src])
                return
            if self.core is not None or (self.target and src != self.target):
                return
            if secure:
                self.state = "secure"
                return
            self.core, self.ap6, self.role = src, b"ap6=1" in text, 0
            self.state = "joined" if self.ap6 else "old"
            self.last_heard, self.last_ack = now, None
            self.firmware_tries, self.told = 0, False
            self.next.update(firmware=now, heartbeat=now, config=now)
            return
        if self.core is None or src != self.core:
            return
        self.last_heard = now
        if kind == ROLE_ASSIGN:
            if len(text) == 5:
                self.role, token = text[0], text[1:5]
            else:
                self.role, token = (text[0] if text else counter & 0xFF), b""
            self.send(self.core, enow(ROLE_ACK, self.role, token))
            if self.ap6:
                self.state = "scanning" if self.role == SCAN_ROLE else "joined"
        elif kind == CONFIG_UPDATE:
            self.send(self.core, enow(CONFIG_ACK))
        elif kind == STOP_SCAN:
            self.role = 0
            self.outbox.clear()
            self.pending.clear()
            if self.ap6:
                self.state = "joined"
            self.send(self.core, enow(STOP_ACK))
        elif kind == RELEASE:
            self.lost()

    def lost(self):
        self.core, self.role, self.ap6, self.last_ack = None, 0, False, None
        self.outbox.clear()
        self.state = "searching"

    def listen(self, seconds):
        end = self.clock() + seconds
        while not self.stopped.is_set():
            left = end - self.clock()
            if left <= 0:
                return
            for raw in self.radio.recv(min(left, 0.05)):
                try:
                    self.frame(raw)
                except (IndexError, struct.error, ValueError):
                    pass

    def home(self, seconds):
        self.tune(BISCUIT_HOME)
        self.channel = None
        now = self.clock()
        if self.core is not None and self.last_ack is not None and now - self.last_ack > LOST_SECONDS:
            self.lost()
        if self.core is None:
            if now >= self.next["request"]:
                self.send(BROADCAST, enow(CORE_REQUEST, 0, b"bsc=1"))
                self.next["request"] = now + (5.0 if self.state == "secure" else REQUEST_GAP)
        else:
            self.check_in(now)
        acks = self.acks
        self.listen(seconds)
        if self.acks > acks:
            self.last_ack = self.clock()

    def check_in(self, now):
        if self.firmware_tries < 3 and now >= self.next["firmware"]:
            self.send(self.core, enow(FW_VERSION, 0, BISCUIT_FIRMWARE))
            self.firmware_tries += 1
            self.next["firmware"] = now + FIRMWARE_GAP
            if not self.told and self.label:
                self.told = self.send(self.core, enow(NODE_TEXT, 0, self.label))
        if now >= self.next["heartbeat"]:
            self.send(self.core, enow(NODE_HEARTBEAT, self.hb))
            self.hb += 1
            self.next["heartbeat"] = now + HEARTBEAT_GAP
        if now >= self.next["config"]:
            self.send(self.core, enow(CONFIG_REQUEST))
            self.next["config"] = now + CONFIG_GAP
        for _ in range(min(len(self.outbox), SEND_PER_VISIT)):
            payload, ids = self.outbox[0]
            if not self.send(self.core, payload):
                break
            self.outbox.pop(0)
            self.sent += len(ids)
            self.sent_ids.update(ids)
            self.stopped.wait(0.008)

    def scan(self):
        end = self.clock() + NODE_SCAN
        while not self.stopped.is_set() and self.state == "scanning" and self.clock() < end:
            if self.at >= len(self.plan):
                self.sweeps += 1
                every = [c for c in ALL_6GHZ if c in self.channels]
                psc = [c for c in PSC_6GHZ if c in self.channels] or every
                self.plan, self.at = (every if self.sweeps % 4 == 0 else psc), 0
            channel = self.plan[self.at]
            self.at += 1
            if self.tune(5950 + 5 * channel):
                self.channel = channel
                self.listen(NODE_DWELL)
        now = self.clock()
        records = []
        for bssid, (channel, rssi, code, ssid) in self.pending.items():
            records.append((bssid, ap6_record(bssid, channel, rssi, code, ssid)))
            self.last_sent[bssid] = (now, rssi)
        self.pending.clear()
        if len(self.last_sent) > 4096:
            self.last_sent = {k: v for k, v in self.last_sent.items() if now - v[0] < RESEND_SECONDS}
        self.seq, packed = wardrive_batches(self.seq, records)
        self.outbox.extend(packed)
        del self.outbox[:-200]

    def status(self, final=False):
        return {"state": self.state, "biscuit": ":".join("%02X" % b for b in self.core) if self.core else None,
                "ap6": self.ap6, "role": self.role, "heard": len(self.heard), "sent": len(self.sent_ids),
                "updates": self.sent, "queued": sum(len(ids) for _, ids in self.outbox), "acks": self.acks,
                "tx_errors": self.tx_errors, "channel": self.channel, "sweeps": self.sweeps,
                "heard_ago": round(self.clock() - self.last_heard, 1) if self.last_heard is not None else None,
                "tune_failures": self.tune_failures, "final": final}

    def run(self):
        try:
            while not self.stopped.is_set():
                if self.state == "scanning":
                    self.scan()
                    self.home(NODE_HOME)
                else:
                    self.home(0.25)
                if self.clock() >= self.next["status"]:
                    self.report("status", self.status())
                    self.next["status"] = self.clock() + STATUS_GAP
            self.report("status", self.status(final=True))
        except Exception as exc:
            self.report("error", {"error": ("%s: %s" % (type(exc).__name__, exc))[:120]})
        finally:
            self.radio.close()

    def stop(self):
        self.stopped.set()


def check_port(value):
    if not isinstance(value, str) or not _PORT.fullmatch(value):
        raise Refused("bad USB port")
    return value


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
        self.resets = {}
        self.clock = time.monotonic
        self.lock = threading.RLock()
        self.last_client = time.monotonic()

    def resetting(self):
        """Whether a USB reset is recent enough to count against its port's limit: the helper stays up until none
        is, so the limit holds even with no client."""
        now = self.clock()
        with self.lock:
            return any(now - at < RESET_WINDOW for done in self.resets.values() for at in done)

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
            if not threads and time.monotonic() - self.last_client > IDLE_EXIT and not self.resetting():
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
