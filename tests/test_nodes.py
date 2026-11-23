import csv
import sys
import threading
import types

import pytest

from quartermaster import config, gnss, hardware, nodes, planner, recon
from quartermaster.engine import Wardrive
from quartermaster.gnss import GPS
from quartermaster.nodes import NodeManager
from quartermaster.recon_engine import Recon
from quartermaster.wifi import parse_iw_scan
from tests.fakes import GGA, Clock, FakeAir, FakeBus, FakeHub, FakeNode, NoBle, QuietFleet, Talker, usb_serial
from tests.test_core import FIX, NOW, SCAN
from tests.test_engine import ADAPTERS, FakeGPS
from tests.test_recon import Sound

NATIVE = "usb-Espressif_USB_JTAG_serial_debug_unit_A0:B1:C2:D3:E4:F5-if00"
BRIDGE = "usb-1a86_USB_Single_Serial_5A7B012345-if00"
GPS_LINK = "usb-Silicon_Labs_CP2102N_USB_to_UART_Bridge_Controller_0001-if00-port0"
ONE, TWO, THREE = "a0b1c2d3e4f5", "a0b1c2d3e4f6", "a0b1c2d3e4f7"


@pytest.fixture(autouse=True)
def no_port_left_held():
    yield
    hardware._held.clear()
    hardware._marks.clear()


class Rig:
    """USB serial ports with devices behind them, and a NodeManager without threads looking at them."""

    def __init__(self, tmp_path, ports, cfg=None, on_change=None, bridge_wait=0):
        self.clock = Clock()
        self.bus = FakeBus(self.clock)
        self.sys_tty, self.dev, self.by_id = usb_serial(tmp_path, {name: port[:2] for name, port in ports.items()})
        self.devices = {}
        for name, (_, _, device) in ports.items():
            if device is not None:
                device.clock = self.clock
                if isinstance(device, FakeNode):
                    device.last_host = device.last_status = device.last_hello = self.clock()
                self.devices[name] = self.bus.add(self.dev / name, device)
        self.manager = NodeManager(cfg or {}, self.bus.factory, self.sys_tty, self.dev, self.by_id, clock=self.clock,
                                   threads=False, on_change=on_change, bridge_wait=bridge_wait)

    def settle(self, seconds=1.0):
        end = self.clock() + seconds
        while self.clock() < end:
            if self.manager.links:
                self.manager.pump()
            else:
                self.clock.advance(nodes.READ_SECONDS)

    def path(self, name):
        return str(self.dev / name)


def node_cfg(*ids, ble=False):
    return {"country": "US", "nodes": [{"id": node_id, "name": "N%d" % i, "ble": ble} for i, node_id in enumerate(ids)]}


def two_nodes(tmp_path, cfg=None, **kwargs):
    return Rig(tmp_path, {"ttyACM0": ("303a:1001", NATIVE, FakeNode(ONE)),
                          "ttyUSB1": ("1a86:55d4", BRIDGE, FakeNode(TWO, board="stamp-c5", caps="wifi24+wifi5"))},
               cfg if cfg is not None else node_cfg(ONE, TWO), **kwargs)


class Sweeps:
    """One sweep of the scan in test_core from the first adapter, then quiet."""

    def __init__(self, adapters, sink, interval):
        self.adapters, self.sink = adapters, sink

    def start(self):
        self.sink(parse_iw_scan(SCAN), self.adapters[0])

    def stop(self):
        pass

    def status(self):
        return [{"interface": a["interface"], "method": "iw", "sweeps": 1, "sweep_seconds": 3.0, "error": "",
                 "label": "", "bands": a["bands"]} for a in self.adapters]


def test_a_node_on_its_own_usb_port_and_one_behind_a_bridge_are_both_found(tmp_path):
    rig = two_nodes(tmp_path, cfg={})
    rig.manager.scan()
    found = {node["id"]: node for node in rig.manager.found()}
    assert set(found) == {ONE, TWO} and rig.manager.known() == []
    assert found[ONE]["port"] == str(rig.by_id / NATIVE) and found[TWO]["board"] == "stamp-c5"
    assert found[ONE]["connected"] and found[ONE]["firmware"] == "1.0.0" and found[ONE]["flash_mb"] == 8
    assert found[TWO]["capabilities"] == ["wifi24", "wifi5"] and found[ONE]["chip"] == "1.0"
    for port in rig.bus.ports:
        assert port.baudrate == 921600 and port.exclusive
        opened = port.events.index(("open", 921600))
        assert port.events[opened - 1:] == [(True, False), ("open", 921600), (True, False), (False, False)]
    assert [device.resets for device in rig.devices.values()] == [0, 0]
    rig.clock.advance(60)
    rig.manager.scan()
    assert len(rig.bus.ports) == 2
    assert hardware.port_holder(rig.path("ttyACM0")) == "node" and hardware.port_mark(rig.path("ttyUSB1")) == "node"


def test_a_gps_port_is_never_asked_and_a_node_port_is_never_a_gps_port(tmp_path, monkeypatch):
    gps = Talker(GGA)
    rig = Rig(tmp_path, {"ttyUSB0": ("10c4:ea60", GPS_LINK, gps), "ttyACM0": ("303a:1001", NATIVE, FakeNode(ONE)),
                         "ttyUSB1": ("1a86:55d4", BRIDGE, FakeNode(TWO))})
    assert hardware.hold_port(rig.path("ttyUSB0"), "gps")
    rig.manager.scan()
    assert gps.heard == b"" and {node["id"] for node in rig.manager.found()} == {ONE, TWO}
    hardware.let_go_port(rig.path("ttyUSB0"), "gps")
    hardware.mark_port(rig.path("ttyUSB0"), "gps")
    rig.clock.advance(60)
    rig.manager.scan()
    assert gps.heard == b""
    assert [c["path"] for c in hardware.gps_candidates(rig.sys_tty, rig.dev)] == [rig.path("ttyUSB0")]
    assert not hardware.hold_port(str(rig.by_id / BRIDGE), "gps")

    opened = []

    class Port:
        def __init__(self, path, baud, **_):
            opened.append(path)
            raise OSError("busy")
    monkeypatch.setitem(sys.modules, "serial", types.SimpleNamespace(Serial=Port))
    GPS("auto", candidates=lambda: [{"path": rig.path("ttyUSB1")}, {"path": str(rig.by_id / NATIVE)},
                                    {"path": rig.path("ttyUSB0")}])._serial(threading.Event())
    assert opened == [rig.path("ttyUSB0")]


def test_a_port_that_is_not_a_node_is_let_go_and_asked_again_only_a_few_times(tmp_path):
    other = Talker(b"Arduino says hi\r\n")
    rig = Rig(tmp_path, {"ttyUSB2": ("1a86:7523", "", other), "ttyUSB3": ("0403:6001", "", Talker())})
    for _ in range(100):
        rig.manager.scan()
        rig.clock.advance(nodes.SCAN_SECONDS)
    assert other.heard.count(b"#HELLO*") == 3 and rig.devices["ttyUSB3"].heard == b""
    assert rig.manager.found() == [] and not other.taken and hardware.port_holder(rig.path("ttyUSB2")) is None
    (rig.by_id.parent / "dev" / "ttyUSB2").unlink()
    (rig.sys_tty / "ttyUSB2" / "device").unlink()
    (rig.sys_tty / "ttyUSB2").rmdir()
    rig.manager.scan()
    assert rig.manager.tries == {}


def test_without_pyserial_nodes_are_unavailable_and_say_why(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "serial", None)
    rig = two_nodes(tmp_path)
    rig.manager.factory = None
    rig.manager.scan()
    assert not rig.manager.available and rig.manager.error == nodes.NO_SERIAL
    assert [node["error"] for node in rig.manager.known()] == [nodes.NOT_PLUGGED] * 2


def test_lines_with_a_wrong_checksum_or_lost_on_the_way_are_counted_and_left_out(tmp_path):
    rig = two_nodes(tmp_path)
    rig.manager.scan()
    heard = []
    rig.manager.attach(heard, lambda networks, adapter: heard.extend(networks))
    rig.settle()
    node = rig.devices["ttyACM0"]
    assert node.running and node.commands[-2:] == ["PLAN", "START"]
    node.garble = 1
    node.wifi("AA:BB:CC:00:00:01", b"Garbled")
    node.lose = 3
    for i in range(3):
        node.wifi("AA:BB:CC:00:00:1%d" % i, b"Lost")
    node.wifi("AA:BB:CC:00:00:02", b"Fine")
    rig.settle()
    status = rig.manager.node(ONE)
    assert [net["ssid"] for net in heard] == ["Fine"] and status["bad"] == 1 and status["lost"] == 4
    assert rig.manager.node(TWO)["lost"] == 0


def test_what_a_node_said_before_its_port_was_opened_is_left_out(tmp_path):
    rig = two_nodes(tmp_path)
    one = rig.devices["ttyACM0"]
    one.plan, one.running = {"version": 9, "ghz2": 0x7FFE, "ghz5": 0x1FFFFFFE, "ble": False}, True
    one.wifi("aa:bb:cc:00:00:01", b"Old")
    one.lose = 5
    one.wifi("aa:bb:cc:00:00:02", b"Older")
    one.lose = 0
    heard = []
    rig.manager.attach(heard, lambda networks, adapter: heard.extend(networks))
    rig.manager.scan()
    rig.settle()
    assert heard == [] and rig.manager.node(ONE)["lost"] == 0 and rig.manager.node(ONE)["bad"] == 0
    one.wifi("aa:bb:cc:00:00:03", b"New")
    rig.settle()
    assert [net["ssid"] for net in heard] == ["New"]


