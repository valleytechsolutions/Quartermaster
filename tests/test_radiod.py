import importlib.util
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import threading
import time

import pytest

from quartermaster import deepscan, monitor, radio_helper

HERE = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("radiod", HERE / "packaging" / "radiod.py")
radiod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(radiod)

FREQS = (2412, 2417, 2422, 2427, 2432, 2437, 2442, 2447, 2452, 2457, 2462, 5180, 5200, 5745, 5955, 6115)
PHY_INFO = ("Wiphy {phy}\n\tSupported interface modes:\n\t\t * managed\n{monitor}\t\t * AP\n"
            "\tBand 1:\n\t\tFrequencies:\n" +
            "".join("\t\t\t* %d.0 MHz [%d] (%s dBm)\n" % (f, (f - 2407) // 5 if f < 3000 else (f - 5000) // 5,
                                                          "20.0" if f < 3000 else "23.0") for f in FREQS) +
            "\t\t\t* 2467.0 MHz [12] (disabled)\n")


class Radios:
    """A /sys/class/net and an iw and ip that act on it. Every call is kept, as the list of words it was."""

    def __init__(self, root):
        self.root = Path(root)
        self.net = self.root / "net"
        self.net.mkdir()
        self.calls = []
        self.links = {}
        self.txpower = {}
        self.power_save = {}
        self.fail = set()
        self.slow = {}
        self.scan_text = "BSS 00:11:22:33:44:55(on wlan2)\n\tfreq: 2437\n\tsignal: -50.00 dBm\n\tSSID: Harbor\n"
        self.phys = {}
        self.add("wlan0", "phy0", "brcmfmac", usb=False, mac="b8:27:eb:00:00:01", monitor=False)
        self.add("wlan1", "phy1", "mt7921u", usb=True, mac="00:c0:ca:00:00:01")
        self.add("wlan2", "phy2", "mt7921u", usb=True, mac="00:c0:ca:00:00:02")
        self.route = self.root / "route"
        self.route.write_text("Iface\tDestination\tGateway\nwlan0\t00000000\t0101A8C0\n")

    def add(self, name, phy, driver, usb=True, mac="00:00:00:00:00:00", monitor=True):
        device = self.root / "devices" / ("usb1/1-1/1-1.%s" % phy if usb else "platform/%s" % phy)
        (device / "net").mkdir(parents=True, exist_ok=True)
        if usb:
            (device.parent / "idVendor").write_text("0e8d\n")
        drivers = self.root / "drivers" / driver
        drivers.mkdir(parents=True, exist_ok=True)
        if not (device / "driver").exists():
            (device / "driver").symlink_to(drivers)
        self.phys.setdefault(phy, {"device": device, "mac": mac, "monitor": monitor})
        (self.root / "ieee80211" / phy).mkdir(parents=True, exist_ok=True)
        self._make(name, phy, "managed")

    def _make(self, name, phy, kind):
        base = self.net / name
        (base / "phy80211").mkdir(parents=True)
        (base / "phy80211" / "name").write_text(phy + "\n")
        (base / "device").symlink_to(self.phys[phy]["device"])
        (base / "address").write_text(self.phys[phy]["mac"] + "\n")
        (base / "type").write_text("803\n" if kind == "monitor" else "1\n")
        self.links[name] = kind

    def system(self):
        return Recording(str(self.net), str(self.route), self.run, str(self.root / "ieee80211"))

    def run(self, args, **kwargs):
        assert isinstance(args, list) and all(isinstance(a, str) for a in args)
        assert not kwargs.get("shell")
        words = [Path(args[0]).name] + args[1:]
        self.calls.append(words)
        out, code = "", 0
        if tuple(words[:2]) in self.fail or tuple(words[:4]) in self.fail:
            return subprocess.CompletedProcess(args, 1, "", "command failed (-22)")
        if words[:3] == ["iw", "reg", "get"]:
            out = "global\ncountry US: DFS-FCC\n"
        elif words[:2] == ["iw", "phy"] and words[3] == "info":
            info = self.phys[words[2]]
            out = PHY_INFO.format(phy=words[2], monitor="\t\t * monitor\n" if info["monitor"] else "")
        elif words[:2] == ["iw", "phy"] and words[3:5] == ["interface", "add"]:
            self._make(words[5], words[2], words[7])
        elif words[:2] == ["iw", "dev"]:
            name, verb = words[2], words[3:]
            if not (self.net / name).exists():
                return subprocess.CompletedProcess(args, 1, "", "No such device (-19)")
            if verb == ["link"]:
                out = "Connected to 11:22:33:44:55:66 (on %s)\n" % name if name == "wlan0" else "Not connected.\n"
            elif verb == ["info"]:
                out = "Interface %s\n\tifindex %d\n\taddr %s\n\ttype %s\n\tchannel 6 (2437 MHz), width: 20 MHz\n" \
                      "\ttxpower %.2f dBm\n" % (name, 7 + len(name), self.phys[self.phy_of(name)]["mac"],
                                                self.links.get(name, "managed"), self.txpower.get(name, 3.0))
            elif verb == ["get", "power_save"]:
                out = "Power save: %s\n" % self.power_save.get(name, "on")
            elif verb[:2] == ["set", "power_save"]:
                self.power_save[name] = verb[2]
            elif verb[:3] == ["set", "txpower", "limit"]:
                self.txpower[name] = int(verb[3]) / 100.0
            elif verb[:2] == ["set", "txpower"]:
                self.txpower[name] = 3.0
            elif verb == ["del"]:
                shutil.rmtree(self.net / name)
                self.links.pop(name, None)
            elif verb[:1] == ["scan"]:
                time.sleep(self.slow.get(name, 0))
                out = self.scan_text
        return subprocess.CompletedProcess(args, code, out, "")

    def phy_of(self, name):
        return (self.net / name / "phy80211" / "name").read_text().strip()


class Recording(radiod.System):
    def __init__(self, *args):
        super().__init__(*args)
        self.hops = []
        self.frames = []

    def set_freq(self, vif, ifindex, freq):
        self.hops.append((vif, freq))
        return True

    def capture(self, vif, mgmt_only):
        return FakeCapture(self.frames, mgmt_only)


class FakeCapture:
    def __init__(self, frames, mgmt_only):
        self.frames, self.mgmt_only = list(frames), mgmt_only
        self.read, self.write = socket.socketpair()
        for _ in self.frames:
            self.write.send(b"x")
        self.closed = False

    def fileno(self):
        return self.read.fileno()

    def recv_into(self, buffer):
        self.read.recv(1)
        frame = self.frames.pop(0)
        buffer[:len(frame)] = frame
        return len(frame)

    def stats(self):
        return 5, 1

    def close(self):
        self.closed = True
        self.read.close()
        self.write.close()


@pytest.fixture
def radios(tmp_path):
    return Radios(tmp_path)


def session(radios, sent=None):
    helper = radiod.Helper(radios.system())
    sent = [] if sent is None else sent
    return helper, helper.open_session(sent.append)


def ask(sess, **request):
    return sess.handle(json.dumps(request).encode())


def test_unknown_commands_the_regulatory_domain_and_odd_messages_are_refused(radios):
    _, sess = session(radios)
    for request in ({"cmd": "reg"}, {"cmd": "reg", "set": "BO"}, {"cmd": "set_reg"}, {"cmd": "shell"},
                    {"cmd": "connect", "iface": "wlan1"}, {"cmd": "ap"}, {"cmd": "send"}, {"cmd": "inject"},
                    {"cmd": "hello", "extra": 1}, {"cmd": "scan"}, {"nothing": True}, ["hello"]):
        answer = sess.handle(json.dumps(request).encode())
        assert answer["ok"] is False, request
    assert sess.handle(b"not json")["ok"] is False
    assert sess.handle(b"\xff\xfe")["ok"] is False
    big = json.dumps({"cmd": "scan", "iface": "wlan1", "freqs": [2412] * 900}).encode()
    assert len(big) > radiod.MAX_MESSAGE and sess.handle(big) == {"ok": False, "error": "message too long"}
    assert "reg" not in radiod.COMMANDS and not any("reg" in c for c in radiod.COMMANDS)
    assert not any(call[:3] == ["iw", "reg", "set"] for call in radios.calls)
    hello = ask(sess, cmd="hello")
    assert hello["ok"] and hello["regdom"] == "US" and sorted(hello["caps"]) == sorted(radiod.COMMANDS)