def test_an_unplugged_node_is_let_go_and_comes_back_when_plugged_in_again(tmp_path):
    rig = two_nodes(tmp_path)
    rig.manager.scan()
    node = rig.devices["ttyUSB1"]
    node.unplug()
    rig.settle()
    status = rig.manager.node(TWO)
    assert not status["connected"] and status["error"] == nodes.UNPLUGGED and status["board"] == "stamp-c5"
    assert hardware.port_holder(rig.path("ttyUSB1")) is None and hardware.port_mark(rig.path("ttyUSB1")) == "node"
    rig.clock.advance(2)
    rig.manager.scan()
    assert not rig.manager.node(TWO)["connected"]
    node.replug()
    rig.clock.advance(10)
    rig.manager.scan()
    assert rig.manager.node(TWO)["connected"] and rig.manager.node(TWO)["error"] == ""


def test_a_node_that_goes_quiet_is_let_go_after_35_seconds(tmp_path):
    rig = two_nodes(tmp_path)
    rig.manager.scan()
    rig.devices["ttyACM0"].lose = 10 ** 6
    rig.settle(34)
    assert rig.manager.node(ONE)["connected"]
    rig.settle(2)
    assert not rig.manager.node(ONE)["connected"] and rig.manager.node(ONE)["error"] == nodes.SILENT
    assert rig.manager.node(TWO)["connected"]


def test_hello_every_10_seconds_keeps_a_running_node_going_and_a_node_that_stopped_is_started_again(tmp_path):
    rig = two_nodes(tmp_path)
    rig.manager.scan()
    rig.manager.attach(rig, lambda networks, adapter: None)
    rig.settle(120)
    node = rig.devices["ttyACM0"]
    assert node.running and node.commands.count("HELLO") >= 11 and node.commands.count("START") == 1
    rig.clock.advance(31)
    node.tick()
    assert not node.running
    rig.settle(15)
    assert node.running and node.commands.count("START") == 2
    version = rig.manager.plans[ONE].version
    node.reset()
    before = len(node.commands)
    rig.settle(15)
    after = [name for name in node.commands[before:] if name != "HELLO"]
    assert node.running and node.plan["version"] == version and after == ["PLAN", "START"]
    rig.manager.detach(rig)
    rig.settle()
    assert not node.running and node.commands[-1] in ("STOP", "HELLO")


class Picky(FakeNode):
    def _plan(self, args):
        return "country"


def test_a_node_that_refuses_its_plan_says_so_and_is_not_asked_again_and_again(tmp_path):
    rig = Rig(tmp_path, {"ttyACM0": ("303a:1001", NATIVE, Picky(ONE))}, node_cfg(ONE))
    rig.manager.scan()
    rig.manager.attach(rig, lambda networks, adapter: None)
    rig.settle(30)
    assert rig.devices["ttyACM0"].commands.count("PLAN") == 1 and rig.manager.node(ONE)["error"] == nodes.REFUSED


def test_sightings_reach_the_wardrive_a_batch_at_a_time(tmp_path):
    rig = two_nodes(tmp_path)
    rig.manager.scan()
    batches = []
    rig.manager.attach(rig, lambda networks, adapter: batches.append((len(networks), adapter["label"])))
    rig.settle()
    one = rig.devices["ttyACM0"]
    for i in range(130):
        one.wifi("aa:bb:cc:00:%02x:%02x" % (i >> 8, i & 255), b"Busy street", channel=1 + i % 11)
    rig.manager.pump()
    assert batches == [(64, "Node N0"), (64, "Node N0")]
    rig.settle()
    assert batches[2:] == [(2, "Node N0")]


AUTH = {0: "[ESS]", 1: "[WEP][ESS]", 2: "[WPA-PSK-TKIP][ESS]", 3: "[WPA2-PSK-CCMP][ESS]",
        4: "[WPA-PSK-CCMP+TKIP][WPA2-PSK-CCMP+TKIP][ESS]", 5: "[WPA2-EAP-CCMP][ESS]", 6: "[RSN-SAE-CCMP][ESS]",
        7: "[WPA2-PSK-CCMP][RSN-SAE-CCMP][ESS]", 8: "[WEP][ESS]", 9: "[RSN-OWE-CCMP][ESS]",
        10: "[WPA2-EAP-GCMP-256][ESS]", 11: "[RSN-SAE-GCMP-256][ESS]", 12: "[RSN-SAE-CCMP][ESS]",
        13: "[RSN-DPP-CCMP][ESS]", 14: "[WPA2-EAP-CCMP][ESS]", 15: "[WPA2-EAP-CCMP][ESS]", 16: "[WPA-EAP-TKIP][ESS]",
        17: "[ESS]"}
CIPHER = {2: 3, 4: 5, 10: 9, 11: 9, 16: 3}
LABEL = {0: "OPEN", 1: "WEP", 2: "WPA", 3: "WPA2", 4: "WPA2", 5: "EAP", 6: "WPA3", 7: "WPA2/3", 8: "WEP", 9: "OWE",
         10: "EAP", 11: "WPA3", 12: "WPA3", 13: "OPEN", 14: "EAP", 15: "EAP", 16: "EAP", 17: "OPEN"}


def line(node):
    raw = bytes(node.out).split(b"\n")[-2]
    return nodes.parse(raw)


def test_every_esp_idf_auth_mode_reads_as_the_scan_of_an_adapter_reads_it():
    assert set(AUTH) == set(nodes.AUTH)
    node = FakeNode()
    node.plan, node.running = {"version": 1, "ghz2": 0x7FFE, "ghz5": 0x1FFFFFFE, "ble": True}, True
    for auth, caps in AUTH.items():
        node.wifi("a4:2b:b0:11:22:%02x" % auth, b"Net", auth=auth, pairwise=CIPHER.get(auth, 4))
        record = nodes.wifi_record(line(node))
        assert (record["capabilities"], record["security"]) == (caps, LABEL[auth]), auth
        assert record["bssid"] == "A4:2B:B0:11:22:%02X" % auth
    adapter = parse_iw_scan(SCAN)[0]
    node.wifi("aa:bb:cc:dd:ee:01", adapter["ssid"].encode(), rssi=-61, channel=6, auth=7, pairwise=4, flags=0x1A)
    record = nodes.wifi_record(line(node))
    for key in ("bssid", "ssid", "channel", "frequency", "band", "security", "capabilities", "hidden", "wps"):
        assert record[key] == adapter[key], key
    node.wifi("aa:bb:cc:dd:ee:09", b"Free", channel=149, auth=0, pairwise=0)
    record = nodes.wifi_record(line(node))
    assert (record["capabilities"], record["frequency"], record["band"]) == ("[ESS]", 5745, "5")


def test_a_name_with_commas_bytes_that_are_not_utf8_or_no_name_at_all_reads_as_it_should():
    node = FakeNode()
    node.plan, node.running = {"version": 1, "ghz2": 0x7FFE, "ghz5": 0x1FFFFFFE, "ble": True}, True
    cases = [(b'Black Pearl, "Guest"', 'Black Pearl, "Guest"', False), (b"caf\xe9\xff,", "caf��,", False),
             (b"", "", True), (b"\x00\x00\x00", "", True), ("é".encode() * 16, "é" * 16, False),
             (b"A*B\x00", "A*B\x00", False)]
    for raw, ssid, hidden in cases:
        node.wifi("aa:bb:cc:00:00:01", raw, flags=1 if hidden else 0)
        record = nodes.wifi_record(line(node))
        assert (record["ssid"], record["hidden"]) == (ssid, hidden), raw
    long = nodes.wifi_record(["W", "1", "0", "AA:BB:CC:00:00:01", "-50", "1", "2412", "3", "4", "4", "0",
                              ("é".encode() * 20).hex()])
    assert long["ssid"] == "é" * 20 and nodes.wifi_record(["W", "1", "x"] + ["0"] * 9) is None
    assert nodes.parse(b"ESP-ROM:esp32c5-eco2") is None and nodes.parse(b"@W,1,2*00") is False
    assert nodes.parse(b"@A,18,START,ok,*0C") == ["A", "18", "START", "ok", ""]
    assert nodes.parse(b"@A,18,START,ok,*0C\r") == ["A", "18", "START", "ok", ""]


def test_a_bluetooth_line_reads_as_the_bluetooth_scan_reads_a_device():
    node = FakeNode()
    node.plan, node.running = {"version": 1, "ghz2": 0x7FFE, "ghz5": 0x1FFFFFFE, "ble": True}, True
    node.ble("c1:22:33:44:55:66", rssi=-71, kind=1, flags=3, maker=0x004C, name="Kraken, é".encode())
    record = nodes.ble_record(line(node))
    assert record["address"] == "C1:22:33:44:55:66" and record["address_type"] == 1 and record["rssi"] == -71
    assert record["name"] == "Kraken, é" and record["manufacturer"] == {0x004C: b""} and not record["classic"]
    node.ble("00:11:22:33:44:55", kind=0, flags=6)
    record = nodes.ble_record(line(node))
    assert (record["address_type"], record["manufacturer"], record["name"]) == (0, {}, "")
    assert not record["connectable"] and nodes.ble_record(["B", "1", "0", "C1:22:33:44:55:66", "1", "-50", "1", "",
                                                           ""])["connectable"]