def test_scans_only_on_usb_adapters_and_only_on_frequencies_the_radio_has(radios):
    _, sess = session(radios)
    assert ask(sess, cmd="scan", iface="wlan0")["error"] == "not a USB adapter"
    assert ask(sess, cmd="scan", iface="wlan9")["ok"] is False
    assert ask(sess, cmd="scan", iface="qmmon0")["ok"] is False
    for bad in ("wlan1;reboot", "-wlan1", "wlan 1", "", "x" * 16, 7, None, "../wlan1"):
        assert ask(sess, cmd="scan", iface=bad)["ok"] is False, bad
    assert ask(sess, cmd="scan", iface="wlan1", freqs=[2467])["ok"] is False
    assert ask(sess, cmd="scan", iface="wlan1", freqs=[2413])["ok"] is False
    assert ask(sess, cmd="scan", iface="wlan1", freqs=["2412"])["ok"] is False
    assert ask(sess, cmd="scan", iface="wlan1", freqs=[True])["ok"] is False
    answer = ask(sess, cmd="scan", iface="wlan1", freqs=[2412, 5180])
    assert answer["ok"] and "Harbor" in answer["text"]
    assert radios.calls[-1] == ["iw", "dev", "wlan1", "scan", "freq", "2412", "5180"]
    assert ask(sess, cmd="scan", iface="wlan1")["ok"]
    assert radios.calls[-1] == ["iw", "dev", "wlan1", "scan"]


def test_monitor_mode_only_on_a_free_usb_adapter_with_an_allowed_driver(radios):
    _, sess = session(radios)
    assert ask(sess, cmd="monitor_start", iface="wlan0")["error"] == "not a USB adapter"
    radios.add("wlan3", "phy3", "rtl88xxau", mac="00:c0:ca:00:00:03")
    assert "driver" in ask(sess, cmd="monitor_start", iface="wlan3")["error"]
    radios.add("wlan4", "phy4", "mt76x2u", mac="00:c0:ca:00:00:04", monitor=False)
    assert ask(sess, cmd="monitor_start", iface="wlan4")["error"] == "no monitor mode"
    radios.route.write_text("Iface\tDestination\tGateway\nwlan1\t00000000\t0101A8C0\n")
    assert ask(sess, cmd="monitor_start", iface="wlan1")["error"] == "connected to a network"
    assert (radios.net / "wlan1").exists()
    before = len(radios.calls)
    answer = ask(sess, cmd="monitor_start", iface="wlan2")
    assert answer == {"ok": True, "vif": "qmmon0"}
    changes = [c for c in radios.calls[before:] if c[-1] not in ("info", "link") and "get" not in c]
    assert changes == [["iw", "dev", "wlan2", "del"], ["iw", "phy", "phy2", "interface", "add", "qmmon0", "type",
                                                       "monitor"], ["ip", "link", "set", "qmmon0", "up"]]
    assert radios.links == {"wlan0": "managed", "wlan1": "managed", "wlan3": "managed", "wlan4": "managed",
                            "qmmon0": "monitor"}


def test_a_connected_adapter_is_never_put_in_monitor_mode(radios):
    _, sess = session(radios)
    radios.add("wlan5", "phy5", "mt7921u", mac="00:c0:ca:00:00:05")
    original = radios.run

    def connected(args, **kwargs):
        if args[1:] == ["dev", "wlan5", "link"]:
            return subprocess.CompletedProcess(args, 0, "Connected to 11:22:33:44:55:66 (on wlan5)\n", "")
        return original(args, **kwargs)
    sess.system.runner = connected
    assert ask(sess, cmd="monitor_start", iface="wlan5")["error"] == "connected to a network"
    assert (radios.net / "wlan5").exists()


def test_a_failed_monitor_start_puts_the_adapter_back(radios):
    _, sess = session(radios)
    radios.fail.add(("ip", "link"))
    answer = ask(sess, cmd="monitor_start", iface="wlan2")
    assert answer["ok"] is False and "monitor mode failed" in answer["error"]
    assert radios.links.get("wlan2") == "managed" and "qmmon0" not in radios.links
    assert sess.monitors == {}


def test_hop_plans_are_checked_step_by_step(radios):
    _, sess = session(radios)
    vif = ask(sess, cmd="monitor_start", iface="wlan2")["vif"]
    for plan in ([[2412, 20, 49]], [[2412, 20, 59]], [[2412, 20, 1001]], [[2412, 40, 250]], [[2467, 20, 250]],
                 [[2413, 20, 250]], [[2412, 20]], [[2412, 20, 250, 1]], [[2412, True, 250]], [[2412.0, 20, 250]],
                 [[2412, 20, 250]] * 65, [], "2412", None):
        assert ask(sess, cmd="hop_plan", vif=vif, plan=plan)["ok"] is False, plan
    assert ask(sess, cmd="hop_plan", vif="qmmon1", plan=[[2412, 20, 250]])["ok"] is False
    assert ask(sess, cmd="hop_plan", vif="wlan1", plan=[[2412, 20, 250]])["ok"] is False
    answer = ask(sess, cmd="hop_plan", vif=vif, plan=[[2462, 20, 60], [2437, 20, 60], [5955, 20, 60]])
    assert answer == {"ok": True, "steps": 3, "cycle_ms": None}
    deadline = time.monotonic() + 3
    while len(sess.system.hops) < 3 and time.monotonic() < deadline:
        time.sleep(0.01)
    assert sess.system.hops[:3] == [(vif, 2462), (vif, 2437), (vif, 5955)]
    ask(sess, cmd="monitor_stop", vif=vif)


def test_another_connection_cannot_touch_a_monitor_it_did_not_start(radios):
    helper, sess = session(radios)
    other = helper.open_session(lambda data: None)
    vif = ask(sess, cmd="monitor_start", iface="wlan2")["vif"]
    assert ask(other, cmd="monitor_stop", vif=vif)["error"] == "not this connection's monitor"
    assert ask(other, cmd="capture", vif=vif)["ok"] is False
    assert helper.open_session(lambda data: None) is None
    assert ask(other, cmd="monitor_start", iface="wlan1")["ok"]
    for n in range(3, 8):
        radios.add("wlan%d" % n, "phy%d" % n, "mt7921u", mac="00:c0:ca:00:00:0%d" % n)
    for n in range(3, 3 + radiod.MAX_MONITORS - 2):
        assert ask(other if n % 2 else sess, cmd="monitor_start", iface="wlan%d" % n)["ok"]
    assert helper.monitor_count() == radiod.MAX_MONITORS == 6
    assert ask(other, cmd="monitor_start", iface="wlan7")["error"] == "too many monitors"
    helper.restore_all()
    assert radios.links == dict({"wlan0": "managed"}, **{"wlan%d" % n: "managed" for n in range(1, 8)})


def test_tuning_turns_power_save_off_and_power_up_to_the_regulatory_limit(radios):
    _, sess = session(radios)
    assert ask(sess, cmd="tune", iface="wlan0")["error"] == "not a USB adapter"
    radios.power_save["wlan1"] = "off"
    answer = ask(sess, cmd="tune", iface="wlan1")
    assert answer["ok"] and answer["limit"] == 20.0 and answer["txpower"] == 20.0
    assert ["iw", "dev", "wlan1", "set", "power_save", "off"] in radios.calls
    assert ["iw", "dev", "wlan1", "set", "txpower", "limit", "2000"] in radios.calls
    assert ask(sess, cmd="tune", iface="wlan2")["ok"]
    assert radios.power_save["wlan2"] == "off"
    assert ask(sess, cmd="untune", iface="wlan2") == {"ok": True}
    assert radios.power_save["wlan2"] == "on" and radios.txpower["wlan2"] == 3.0
    assert not any(call[:2] == ["iw", "reg"] and call[2] != "get" for call in radios.calls)