def drive(rig, tmp_path, cfg=None, fleet=QuietFleet):
    settings = {"ble": False}
    settings.update(cfg or {})
    return Wardrive(settings, FakeGPS(FIX), root=tmp_path / "home", adapters=ADAPTERS, ble=NoBle(), fleet=fleet,
                    clock=lambda: NOW, nodes=rig.manager)


def rows_of(path, mac):
    return [row for row in csv.reader(path.read_text(encoding="utf-8").splitlines()[2:]) if row[0] == mac]


def test_a_network_heard_by_an_adapter_and_by_nodes_is_one_device_with_at_most_two_rows(tmp_path):
    rig = Rig(tmp_path, {"ttyACM0": ("303a:1001", NATIVE, FakeNode(ONE)),
                         "ttyACM1": ("303a:1001", "", FakeNode(TWO)), "ttyACM2": ("303a:1001", "", FakeNode(THREE))},
              node_cfg(ONE, TWO, THREE))
    rig.manager.scan()
    wardrive = drive(rig, tmp_path, fleet=Sweeps)
    wardrive.start()
    rig.settle()
    adapter = parse_iw_scan(SCAN)[0]
    hearing = [node for node in rig.devices.values() if node.scans(6)]
    assert len(hearing) == 2
    for rssi in (-40, -90, -55, -70, -45, -85, -62):
        for node in rig.devices.values():
            node.wifi(adapter["bssid"], adapter["ssid"].encode(), rssi=rssi, channel=6, auth=7, flags=2)
        rig.settle()
    radios = wardrive.status()["adapters"]
    assert [a["label"] for a in radios] == ["", "", "Node N0", "Node N1", "Node N2"]
    assert all(a["method"] == "node" and a["error"] == "" and a["bands"] and a["sweeps"] for a in radios[2:])
    wardrive.stop()
    rig.settle()
    device = wardrive.tracker.find("WIFI", adapter["bssid"])
    assert device.sightings == 1 + 2 * 7 and len(wardrive.tracker.found()) == 3
    rows = rows_of(wardrive.log_path, adapter["bssid"].lower())
    assert len(rows) == 2 and {row[2] for row in rows} == {adapter["capabilities"]}
    assert sorted(row[6] for row in rows) == ["-40", "-90"]
    assert all(not node.running for node in rig.devices.values())


def test_a_node_unplugged_mid_drive_hands_its_channels_to_the_others_and_gets_a_share_back(tmp_path):
    rig = two_nodes(tmp_path)
    rig.manager.scan()
    wardrive = drive(rig, tmp_path)
    wardrive.start()
    rig.settle()
    one, two = rig.devices["ttyACM0"], rig.devices["ttyUSB1"]
    first = (one.plan["version"], two.plan["version"])
    assert one.running and two.running and not one.scans(149) and two.scans(149)
    two.unplug()
    rig.settle()
    assert one.plan["version"] > max(first) and one.scans(149) and one.scans(1)
    assert planner.share_of(rig.manager.plans[ONE]) == planner.channels("US")
    assert [(a["label"], a["error"]) for a in wardrive.status()["adapters"][-2:]] == [("Node N0", ""),
                                                                                      ("Node N1", nodes.UNPLUGGED)]
    assert one.wifi("aa:bb:cc:00:01:49", b"Far", channel=149)
    rig.settle()
    assert wardrive.tracker.find("WIFI", "AA:BB:CC:00:01:49") is not None
    two.replug()
    rig.clock.advance(nodes.NODE_RETRY_SECONDS)
    rig.manager.scan()
    rig.settle()
    assert two.running and two.scans(149) and not one.scans(149)
    assert two.plan["version"] == one.plan["version"] > first[1]
    assert wardrive.status()["adapters"][-1]["error"] == ""


def test_nodes_start_and_stop_with_the_wardrive_and_say_nothing_after_it(tmp_path):
    rig = two_nodes(tmp_path)
    rig.manager.scan()
    one = rig.devices["ttyACM0"]
    assert not one.running and "PLAN" not in one.commands
    wardrive = drive(rig, tmp_path)
    wardrive.start()
    rig.settle()
    assert one.running and one.commands.index("PLAN") < one.commands.index("START")
    wardrive.stop()
    rig.settle()
    assert not one.running and one.commands[one.commands.index("START"):].count("STOP") == 1
    one.running = True
    one.wifi("aa:bb:cc:00:00:06", b"After", channel=1)
    rig.settle()
    assert wardrive.tracker.find("WIFI", "AA:BB:CC:00:00:06") is None


def test_nodes_with_bluetooth_feed_the_wardrive_its_devices(tmp_path):
    rig = two_nodes(tmp_path, node_cfg(ONE, TWO, ble=True))
    rig.manager.scan()
    wardrive = drive(rig, tmp_path, {"ble": True})
    wardrive.start()
    rig.settle()
    one, two = rig.devices["ttyACM0"], rig.devices["ttyUSB1"]
    assert one.plan["ble"] and not two.plan["ble"]
    assert rig.manager.node(TWO)["error"] == nodes.NO_BLE
    for _ in range(2):
        one.ble("c1:22:33:44:55:66", rssi=-60, maker=0x004C, name=b"Tag")
        rig.settle()
    device = wardrive.tracker.find("BLE", "C1:22:33:44:55:66")
    assert device.name == "Tag" and device.extra == 0x004C and device.sightings == 2


def test_a_wardrive_with_bluetooth_off_asks_no_node_for_it(tmp_path):
    rig = two_nodes(tmp_path, node_cfg(ONE, TWO, ble=True))
    rig.manager.scan()
    wardrive = drive(rig, tmp_path, {"ble": False, "wifi_adapters": ["none"]})
    wardrive.start()
    rig.settle()
    assert not rig.devices["ttyACM0"].plan["ble"] and wardrive.state == "running"


def test_recon_hears_the_nodes(tmp_path):
    rig = two_nodes(tmp_path, node_cfg(ONE, TWO, ble=True))
    rig.manager.scan()
    settings = {"recon_categories": list(recon.CATEGORIES), "recon_display_min": "low", "recon_alert_min": "high",
                "recon_sound": False}
    scout = Recon(settings, FakeGPS(FIX), root=tmp_path, adapters=ADAPTERS, ble=NoBle(), fleet=QuietFleet,
                  sound=Sound(), clock=lambda: NOW, nodes=rig.manager)
    scout.start()
    rig.settle()
    one = rig.devices["ttyACM0"]
    assert one.wifi("00:25:DF:11:22:33", b"", channel=1)
    assert one.ble("11:22:33:44:55:66", maker=0x034D)
    rig.settle()
    assert scout.device("wifi", "00:25:DF:11:22:33")["category"] == "axon"
    assert scout.device("ble", "11:22:33:44:55:66") is not None
    assert scout.summary()["radios"] == len(ADAPTERS) + 2
    rig.devices["ttyUSB1"].unplug()
    rig.settle()
    scout._follow_adapters()
    assert scout.errors["wifi"] == "Node N1: Unplugged" and one.scans(149)
    scout.stop()
    rig.settle()
    assert not one.running and rig.manager.adapters() == []


def test_nodes_can_be_added_renamed_given_bluetooth_and_removed_and_the_settings_follow(tmp_path):
    saved = []
    rig = two_nodes(tmp_path, cfg={}, on_change=saved.append)
    rig.manager.scan()
    rig.manager.add(ONE, "  Bow   lookout  ")
    rig.manager.add(TWO)
    rig.manager.add(TWO)
    assert saved[-1] == [{"id": ONE, "name": "Bow lookout", "ble": False}, {"id": TWO, "name": "", "ble": False}]
    assert [node["name"] for node in rig.manager.known()] == ["Bow lookout", "E4F6"] and rig.manager.found() == []
    plans = dict(rig.manager.plans)
    rig.manager.set_ble(ONE, True)
    assert rig.manager.plans[TWO] is plans[TWO] and rig.manager.plans[ONE].version > plans[ONE].version
    rig.manager.rename(ONE, "Stern")
    rig.manager.set_led(ONE, "gpio,27,1")
    rig.settle()
    assert rig.devices["ttyACM0"].led == ["gpio", "27", "1"]
    assert saved[-1][0] == {"id": ONE, "name": "Stern", "ble": True, "led": "gpio,27,1"}
    with pytest.raises(ValueError):
        rig.manager.set_led(ONE, "gpio,15")
    for i in range(6):
        rig.manager.add("%012x" % i)
    with pytest.raises(ValueError):
        rig.manager.add("%012x" % 99)
    rig.manager.remove(ONE)
    assert ONE not in [entry["id"] for entry in saved[-1]] and [node["id"] for node in rig.manager.found()] == [ONE]
    assert set(rig.manager.plans) == {TWO}


def test_identify_blinks_the_node(tmp_path):
    rig = two_nodes(tmp_path)
    rig.manager.scan()
    assert rig.manager.identify(ONE) and not rig.manager.identify("%012x" % 7)
    rig.settle()
    assert rig.devices["ttyACM0"].idents == [5]


def test_a_node_given_away_for_flashing_is_left_alone_until_it_is_taken_back(tmp_path):
    rig = two_nodes(tmp_path)
    rig.manager.scan()
    wardrive = drive(rig, tmp_path)
    wardrive.start()
    rig.settle()
    path = rig.manager.release(TWO)
    assert path == str(rig.by_id / BRIDGE) and not rig.devices["ttyUSB1"].taken
    assert rig.manager.node(TWO)["updating"] and rig.manager.node(TWO)["error"] == nodes.UPDATING
    assert planner.share_of(rig.manager.plans[ONE]) == planner.channels("US")
    assert not hardware.hold_port(path, "gps")
    rig.clock.advance(60)
    rig.manager.scan()
    assert not rig.devices["ttyUSB1"].taken and not rig.manager.node(TWO)["connected"]
    rig.manager.take_back(path)
    rig.manager.scan()
    rig.settle()
    assert rig.manager.node(TWO)["connected"] and rig.devices["ttyUSB1"].running
    assert rig.manager.release("%012x" % 7) is None


def test_a_board_on_both_of_its_usb_ports_is_one_node_and_neither_port_is_a_gps(tmp_path):
    rig = Rig(tmp_path, {"ttyACM0": ("303a:1001", NATIVE, FakeNode(ONE)), "ttyUSB1": ("10c4:ea60", "", None)},
              node_cfg(ONE))
    rig.bus.add(rig.dev / "ttyUSB1", FakeNode(ONE, clock=rig.clock))
    rig.manager.scan()
    assert len(rig.manager.links) == 1 and rig.manager.twins == {rig.path("ttyUSB1"): ONE}
    rig.clock.advance(60)
    rig.manager.scan()
    assert len(rig.bus.ports) == 2
    assert hardware.gps_candidates(rig.sys_tty, rig.dev) == []


def test_the_country_changes_every_plan(tmp_path):
    rig = two_nodes(tmp_path)
    rig.manager.scan()
    wardrive = drive(rig, tmp_path)
    wardrive.start()
    rig.settle()
    rig.manager.set_country("JP")
    rig.settle()
    one = rig.devices["ttyACM0"]
    assert one.plan["country"] == "JP" and one.country == "JP"
    shares = [planner.share_of(plan) for plan in rig.manager.plans.values()]
    assert sorted(item for share in shares for item in share) == sorted(planner.channels("JP"))


def test_a_node_gets_the_led_of_its_board_from_the_settings_when_it_is_plugged_in(tmp_path):
    cfg = node_cfg(ONE)
    cfg["nodes"][0]["led"] = "gpio,28"
    rig = Rig(tmp_path, {"ttyACM0": ("303a:1001", NATIVE, FakeNode(ONE, board="stamp-c5"))}, cfg)
    rig.manager.scan()
    rig.settle()
    assert rig.devices["ttyACM0"].led == ["gpio", "28"]


def test_the_settings_keep_only_nodes_that_make_sense(tmp_path):
    path = tmp_path / "config.json"
    path.write_text('{"country": "XX", "nodes": [{"id": "A0B1C2D3E4F5", "name": "Bow  lookout of the Black Pearl",'
                    ' "ble": true, "led": "ws2812,27"}, {"id": "a0b1c2d3e4f5"}, {"id": "nothex123456"}, 5,'
                    ' {"id": "a0b1c2d3e4f6", "ble": "yes", "led": "gpio,12"}]}')
    cfg = config.load(path)
    assert cfg["country"] == "US" and config.DEFAULTS["country"] == "US"
    assert cfg["nodes"] == [{"id": ONE, "name": "Bow lookout of t", "ble": True, "led": "ws2812,27"},
                            {"id": TWO, "name": "", "ble": False}]
    path.write_text('{"country": "DE", "nodes": [%s]}' % ",".join('{"id": "%012x"}' % i for i in range(12)))
    cfg = config.load(path)
    assert cfg["country"] == "DE" and len(cfg["nodes"]) == config.MAX_NODES
    config.save(cfg, path)
    assert config.load(path)["nodes"] == cfg["nodes"]
    assert {code for code, _ in config.COUNTRIES} == set(planner.COUNTRIES)


def test_a_bridge_waits_for_the_gps_to_have_its_look_and_a_gps_port_is_never_asked(tmp_path):
    gps, gpsd, chosen = Talker(GGA), Talker(GGA), Talker(GGA)
    rig = Rig(tmp_path, {"ttyUSB0": ("10c4:ea60", GPS_LINK, gps), "ttyUSB1": ("1a86:55d4", BRIDGE, FakeNode(TWO)),
                         "ttyUSB2": ("1a86:7523", "", FakeNode(THREE)), "ttyUSB3": ("10c4:ea60", "", gpsd),
                         "ttyUSB4": ("1a86:55d3", "", chosen)}, bridge_wait=nodes.BRIDGE_SECONDS)
    rig.manager.configure({"gps_source": rig.path("ttyUSB4")})
    rig.manager.scan()
    assert rig.manager.found() == [] and rig.bus.ports == []
    hardware.mark_port(rig.path("ttyUSB1"), "checked")
    hardware.mark_port(rig.path("ttyUSB0"), "gps")
    rig.manager.scan()
    assert [node["id"] for node in rig.manager.found()] == [TWO]
    gnss._mark_gpsd({"class": "DEVICES", "devices": [{"path": rig.path("ttyUSB3"), "driver": "u-blox"}]})
    rig.clock.advance(nodes.BRIDGE_SECONDS)
    for _ in range(20):
        rig.manager.scan()
        rig.clock.advance(nodes.SCAN_SECONDS * 10)
    assert [node["id"] for node in rig.manager.found()] == [TWO, THREE]
    assert gps.heard == gpsd.heard == chosen.heard == b""


class Receiver:
    """Just enough of pyserial for the GPS layer: a port that says `lines`, then nothing."""

    def __init__(self, lines, seen):
        self.lines, self.seen = list(lines), seen

    def __call__(self, path, baud, **_):
        self.path = path
        return self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read_until(self, end, size):
        self.seen.append(hardware.port_mark(self.path))
        return self.lines.pop(0) if self.lines else b""


def test_the_gps_layer_marks_what_it_heard_on_a_port_and_skips_a_node_port(tmp_path, monkeypatch):
    now = [0.0]

    def tick():
        now[0] += 1
        return now[0]
    monkeypatch.setattr(gnss, "time", types.SimpleNamespace(monotonic=tick))
    seen = []
    monkeypatch.setitem(sys.modules, "serial", types.SimpleNamespace(Serial=Receiver([GGA], seen)))
    path = str(tmp_path / "ttyUSB0")
    GPS("auto", candidates=lambda: [{"path": path}])._serial(threading.Event())
    assert seen[:2] == [None, "gps"] and hardware.port_mark(path) is None and hardware.port_holder(path) is None
    seen.clear()
    GPS("auto", candidates=lambda: [{"path": path}])._serial(threading.Event())
    assert seen and set(seen) == {None} and hardware.port_mark(path) == "checked"
    hardware.mark_port(path, "node")
    seen.clear()
    GPS("auto", candidates=lambda: [{"path": path}])._serial(threading.Event())
    assert seen == []
    gnss._mark_gpsd({"class": "TPV", "device": str(tmp_path / "ttyACM3"), "mode": 3})
    assert hardware.port_mark(str(tmp_path / "ttyACM3")) == "gps"

    class Gpsd:
        said = [b'{"class":"DEVICES","devices":[{"path":"%s"}]}\n' % str(tmp_path / "ttyUSB7").encode(), b""]

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def settimeout(self, seconds):
            pass

        def sendall(self, data):
            pass

        def recv(self, size):
            return self.said.pop(0)
    monkeypatch.setattr(gnss.socket, "create_connection", lambda address, timeout: Gpsd())
    GPS("gpsd")._gpsd(threading.Event())
    assert hardware.port_mark(str(tmp_path / "ttyUSB7")) == "gps"


def test_the_esp32_c5_port_is_never_a_gps_port_even_before_it_answered(tmp_path):
    sys_tty, dev, _ = usb_serial(tmp_path, {"ttyACM0": ("303a:1001", NATIVE), "ttyUSB0": ("10c4:ea60", "")})
    assert [c["path"] for c in hardware.gps_candidates(sys_tty, dev)] == [str(dev / "ttyUSB0")]


def test_a_board_on_both_usb_ports_given_away_for_flashing_has_neither_port_opened(tmp_path):
    rig = Rig(tmp_path, {"ttyACM0": ("303a:1001", NATIVE, FakeNode(ONE)), "ttyUSB1": ("10c4:ea60", "", None)},
              node_cfg(ONE))
    twin = rig.bus.add(rig.dev / "ttyUSB1", FakeNode(ONE, clock=rig.clock))
    rig.manager.scan()
    rig.manager.attach(rig, lambda networks, adapter: None)
    rig.settle()
    asked = len(twin.commands)
    path = rig.manager.release(ONE)
    for _ in range(5):
        rig.clock.advance(nodes.NODE_RETRY_SECONDS)
        rig.manager.scan()
        rig.settle()
    assert len(twin.commands) == asked and not twin.taken and not hardware.hold_port(rig.path("ttyUSB1"), "gps")
    assert not rig.manager.node(ONE)["connected"] and rig.manager.node(ONE)["updating"]
    rig.manager.take_back(path)
    rig.manager.scan()
    rig.settle()
    assert rig.manager.node(ONE)["connected"] and not rig.manager.node(ONE)["updating"]
    assert rig.manager.away == {} and len(rig.manager.links) == 1