def test_a_closed_connection_puts_everything_back_newest_first(radios):
    helper, sess = session(radios)
    ask(sess, cmd="tune", iface="wlan1")
    vif = ask(sess, cmd="monitor_start", iface="wlan2")["vif"]
    ask(sess, cmd="hop_plan", vif=vif, plan=[[2412, 20, 250]])
    before = len(radios.calls)
    helper.close_session(sess)
    changes = [c for c in radios.calls[before:] if c[-1] not in ("info", "link")]
    assert changes == [["ip", "link", "set", "qmmon0", "down"], ["iw", "dev", "qmmon0", "del"],
                       ["iw", "phy", "phy2", "interface", "add", "wlan2", "type", "managed"],
                       ["iw", "dev", "wlan1", "set", "txpower", "auto"],
                       ["iw", "dev", "wlan1", "set", "power_save", "on"]]
    assert radios.links["wlan2"] == "managed" and "qmmon0" not in radios.links
    assert helper.sessions == [] and sess.monitors == {} and sess.tuned == {}


def test_what_a_killed_helper_left_is_put_back_at_its_next_start(radios, tmp_path):
    state = str(tmp_path / "state.json")
    helper = radiod.Helper(radios.system(), state_path=state)
    sess = helper.open_session(lambda data: None)
    ask(sess, cmd="tune", iface="wlan1")
    ask(sess, cmd="monitor_start", iface="wlan2")
    saved = json.loads(Path(state).read_text())
    assert {e["kind"] for e in saved} == {"monitor", "tune"}
    again = radiod.Helper(radios.system(), state_path=state)
    again.recover()
    assert radios.links["wlan2"] == "managed" and "qmmon0" not in radios.links
    assert radios.calls[-1] == ["iw", "dev", "wlan1", "set", "power_save", "on"]
    assert json.loads(Path(state).read_text()) == []
    Path(state).write_text(json.dumps([{"kind": "monitor", "vif": "qmmon0;x", "name": "wlan2", "phy": "phy2"},
                                       {"kind": "tune", "iface": "wlan1", "power_save": "maybe"}, "junk"]))
    before = len(radios.calls)
    radiod.Helper(radios.system(), state_path=state).recover()
    assert radios.calls[before:] == []


def test_only_fixed_tools_with_checked_words_and_never_a_shell(radios):
    system = radios.system()
    for word in ("a;b", "a|b", "$(x)", "`x`", "a b", "-o", "", "a\nb", "a&b"):
        with pytest.raises(radiod.Refused):
            system.run("iw", "dev", word, "info")
    with pytest.raises(KeyError):
        system.run("sh", "-c", "true")
    source = (HERE / "packaging" / "radiod.py").read_text()
    for banned in ("shell=True", "os.system", "os.popen", "Popen(", "setuid", "sudo", ".sendto(", "sendmsg"):
        assert banned not in source
    capture = radiod.Capture.__dict__
    assert not any(name.startswith("send") for name in capture)


def test_only_members_of_the_radio_group_may_connect(monkeypatch):
    class Group:
        gr_gid = 990
    monkeypatch.setattr(radiod.grp, "getgrnam", lambda name: Group)
    assert radiod.peer_allowed(1000, 1000, members=[1000, 990])
    assert not radiod.peer_allowed(1000, 1000, members=[1000, 27])
    assert not radiod.peer_allowed(0, 0, members=[990])
    assert not radiod.peer_allowed(999, 999, members=[990])
    assert not radiod.peer_allowed(65534, 65534, members=[990])

    def missing(name):
        raise KeyError(name)
    monkeypatch.setattr(radiod.grp, "getgrnam", missing)
    assert not radiod.peer_allowed(1000, 1000, members=[990])


def test_a_wrong_peer_is_turned_away(radios, monkeypatch):
    monkeypatch.setattr(radiod, "peer_allowed", lambda uid, gid: False)
    helper = radiod.Helper(radios.system())
    ours, theirs = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
    helper.serve_connection(theirs)
    answer = json.loads(ours.recv(4096)[1:])
    assert answer["ok"] is False and "group" in answer["error"]
    assert helper.sessions == []
    ours.close()