def test_a_node_that_comes_back_on_another_port_after_flashing_is_taken_back_there(tmp_path):
    rig = Rig(tmp_path, {"ttyACM0": ("303a:1001", NATIVE, FakeNode(ONE))}, node_cfg(ONE))
    rig.manager.scan()
    node = rig.devices["ttyACM0"]
    assert rig.manager.release(ONE) == str(rig.by_id / NATIVE)
    (rig.dev / "ttyACM0").unlink()
    (rig.sys_tty / "ttyACM0").rename(rig.sys_tty / "ttyACM1")
    (rig.dev / "ttyACM1").write_text("")
    (rig.by_id / NATIVE).unlink()
    (rig.by_id / NATIVE).symlink_to(rig.dev / "ttyACM1")
    rig.bus.add(rig.dev / "ttyACM1", node)
    rig.clock.advance(nodes.NODE_RETRY_SECONDS)
    rig.manager.scan()
    assert not node.taken and not rig.manager.node(ONE)["connected"] and rig.manager.node(ONE)["updating"]
    assert not hardware.hold_port(rig.path("ttyACM1"), "gps")
    rig.manager.take_back(rig.path("ttyACM1"))
    rig.manager.scan()
    rig.settle()
    status = rig.manager.node(ONE)
    assert status["connected"] and not status["updating"] and status["error"] == "" and rig.manager.away == {}
    assert hardware.port_holder(rig.path("ttyACM0")) is None and hardware.port_holder(rig.path("ttyACM1")) == "node"


class Garbling(FakeNode):
    garbled = False

    def receive(self, data):
        if data.startswith(b"#PLAN") and not self.garbled:
            self.garbled, data = True, data.replace(b"#PLAN,", b"#PLAN,9", 1)
        super().receive(data)


def test_a_plan_garbled_on_the_way_is_sent_again(tmp_path):
    rig = Rig(tmp_path, {"ttyACM0": ("303a:1001", NATIVE, Garbling(ONE))}, node_cfg(ONE))
    rig.manager.scan()
    rig.manager.attach(rig, lambda networks, adapter: None)
    rig.settle(30)
    node = rig.devices["ttyACM0"]
    assert node.commands.count("PLAN") == 1 and node.plan["version"] == rig.manager.plans[ONE].version
    assert node.running and rig.manager.node(ONE)["error"] == ""


def test_recon_keeps_what_bluez_found_when_a_node_hears_the_same_tag(tmp_path):
    rig = Rig(tmp_path, {"ttyACM0": ("303a:1001", NATIVE, FakeNode(ONE))}, node_cfg(ONE, ble=True))
    rig.manager.scan()
    settings = {"recon_categories": list(recon.CATEGORIES), "recon_display_min": "low", "recon_alert_min": "high",
                "recon_sound": False}
    scout = Recon(settings, FakeGPS(FIX), root=tmp_path, adapters=ADAPTERS, ble=NoBle(), fleet=QuietFleet,
                  sound=Sound(), clock=lambda: NOW, nodes=rig.manager)
    scout.start()
    rig.settle()
    tag = {"address": "C1:22:33:44:55:66", "address_type": 1, "rssi": -60, "name": "Tag", "uuids": [],
           "manufacturer": {0x004C: bytes([0x12, 0x19]) + bytes(25)}, "service_data": {}}
    scout.observe("ble", tag)
    classified = scout.counters["classified"]
    one = rig.devices["ttyACM0"]
    for rssi in (-58, -50):
        assert one.ble(tag["address"], rssi=rssi, maker=0x004C)
        rig.settle()
    device = scout.device("ble", tag["address"])
    assert device["category"] == "airtag" and device["rssi"] == -50 and device["count"] == 3
    assert device["name"] == "Tag" and scout.counters["classified"] == classified
    scout.observe("ble", dict(tag, manufacturer={}, name=""))
    assert scout.device("ble", tag["address"])["category"] is None
    speaker = dict(tag, address="C1:22:33:44:55:77", manufacturer={}, name="Speaker")
    scout.observe("ble", speaker)
    assert one.ble(speaker["address"], rssi=-40)
    rig.settle()
    assert scout.device("ble", speaker["address"])["name"] == "Speaker"
    scout.stop()


def test_nodes_scan_only_the_bands_the_wardrive_keeps(tmp_path):
    rig = two_nodes(tmp_path)
    rig.manager.scan()
    wardrive = drive(rig, tmp_path, {"wifi_bands": "2.4"})
    wardrive.start()
    rig.settle()
    one, two = rig.devices["ttyACM0"], rig.devices["ttyUSB1"]
    heard = [ch for node in (one, two) for ch in range(1, 178) if node.scans(ch)]
    assert sorted(heard) == list(range(1, 12))
    wardrive.stop()
    rig.settle()
    wardrive = drive(rig, tmp_path, {"wifi_bands": "6"})
    wardrive.start()
    rig.settle()
    assert (one.plan["ghz2"], one.plan["ghz5"], two.plan["ghz2"], two.plan["ghz5"]) == (1, 1, 1, 1)
    assert one.running and two.running
    wardrive.stop()


def test_the_settings_are_saved_without_holding_up_the_nodes(tmp_path):
    free = []

    def save(entries):
        def other():
            if rig.manager.lock.acquire(timeout=0.5):
                rig.manager.lock.release()
                free.append(True)
        thread = threading.Thread(target=other)
        thread.start()
        thread.join()
    rig = two_nodes(tmp_path, cfg={}, on_change=save)
    rig.manager.scan()
    rig.manager.add(ONE)
    rig.manager.rename(ONE, "Bow")
    rig.manager.remove(ONE)
    assert free == [True] * 3


def test_a_batch_of_sightings_waits_while_the_node_list_is_being_changed(tmp_path):
    rig = two_nodes(tmp_path)
    rig.manager.scan()
    heard = []
    rig.manager.attach(rig, lambda networks, adapter: heard.append(adapter["label"]))
    link = rig.manager.links[ONE]
    with rig.manager.lock:
        thread = threading.Thread(target=rig.manager.sightings, args=(link, [{"bssid": "AA"}], []))
        thread.start()
        thread.join(0.2)
        assert thread.is_alive() and heard == []
    thread.join()
    assert heard == ["Node N0"]


def test_a_node_that_speaks_another_protocol_says_which_side_to_update(tmp_path):
    rig = Rig(tmp_path, {"ttyACM0": ("303a:1001", NATIVE, FakeNode(ONE, protocol=2)),
                         "ttyACM1": ("303a:1001", "", FakeNode(TWO, protocol=0))}, node_cfg(ONE, TWO))
    rig.manager.scan()
    assert [node["error"] for node in rig.manager.known()] == [nodes.NEWER, nodes.OLDER]
    assert rig.manager.in_use() == [] and "Quartermaster" in nodes.NEWER


def test_adding_something_that_is_not_a_node_id_says_so(tmp_path):
    rig = two_nodes(tmp_path, cfg={})
    for bad in ("a0b1c2", "nothex123456", None):
        with pytest.raises(ValueError, match=nodes.NOT_A_NODE):
            rig.manager.add(bad)
    rig.manager.add(ONE.upper())
    assert [node["id"] for node in rig.manager.known()] == [ONE]


def test_only_the_wardrive_or_recon_that_took_the_nodes_can_let_them_go(tmp_path):
    rig = two_nodes(tmp_path)
    rig.manager.scan()
    rig.manager.attach(rig, lambda networks, adapter: None)
    rig.settle()
    rig.manager.detach(object())
    rig.settle()
    assert all(node.running for node in rig.devices.values()) and len(rig.manager.adapters()) == 2


def test_a_node_port_that_just_failed_is_asked_again_a_moment_later(tmp_path):
    rig = two_nodes(tmp_path)
    rig.manager.scan()
    rig.devices["ttyACM0"].lose = 10 ** 6
    rig.settle(36)
    assert not rig.manager.node(ONE)["connected"]
    rig.devices["ttyACM0"].lose = 0
    opened = len(rig.bus.ports)
    rig.manager.scan()
    assert len(rig.bus.ports) == opened
    rig.clock.advance(1)
    rig.manager.scan()
    assert len(rig.bus.ports) == opened + 1 and rig.manager.node(ONE)["connected"]


def test_a_name_longer_than_32_characters_is_cut_as_the_adapter_scan_cuts_it():
    record = nodes.wifi_record(["W", "1", "0", "AA:BB:CC:00:00:01", "-50", "1", "2412", "3", "4", "4", "0",
                                (b"Q" * 40).hex()])
    assert record["ssid"] == "Q" * 32


def test_without_pyserial_the_manager_does_not_look_for_it_again_and_again(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "serial", None)
    rig = two_nodes(tmp_path)
    rig.manager.factory = None
    assert not rig.manager.available
    monkeypatch.setitem(sys.modules, "serial", types.SimpleNamespace(Serial=rig.bus.factory))
    assert not rig.manager.available and rig.manager.error == nodes.NO_SERIAL


class SlowBus(FakeBus):
    def __init__(self, clock):
        super().__init__(clock)
        self.opening, self.go = threading.Event(), threading.Event()

    def factory(self):
        self.opening.set()
        self.go.wait(5)
        return super().factory()


def test_stopping_while_a_port_is_being_asked_leaves_no_port_open(tmp_path):
    rig = Rig(tmp_path, {"ttyACM0": ("303a:1001", NATIVE, FakeNode(ONE))}, node_cfg(ONE))
    bus = SlowBus(rig.clock)
    bus.devices = rig.bus.devices
    manager = NodeManager(node_cfg(ONE), bus.factory, rig.sys_tty, rig.dev, rig.by_id, clock=rig.clock)
    manager.start()
    assert bus.opening.wait(5)
    stopping = threading.Thread(target=manager.stop)
    stopping.start()
    stopping.join(0.2)
    bus.go.set()
    stopping.join(10)
    assert not stopping.is_alive() and all(link.closed for link in manager.links.values())
    assert not rig.devices["ttyACM0"].taken
    assert not [t for t in threading.enumerate() if t.name.startswith("node")]
    opened = len(bus.ports)
    rig.clock.advance(nodes.NODE_RETRY_SECONDS)
    manager.scan()
    assert len(bus.ports) == opened


HUB, AIR1, AIR2 = "c0b1c2d3e4f5", "b0b1c2d3e4f5", "b0b1c2d3e4f6"
HUB_LINK = "usb-Espressif_USB_JTAG_serial_debug_unit_C0:B1:C2:D3:E4:F5-if00"
KEY = "8f1e2d3c4b5a69788796a5b4c3d2e1f0"


def hub_rig(tmp_path, airs=(AIR1, AIR2), paired=True, hub=True, key=KEY, **kwargs):
    """ONE on USB, a hub on USB, and wireless nodes around the hub: by default in the settings and admitted."""
    air_nodes = [FakeAir(node_id) for node_id in airs]
    fake_hub = FakeHub(HUB, airs=air_nodes)
    cfg = {"country": "US", "nodes": [{"id": ONE, "name": "N0"}, {"id": HUB, "name": "Hub", "hub": hub}]}
    if key:
        cfg["cluster_key"] = key
    if paired:
        fake_hub.saved_key = key
        for i, air in enumerate(air_nodes):
            fake_hub.admitted[air.node_id], air.hub = None, HUB
            cfg["nodes"].append({"id": air.node_id, "name": "A%d" % i, "wireless": True})
    ports = {"ttyACM0": ("303a:1001", NATIVE, FakeNode(ONE)), "ttyACM1": ("303a:1001", HUB_LINK, fake_hub)}
    rig = Rig(tmp_path, ports, cfg, **kwargs)
    rig.hub, rig.airs = fake_hub, {air.node_id: air for air in air_nodes}
    return rig


def shares(rig):
    return {node_id: planner.share_of(plan) for node_id, plan in rig.manager.plans.items()}


def test_a_hub_gets_its_channel_and_key_scans_nothing_and_runs_its_wireless_nodes_with_the_wardrive(tmp_path):
    rig = hub_rig(tmp_path)
    rig.manager.scan()
    rig.settle()
    hub, one = rig.hub, rig.devices["ttyACM0"]
    assert hub.role == "hub" and hub.home == 6 and hub.hub_commands[0] == ("HUB", "6", KEY)
    assert rig.manager.hub()["ready"] and rig.manager.hub()["nodes"] == 2
    assert not hub.running and not any(air.running for air in rig.airs.values())
    wardrive = drive(rig, tmp_path)
    wardrive.start()
    rig.settle(4)
    assert rig.manager.in_use() == [ONE, AIR1, AIR2]
    assert "PLAN" not in hub.commands and HUB not in rig.manager.plans and hub.running
    assert one.running and all(air.running for air in rig.airs.values())
    assert all(air.plan["version"] == rig.manager.plans[air.node_id].version for air in rig.airs.values())
    assert sorted(set(item for share in shares(rig).values() for item in share)) == sorted(planner.channels("US"))
    air = rig.airs[AIR1]
    channel = planner.share_of(rig.manager.plans[AIR1])[0][1]
    assert air.wifi("aa:bb:cc:00:00:a1", b"Up the mast", channel=channel)
    rig.settle()
    assert wardrive.tracker.find("WIFI", "AA:BB:CC:00:00:A1").name == "Up the mast"
    radios = {a["label"]: a for a in wardrive.status()["adapters"] if a["method"] == "node"}
    assert set(radios) == {"Node N0", "Node A0", "Node A1"} and radios["Node A0"]["error"] == ""
    node = rig.manager.node(AIR1)
    assert node["wireless"] and node["connected"] and node["in_use"] and node["port"] == "" and node["link_rssi"] == -52
    assert rig.manager.node(HUB)["hub"] and rig.manager.node(HUB)["hub_ready"]
    wardrive.stop()
    rig.settle(3)
    assert not hub.running and not any(air.running for air in rig.airs.values()) and not one.running


def test_wireless_nodes_get_a_smaller_share_and_a_bluetooth_plan_through_the_hub(tmp_path):
    rig = hub_rig(tmp_path, airs=(AIR1,))
    rig.manager.set_ble(AIR1, True)
    rig.manager.scan()
    rig.manager.attach(rig, lambda networks, adapter: None, lambda devices: None)
    rig.settle(4)
    usb, air = planner.sweep_ms(shares(rig)[ONE]), planner.sweep_ms(shares(rig)[AIR1])
    assert air < usb and rig.airs[AIR1].plan["ble"]
    assert rig.airs[AIR1].plan["version"] == rig.manager.plans[AIR1].version
    assert any(command[:3] == ("TO", AIR1, "PLAN") for command in rig.hub.hub_commands)


def test_the_hub_key_is_made_once_kept_in_the_settings_and_never_shown(tmp_path):
    keys, saved = [], []
    second = FakeHub(TWO)
    rig = hub_rig(tmp_path, airs=(), paired=False, hub=False, key=None, on_change=saved.append)
    rig.bus.add(rig.dev / "ttyACM1", rig.hub)
    _, dev, _ = usb_serial(tmp_path, {"ttyACM2": ("303a:1001", "")})
    rig.bus.add(dev / "ttyACM2", second).clock = rig.clock
    second.last_host = second.last_status = second.last_hello = rig.clock()
    rig.manager.on_key = keys.append
    rig.manager.scan()
    rig.manager.add(TWO, "Spare")
    with pytest.raises(ValueError, match="firmware update"):
        rig.manager.set_hub(ONE, True)
    rig.manager.set_hub(HUB, True)
    rig.settle()
    assert len(keys) == 1 and len(keys[0]) == 32 and int(keys[0], 16) and rig.hub.saved_key == keys[0]
    assert [entry.get("hub", False) for entry in saved[-1]] == [False, True, False]
    rig.manager.set_hub(TWO, True)
    rig.settle()
    assert len(keys) == 1 and second.role == "hub" and second.saved_key == keys[0] and rig.hub.role == "usb"
    assert [entry.get("hub", False) for entry in saved[-1]] == [False, False, True]
    assert HUB in rig.manager.in_use() and TWO not in rig.manager.in_use()
    shown = repr(rig.manager.known()) + repr(rig.manager.hub()) + repr(rig.manager.found())
    assert keys[0] not in shown and keys[0] not in repr(saved)
    path = tmp_path / "config.json"
    cfg = config.load(path)
    cfg.update(nodes=saved[-1], cluster_key=keys[0])
    config.save(cfg, path)
    assert config.load(path)["cluster_key"] == keys[0] and config.load(path)["nodes"] == saved[-1]
    assert path.stat().st_mode & 0o777 == 0o600
    (tmp_path / ".config.json").write_text("{}")
    (tmp_path / ".config.json").chmod(0o644)
    config.save(cfg, path)
    assert path.stat().st_mode & 0o777 == 0o600
    path.write_text('{"cluster_key": "%s"}' % KEY.upper())
    assert config.load(path)["cluster_key"] == KEY
    for bad in ("0" * 32, KEY[:-1] + "g", "12ab", 7):
        path.write_text('{"cluster_key": %s}' % ('"%s"' % bad if isinstance(bad, str) else bad))
        assert "cluster_key" not in config.load(path)
    entries = config.node_entries([{"id": ONE, "hub": True}, {"id": TWO, "hub": True},
                                   {"id": THREE, "hub": True, "wireless": True}])
    assert [(e.get("hub"), e.get("wireless")) for e in entries] == [(True, None), (None, None), (None, True)]
    many = config.node_entries([{"id": "%012x" % i, "wireless": i % 2 == 0} for i in range(40)])
    assert len(many) == config.MAX_NODES + config.MAX_WIRELESS


def test_a_hub_with_firmware_that_cannot_be_one_says_so_and_is_sent_nothing(tmp_path):
    rig = Rig(tmp_path, {"ttyACM0": ("303a:1001", NATIVE, FakeNode(ONE))},
              {"cluster_key": KEY, "nodes": [{"id": ONE, "name": "Old", "hub": True}]})
    rig.manager.scan()
    rig.settle()
    assert "HUB" not in rig.devices["ttyACM0"].commands and rig.manager.node(ONE)["error"] == nodes.NO_ESPNOW
    assert rig.manager.in_use() == [] and not rig.manager.hub()["ready"] and not rig.manager.pair()


def test_a_new_wireless_node_is_shown_while_pairing_and_added_through_the_hub(tmp_path):
    saved = []
    rig = hub_rig(tmp_path, paired=False, on_change=saved.append)
    rig.manager.scan()
    rig.settle()
    assert rig.manager.joining() == [] and rig.hub.asked == {}
    assert rig.manager.pair()
    rig.settle(2)
    assert rig.hub.hub_commands[-1] == ("PAIR", "60") and 55 <= rig.manager.hub()["pairing"] <= 60
    joining = rig.manager.joining()
    assert [join["id"] for join in joining] == [AIR1, AIR2] and joining[0]["board"] == "c5-8mb"
    assert joining[0]["firmware"] == "1.1.0" and joining[0]["rssi"] == -52 and not joining[0]["adding"]
    assert rig.manager.admit(AIR1, "C5 E4F5")
    assert rig.manager.joining()[0]["adding"]
    rig.settle(2)
    assert rig.hub.admitted.get(AIR1) is not None and rig.airs[AIR1].hub == HUB
    assert saved[-1][-1] == {"id": AIR1, "name": "C5 E4F5", "ble": False, "wireless": True}
    assert [join["id"] for join in rig.manager.joining()] == [AIR2]
    node = rig.manager.node(AIR1)
    assert node["wireless"] and node["connected"] and node["firmware"] == "1.1.0" and node["error"] == ""
    rig.airs[AIR2].reach = False
    rig.settle(nodes.JOIN_SECONDS)
    assert rig.manager.joining() == [] and rig.manager.admit(AIR2, "Late")
    rig.settle(2)
    assert AIR2 not in [entry["id"] for entry in saved[-1]] and rig.manager.hub()["note"] == nodes.ADMIT_ERRORS["node"]
    assert rig.manager.pair(0)
    rig.settle()
    assert rig.hub.hub_commands[-1] == ("PAIR", "0") and rig.manager.hub()["pairing"] == 0