def serve(radios, monkeypatch):
    monkeypatch.setattr(radiod, "peer_allowed", lambda uid, gid: True)
    helper = radiod.Helper(radios.system())
    ours, theirs = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
    thread = threading.Thread(target=helper.serve_connection, args=(theirs,), daemon=True)
    thread.start()
    client = radio_helper.Client("unused", connect=lambda path: ours).open()
    return helper, client, thread


def test_the_app_and_the_helper_talk_over_one_socket_and_closing_it_restores(radios, monkeypatch):
    helper, client, thread = serve(radios, monkeypatch)
    try:
        assert client.request("hello")["version"] == radiod.VERSION
        radios.scan_text = "".join("BSS 00:11:22:33:%02x:%02x(on wlan1)\n\tfreq: 2437\n\tsignal: -50.00 dBm\n"
                                   "\tSSID: %s\n" % (i >> 8, i & 255, "N%05d" % i + "x" * 40) for i in range(2500))
        text = client.request("scan", iface="wlan1")["text"]
        assert text == radios.scan_text and len(text) > 3 * radiod.CHUNK
        with pytest.raises(radio_helper.HelperError):
            client.request("reg")
        adapters = {a["interface"]: a for a in client.request("adapters")["adapters"]}
        assert adapters["wlan0"]["usb"] is False and adapters["wlan0"]["connected"] is True
        assert adapters["wlan0"]["default_route"] is True and adapters["wlan1"]["monitor"] is True
        assert 6115 in adapters["wlan1"]["freqs"] and 2467 not in adapters["wlan1"]["freqs"]
        vif = client.request("monitor_start", iface="wlan2")["vif"]
        got = []
        client.frames = got.append
        helper.sessions[0].system.frames = [b"\x00" * 30, b"\x01" * 40]
        client.request("capture", vif=vif)
        deadline = time.monotonic() + 3
        while not got and time.monotonic() < deadline:
            time.sleep(0.01)
        received, dropped, freq, switched, frames = radio_helper.split_batch(got[0])
        assert (received, dropped) == (5, 1) and [bytes(f) for f in frames] == [b"\x00" * 30, b"\x01" * 40]
    finally:
        client.close()
    thread.join(timeout=5)
    assert not thread.is_alive()
    assert radios.links["wlan2"] == "managed" and "qmmon0" not in radios.links and helper.sessions == []


def test_the_app_reads_the_helper_state(tmp_path, monkeypatch):
    assert radio_helper.state({}) == ("off", radio_helper.WORDS["off"])
    assert radio_helper.state({"deep_scan": True}, path=str(tmp_path / "none.sock"))[0] == "missing"

    def refused(path):
        raise PermissionError(13, "Permission denied")
    client = lambda path: radio_helper.Client(path, connect=refused)
    assert radio_helper.state({"deep_scan": True}, client=client)[0] == "no_group"
    assert "Restart" in radio_helper.state({"radio_tuning": True}, client=client)[1]

    def broken(path):
        raise OSError(5, "Input/output error")
    found = radio_helper.state({"direct_scan": True}, client=lambda p: radio_helper.Client(p, connect=broken))
    assert found[0] == "error" and found[1].startswith("Radio helper failed")


def test_the_nl80211_numbers_match_the_kernel_header(tmp_path):
    header = Path("/usr/include/linux/nl80211.h")
    compiler = shutil.which("cc") or shutil.which("gcc")
    if not header.exists() or not compiler:
        pytest.skip("no kernel header or compiler here")
    source = tmp_path / "nl.c"
    source.write_text("#include <stdio.h>\n#include <linux/nl80211.h>\n#include <linux/genetlink.h>\n"
                      "int main(void){printf(\"%d %d %d %d %d %d %d %d %d\\n\", NL80211_CMD_SET_WIPHY,"
                      "NL80211_ATTR_IFINDEX, NL80211_ATTR_WIPHY_FREQ, NL80211_ATTR_CHANNEL_WIDTH,"
                      "NL80211_ATTR_CENTER_FREQ1, NL80211_CHAN_WIDTH_20_NOHT, CTRL_CMD_GETFAMILY,"
                      "CTRL_ATTR_FAMILY_ID, CTRL_ATTR_FAMILY_NAME); return 0;}\n")
    subprocess.run([compiler, str(source), "-o", str(tmp_path / "nl")], check=True, capture_output=True)
    numbers = subprocess.run([str(tmp_path / "nl")], check=True, capture_output=True, text=True).stdout.split()
    assert [int(n) for n in numbers] == [radiod.NL80211_CMD_SET_WIPHY, radiod.NL80211_ATTR_IFINDEX,
                                         radiod.NL80211_ATTR_WIPHY_FREQ, radiod.NL80211_ATTR_CHANNEL_WIDTH,
                                         radiod.NL80211_ATTR_CENTER_FREQ1, radiod.NL80211_CHAN_WIDTH_20_NOHT,
                                         radiod.CTRL_CMD_GETFAMILY, radiod.CTRL_ATTR_FAMILY_ID,
                                         radiod.CTRL_ATTR_FAMILY_NAME]


def test_the_app_and_the_helper_agree_on_drivers_and_flock_prefixes():
    assert deepscan.DRIVERS == radiod.DRIVERS
    assert frozenset(radiod.FLOCK_OUIS) == monitor.FLOCK_OUIS
    assert not monitor.FLOCK_OUIS & monitor.ESPRESSIF_LEFT_OUT
    assert radio_helper.HEADER.format == radiod.Relay.HEADER.format


def test_the_helper_parses_phy_and_interface_info():
    phy = radiod.parse_phy(PHY_INFO.format(phy="phy1", monitor="\t\t * monitor\n"))
    assert phy["monitor"] is True and 2467 not in phy["freqs"] and phy["freqs"][2412]["dbm"] == 20.0
    assert radiod.parse_phy("Wiphy phy0\n")["monitor"] is False
    dev = radiod.parse_dev("Interface wlan1\n\tifindex 9\n\taddr 00:c0:ca:aa:bb:cc\n\ttype managed\n"
                           "\tchannel 36 (5180 MHz), width: 20 MHz\n\ttxpower 3.00 dBm\n")
    assert dev == {"type": "managed", "freq": 5180, "txpower": 3.0, "ifindex": 9, "addr": "00:c0:ca:aa:bb:cc"}
    assert os.path.basename(radiod._tool("iw")) == "iw"


def test_a_new_hop_plan_waits_for_the_running_cycle_and_one_hopper_hops(radios):
    _, sess = session(radios)
    vif = ask(sess, cmd="monitor_start", iface="wlan2")["vif"]
    ask(sess, cmd="hop_plan", vif=vif, plan=[[2462, 20, 60], [2437, 20, 60], [5955, 20, 60]])
    again = ask(sess, cmd="hop_plan", vif=vif, plan=[[2412, 20, 60]])
    assert again["ok"] and again["steps"] == 1
    deadline = time.monotonic() + 3
    while len(sess.system.hops) < 5 and time.monotonic() < deadline:
        time.sleep(0.01)
    assert [f for _, f in sess.system.hops[:5]] == [2462, 2437, 5955, 2412, 2412]
    assert [t.name for t in threading.enumerate()].count("hop-" + vif) == 1
    assert ask(sess, cmd="hop_plan", vif=vif, plan=[[2412, 20, 60]])["cycle_ms"] >= 60
    ask(sess, cmd="monitor_stop", vif=vif)