def test_removing_a_wireless_node_makes_the_hub_forget_it_now_or_when_it_is_back(tmp_path):
    rig = hub_rig(tmp_path)
    rig.manager.scan()
    rig.settle()
    rig.manager.remove(AIR1)
    rig.settle()
    assert ("FORGET", AIR1) in rig.hub.hub_commands and rig.airs[AIR1].hub is None
    assert [node["id"] for node in rig.manager.known()] == [ONE, HUB, AIR2] and AIR1 not in rig.manager.air
    rig.hub.unplug()
    rig.settle()
    rig.manager.remove(AIR2)
    assert rig.airs[AIR2].hub == HUB
    rig.hub.replug()
    rig.clock.advance(nodes.NODE_RETRY_SECONDS)
    rig.manager.scan()
    rig.settle(6)
    assert ("FORGET", AIR2) in rig.hub.hub_commands and rig.airs[AIR2].hub is None and rig.hub.admitted == {}
    assert rig.hub.hub_commands.count(("FORGET", AIR2)) == 1


def test_a_wireless_line_with_a_wrong_checksum_is_counted_on_its_node_and_a_late_one_left_out(tmp_path):
    rig = hub_rig(tmp_path)
    rig.manager.scan()
    heard = []
    rig.manager.attach(rig, lambda networks, adapter: heard.extend(net["ssid"] for net in networks))
    rig.settle(4)
    air = rig.airs[AIR1]
    channel = planner.share_of(rig.manager.plans[AIR1])[0][1]
    rig.hub.garble_air = 1
    air.wifi("aa:bb:cc:00:00:01", b"Garbled", channel=channel)
    air.lose = 2
    air.wifi("aa:bb:cc:00:00:02", b"Lost", channel=channel)
    air.wifi("aa:bb:cc:00:00:03", b"Lost", channel=channel)
    air.wifi("aa:bb:cc:00:00:04", b"Old", channel=channel, age=nodes.AIR_LATE_MS + 1)
    air.wifi("aa:bb:cc:00:00:05", b"Fine", channel=channel)
    rig.settle()
    node = rig.manager.node(AIR1)
    assert heard == ["Fine"] and node["bad"] == 1 and node["lost"] == 3 and node["late"] == 1
    assert rig.manager.node(HUB)["bad"] == 0 and rig.manager.node(AIR2)["bad"] == 0
    rig.hub.out += b"@R,b0b1c2d3e4ff,@W,1,2*00\n@R,zz*00\n"
    rig.settle()
    assert rig.manager.links[HUB].bad == 2 and rig.manager.node(AIR1)["bad"] == 1


def test_a_wireless_node_out_of_reach_hands_its_channels_back_and_gets_them_again(tmp_path):
    rig = hub_rig(tmp_path)
    rig.manager.scan()
    wardrive = drive(rig, tmp_path)
    wardrive.start()
    rig.settle(4)
    first = shares(rig)
    rig.airs[AIR1].reach = False
    rig.settle(nodes.AIR_SILENT_SECONDS - 6)
    assert AIR1 in rig.manager.in_use()
    rig.settle(12)
    assert rig.manager.in_use() == [ONE, AIR2] and AIR1 not in rig.manager.plans
    assert sorted(item for share in shares(rig).values() for item in set(share)) == sorted(planner.channels("US"))
    assert rig.manager.node(AIR1)["error"] == nodes.AWAY and not rig.manager.node(AIR1)["connected"]
    errors = {a["label"]: a["error"] for a in wardrive.status()["adapters"] if a["method"] == "node"}
    assert errors == {"Node N0": "", "Node A0": nodes.AWAY, "Node A1": ""}
    rig.airs[AIR1].reach = True
    rig.settle(4)
    assert rig.manager.in_use() == [ONE, AIR1, AIR2] and shares(rig) == first
    assert rig.airs[AIR1].plan["version"] == rig.manager.plans[AIR1].version and rig.airs[AIR1].running
    wardrive.stop()


def test_an_unplugged_hub_stops_the_wireless_share_and_the_usb_nodes_take_it(tmp_path):
    rig = hub_rig(tmp_path)
    rig.manager.scan()
    wardrive = drive(rig, tmp_path)
    wardrive.start()
    rig.settle(4)
    rig.hub.unplug()
    rig.settle()
    one = rig.devices["ttyACM0"]
    assert rig.manager.in_use() == [ONE] and planner.share_of(rig.manager.plans[ONE]) == planner.channels("US")
    assert one.scans(149) and one.scans(6) and one.plan["version"] == rig.manager.plans[ONE].version
    assert rig.manager.node(AIR1)["error"] == nodes.NO_HUB and rig.manager.node(AIR1)["last_heard"] is not None
    assert not rig.manager.hub()["connected"] and not rig.manager.hub()["ready"]
    assert rig.manager.admit(AIR1) is False and rig.manager.pair() is False and not rig.manager.identify(AIR1)
    rig.hub.replug()
    rig.clock.advance(nodes.NODE_RETRY_SECONDS)
    rig.manager.scan()
    rig.settle(6)
    assert rig.manager.in_use() == [ONE, AIR1, AIR2] and rig.hub.running and rig.hub.role == "hub"
    assert all(air.running for air in rig.airs.values())
    wardrive.stop()


def test_a_wireless_node_plugged_in_by_usb_is_the_usb_node_and_the_hub_is_told_nothing(tmp_path):
    rig = hub_rig(tmp_path)
    rig.manager.scan()
    heard = []
    rig.manager.attach(rig, lambda networks, adapter: heard.append((adapter["label"], networks[0]["ssid"])))
    rig.settle(4)
    told = len(rig.hub.hub_commands)
    _, dev, _ = usb_serial(tmp_path, {"ttyACM2": ("303a:1001", "")})
    usb = rig.bus.add(dev / "ttyACM2", FakeNode(AIR1, clock=rig.clock, caps="wifi24+wifi5+ble+espnow"))
    rig.manager.scan()
    rig.settle(6)
    assert rig.manager.in_use() == [ONE, AIR1, AIR2] and usb.running and usb.plan is not None
    assert rig.manager.links[AIR1].path == str(dev / "ttyACM2") and rig.manager.node(AIR1)["port"]
    assert not [command for command in rig.hub.hub_commands[told:] if AIR1 in command]
    air, before = rig.airs[AIR1], rig.manager.air[AIR1].heard
    air.plan, air.running = dict(usb.plan), True
    air.wifi("aa:bb:cc:00:00:07", b"Twice", channel=planner.share_of(rig.manager.plans[AIR1])[0][1])
    rig.settle()
    assert heard == [] and rig.manager.air[AIR1].heard == before and rig.manager.air[AIR1].seq is not None
    assert usb.wifi("aa:bb:cc:00:00:08", b"Once", channel=planner.share_of(rig.manager.plans[AIR1])[0][1])
    rig.settle()
    assert heard == [("Node A0", "Once")] and rig.manager.found() == []


def test_commands_for_a_wireless_node_go_through_the_hub(tmp_path):
    rig = hub_rig(tmp_path)
    rig.manager.scan()
    rig.settle()
    assert rig.manager.identify(AIR1, 3)
    rig.manager.set_led(AIR1, "gpio,27,1")
    rig.settle(3)
    assert rig.airs[AIR1].idents == [3] and rig.airs[AIR1].led == ["gpio", "27", "1"]
    assert ("TO", AIR1, "IDENT", "3") in rig.hub.hub_commands and rig.airs[AIR2].idents == []


def test_a_hub_that_restarted_is_made_a_hub_again(tmp_path):
    rig = hub_rig(tmp_path)
    rig.manager.scan()
    rig.manager.attach(rig, lambda networks, adapter: None)
    rig.settle(4)
    rig.hub.reset()
    assert rig.hub.role == "usb"
    for _ in range(int(nodes.HELLO_SECONDS) + 2):
        if rig.hub.hub_commands.count(("HUB", "6", KEY)) == 2:
            break
        rig.settle(1)
    rig.settle(1)
    assert rig.hub.role == "hub" and rig.hub.hub_commands.count(("HUB", "6", KEY)) == 2 and rig.hub.running
    rig.settle(4)
    assert rig.manager.in_use() == [ONE, AIR1, AIR2]