def test_channel_changes_from_two_hoppers_never_share_the_netlink_socket_at_once(radios):
    system = radios.system()
    inside, most = [0], [0]

    class Netlink:
        def set_freq(self, ifindex, freq):
            inside[0] += 1
            most[0] = max(most[0], inside[0])
            time.sleep(0.005)
            inside[0] -= 1

    system._nl = Netlink()
    threads = [threading.Thread(target=lambda: [radiod.System.set_freq(system, "qmmon0", 7, 2412)
                                                for _ in range(20)]) for _ in range(3)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert most[0] == 1


def test_requests_carry_ids_and_odd_commands_are_refused_not_fatal(radios):
    _, sess = session(radios)
    assert ask(sess, cmd="hello", id=7)["id"] == 7
    assert ask(sess, cmd=["hello"]) == {"ok": False, "error": "unknown command"}
    assert ask(sess, cmd={"a": 1}, id=3) == {"ok": False, "error": "unknown command", "id": 3}
    for rid in ("7", True, 0, -1, 2 ** 31, 1.5):
        answer = ask(sess, cmd="hello", id=rid)
        assert answer["ok"] is False and "id" not in answer, rid


def test_two_connections_never_get_the_same_monitor_name(radios):
    helper, first = session(radios)
    second = helper.open_session(lambda data: None)
    first.monitors["qmmon0"] = {"phy": "phy1", "name": "wlan1", "mac": "", "type": "managed", "vif": "qmmon0"}
    assert ask(second, cmd="monitor_start", iface="wlan2")["vif"] == "qmmon1"
    first.monitors.clear()
    radios.links.pop("wlan1")
    shutil.rmtree(radios.net / "wlan1")
    radios._make("qmmon0", "phy1", "monitor")
    radiod.undo_monitor(radios.system(), {"vif": "qmmon0", "name": "wlan2", "phy": "phy2", "mac": ""})
    assert radios.links["qmmon0"] == "monitor"


def test_a_name_taken_by_another_adapter_is_left_alone_at_restore(radios):
    _, sess = session(radios)
    vif = ask(sess, cmd="monitor_start", iface="wlan2")["vif"]
    radios.add("wlan2", "phy7", "mt7612u", mac="00:c0:ca:00:00:07")
    before = len(radios.calls)
    assert ask(sess, cmd="monitor_stop", vif=vif)["ok"]
    changes = [c for c in radios.calls[before:] if c[-1] not in ("info", "link")]
    assert changes == [["ip", "link", "set", vif, "down"], ["iw", "dev", vif, "del"],
                       ["iw", "phy", "phy2", "interface", "add", "wlan3", "type", "managed"]]
    assert radios.phy_of("wlan2") == "phy7" and radios.phy_of("wlan3") == "phy2"
    gone = ask(sess, cmd="monitor_start", iface="wlan3")["vif"]
    (radios.root / "ieee80211" / "phy2").rmdir()
    before = len(radios.calls)
    ask(sess, cmd="monitor_stop", vif=gone)
    assert not any(c[:2] == ["iw", "phy"] or c[:3] == ["ip", "link", "set"] and "address" in c
                   for c in radios.calls[before:])


def test_the_capture_socket_hears_nothing_before_it_is_bound_to_the_monitor(monkeypatch):
    steps = []

    class Sock:
        def __init__(self, family, kind, proto):
            steps.append(("socket", proto))

        def setsockopt(self, level, name, value):
            steps.append(("opt", name))

        def bind(self, address):
            steps.append(("bind", address))

        def close(self):
            pass

    monkeypatch.setattr(radiod.socket, "socket", Sock)
    radiod.Capture("qmmon0", radiod.bpf_program())
    assert steps[0] == ("socket", 0) and steps[-1] == ("bind", ("qmmon0", socket.htons(radiod.ETH_P_ALL)))
    assert steps.index(("opt", radiod.SO_LOCK_FILTER)) < len(steps) - 1


def test_a_long_scan_never_holds_up_other_requests(radios, monkeypatch):
    helper, client, thread = serve(radios, monkeypatch)
    try:
        radios.slow = {"wlan1": 1.0, "wlan2": 1.0}
        results = []
        scans = [threading.Thread(target=lambda name=name: results.append(client.request("scan", iface=name)))
                 for name in ("wlan1", "wlan2")]
        started = time.monotonic()
        for scan in scans:
            scan.start()
        time.sleep(0.1)
        assert client.request("hello")["version"] == radiod.VERSION
        assert time.monotonic() - started < 0.8
        for scan in scans:
            scan.join(timeout=5)
        assert len(results) == 2 and time.monotonic() - started < 1.8
        radios.slow = {"wlan1": 0.5}
        first = threading.Thread(target=lambda: results.append(client.request("scan", iface="wlan1")))
        first.start()
        time.sleep(0.1)
        with pytest.raises(radio_helper.HelperError, match="busy"):
            client.request("scan", iface="wlan1")
        first.join(timeout=5)
    finally:
        client.close()
    thread.join(timeout=5)