def test_a_plan_the_hub_could_not_queue_is_sent_again(tmp_path):
    rig = hub_rig(tmp_path, airs=(AIR1,))
    rig.manager.scan()
    rig.settle()
    air = rig.airs[AIR1]
    air.reach = False
    for _ in range(4):
        assert rig.manager.identify(AIR1)
    rig.manager.attach(rig, lambda networks, adapter: None)
    rig.settle()
    assert rig.hub.queues[AIR1] and len(rig.hub.queues[AIR1]) == 4 and air.plan is None
    air.reach = True
    rig.settle(nodes.AIR_NUDGE_SECONDS + 4)
    assert air.plan is not None and air.plan["version"] == rig.manager.plans[AIR1].version


def test_a_node_the_hub_took_is_added_even_when_the_answer_was_garbled(tmp_path):
    saved = []
    rig = hub_rig(tmp_path, airs=(AIR1,), paired=False, on_change=saved.append)
    rig.manager.scan()
    rig.settle()
    rig.manager.pair()
    rig.settle(2)
    rig.manager.admit(AIR1, "Bow")
    rig.hub.garble = 1
    rig.settle(6)
    assert rig.hub.admitted.get(AIR1) is not None and rig.manager.links[HUB].bad == 1
    assert saved[-1][-1] == {"id": AIR1, "name": "Bow", "ble": False, "wireless": True}
    assert ("FORGET", AIR1) not in rig.hub.hub_commands and rig.manager.node(AIR1)["connected"]


def test_wireless_nodes_of_another_hub_say_they_must_be_added_again_and_keep_their_names(tmp_path):
    saved = []
    rig = hub_rig(tmp_path, on_change=saved.append)
    second = FakeHub(TWO, airs=list(rig.airs.values()))
    _, dev, _ = usb_serial(tmp_path, {"ttyACM2": ("303a:1001", "")})
    rig.bus.add(dev / "ttyACM2", second).clock = rig.clock
    second.last_host = second.last_status = second.last_hello = rig.clock()
    rig.manager.scan()
    rig.manager.add(TWO, "Spare")
    rig.settle()
    assert rig.manager.stranded(TWO) == 2 and rig.manager.stranded(HUB) == 0
    rig.manager.set_hub(TWO, True)
    rig.settle(4)
    assert second.role == "hub" and second.admitted == {} and rig.manager.node(AIR1)["error"] == nodes.AWAY
    rig.settle(8)
    assert rig.manager.node(AIR1)["error"] == nodes.NOT_ON_HUB and rig.manager.node(AIR2)["error"] == nodes.NOT_ON_HUB
    assert not [command for command in second.hub_commands if command[0] == "FORGET"]
    rig.airs[AIR1].hub = None
    rig.manager.pair()
    rig.settle(2)
    assert rig.manager.admit(AIR1)
    rig.settle(3)
    node = rig.manager.node(AIR1)
    assert node["error"] == "" and node["connected"] and node["name"] == "A0"
    assert [entry["name"] for entry in saved[-1] if entry.get("wireless")] == ["A0", "A1"]
    rig.settle(12)
    assert rig.manager.node(AIR1)["error"] == "" and rig.manager.node(AIR2)["error"] == nodes.NOT_ON_HUB


def test_a_refused_admit_never_makes_the_hub_forget_a_node_it_took(tmp_path):
    saved = []
    rig = hub_rig(tmp_path, paired=False, on_change=saved.append)
    rig.manager.scan()
    rig.settle()
    rig.manager.pair()
    rig.settle(2)
    rig.airs[AIR1].reach = rig.airs[AIR2].reach = False
    rig.manager.admit(AIR1, "Bow")
    rig.hub.garble = 1
    rig.manager.pump()
    rig.hub.asked.pop(AIR2)
    rig.manager.admit(AIR2, "Stern")
    rig.manager.pump()
    assert rig.manager.hub()["note"] == nodes.ADMIT_ERRORS["node"] and rig.hub.admitted == {AIR1: None}
    rig.airs[AIR1].reach = True
    rig.settle(6)
    assert ("FORGET", AIR1) not in rig.hub.hub_commands and rig.hub.admitted.get(AIR1) is not None
    assert {"id": AIR1, "name": "Bow", "ble": False, "wireless": True} in saved[-1]
    assert rig.manager.node(AIR1)["connected"]


def test_a_refused_admit_stops_showing_the_node_as_being_added(tmp_path):
    rig = hub_rig(tmp_path, airs=(AIR1,), paired=False)
    rig.manager.scan()
    rig.settle()
    rig.manager.pair()
    rig.settle(2)
    rig.airs[AIR1].reach = False
    rig.hub.asked.clear()
    rig.manager.admit(AIR1, "Bow")
    assert rig.manager.joining()[0]["adding"]
    rig.settle()
    assert not rig.manager.joining()[0]["adding"]


def test_a_hub_that_lost_its_key_gets_a_new_one_and_an_upper_case_key_is_taken(tmp_path):
    keys = []
    rig = hub_rig(tmp_path, airs=(), paired=False, key=None)
    rig.manager.on_key = keys.append
    rig.manager.scan()
    rig.settle(2)
    assert len(keys) == 1 and rig.manager.hub()["ready"] and rig.hub.hub_commands[0] == ("HUB", "6", keys[0])
    assert rig.manager.node(HUB)["error"] == ""
    rig.manager.configure({"country": "US", "nodes": rig.manager.entries(), "cluster_key": KEY.upper()})
    assert rig.manager.key == KEY and config.cluster_key(" %s " % KEY.upper()) == KEY


def test_a_hub_that_is_restarting_says_so_when_it_refuses_a_node(tmp_path):
    rig = hub_rig(tmp_path, airs=(AIR1,), paired=False)
    rig.manager.scan()
    rig.settle()
    rig.manager.pair()
    rig.settle(2)
    rig.hub.role = "usb"
    rig.manager.admit(AIR1, "Bow")
    rig.manager.pump()
    assert rig.manager.hub()["note"] == "The hub is restarting. Try again."


def test_a_wireless_node_is_given_time_to_take_its_plan_before_it_is_sent_again(tmp_path):
    rig = hub_rig(tmp_path, airs=(AIR1,))
    rig.manager.scan()
    rig.settle()
    air, plans = rig.airs[AIR1], []
    taken = air.receive

    def receive(data):
        if data.startswith(b"#PLAN"):
            plans.append(data)
            if len(plans) == 1:
                return
        taken(data)
    air.receive = receive
    rig.manager.attach(rig, lambda networks, adapter: None)
    rig.settle(nodes.AIR_NUDGE_SECONDS - 2)
    assert len(plans) == 1 and air.running and air.plan["version"] == 0
    rig.settle(4)
    assert len(plans) == 2 and air.plan["version"] == rig.manager.plans[AIR1].version


def test_a_hello_relayed_with_another_nodes_id_is_counted_as_bad(tmp_path):
    rig = hub_rig(tmp_path, airs=(AIR1,))
    rig.manager.scan()
    rig.settle()
    rig.hub.relay(AIR1, "@H,1,%s,9.9.9,1.0,8,0,c5,wifi24,US,0,idle" % AIR2)
    rig.manager.pump()
    node = rig.manager.node(AIR1)
    assert rig.manager.air[AIR1].bad == 1 and node["firmware"] == "1.1.0" and node["capabilities"] != ["wifi24"]


def test_a_node_on_usb_never_shows_as_a_new_wireless_node(tmp_path):
    rig = hub_rig(tmp_path, airs=(AIR1,), paired=False)
    rig.manager.scan()
    rig.settle()
    _, dev, _ = usb_serial(tmp_path, {"ttyACM2": ("303a:1001", "")})
    usb = rig.bus.add(dev / "ttyACM2", FakeNode(AIR1, clock=rig.clock, caps="wifi24+wifi5+ble+espnow"))
    rig.manager.scan()
    rig.settle()
    rig.manager.pair()
    rig.settle(2)
    assert rig.hub.told and rig.manager.joining() == []
    usb.unplug()
    rig.settle(1)
    assert AIR1 not in rig.manager.links and rig.manager.joining() == []
    rig.settle(5)
    assert [join["id"] for join in rig.manager.joining()] == [AIR1]
    usb.replug()
    rig.clock.advance(nodes.NODE_RETRY_SECONDS)
    rig.manager.scan()
    rig.settle()
    assert AIR1 in rig.manager.links and rig.manager.joining() == []


def test_a_hub_taken_out_of_the_settings_stops_being_one_and_stops_at_once_with_the_scan(tmp_path):
    rig = hub_rig(tmp_path)
    rig.manager.scan()
    rig.manager.attach(rig, lambda networks, adapter: None)
    rig.settle(4)
    assert rig.hub.running
    before = len(rig.hub.commands)
    rig.manager.detach(rig)
    rig.manager.pump()
    assert "STOP" in rig.hub.commands[before:] and not rig.hub.running
    rig.manager.remove(HUB)
    rig.manager.pump()
    assert rig.hub.hub_commands[-1] == ("USB",) and rig.hub.role == "usb"


def test_a_node_the_settings_do_not_have_is_forgotten_once_a_minute_at_most(tmp_path):
    rig = hub_rig(tmp_path, airs=(AIR1,), paired=False)
    rig.hub.saved_key, rig.hub.admitted[AIR1], rig.airs[AIR1].reach = KEY, None, False
    rig.manager.scan()
    rig.settle(2)
    told = [command for command in rig.hub.hub_commands if command == ("FORGET", AIR1)]
    assert len(told) == 1
    rig.hub.admitted[AIR1] = None
    rig.settle(nodes.FORGET_SECONDS - 10)
    assert rig.hub.hub_commands.count(("FORGET", AIR1)) == 1
    rig.settle(12)
    assert rig.hub.hub_commands.count(("FORGET", AIR1)) == 2
