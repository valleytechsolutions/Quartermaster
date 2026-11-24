import functools
import os
import time

import pytest

from quartermaster import config, deepscan, drive_watch, powerguard, wigle
from quartermaster.engine import Wardrive
from quartermaster.powerguard import PowerGuard
from quartermaster.wifi import WifiFleet
from tests.test_core import FIX, NOW
from tests.test_deepscan import FakeClient
from tests.test_engine import FakeFleet, FakeGPS

ALFA = {"interface": "wlan1", "external": True, "bands": ["2.4", "5", "6"], "usb_id": "0e8d:7961",
        "driver": "mt7921u", "phy": "phy1"}
AC1200 = {"interface": "wlan2", "external": True, "bands": ["2.4", "5"], "usb_id": "0bda:8812",
          "driver": "rtw_8812au", "phy": "phy2"}
BUILTIN = {"interface": "wlan0", "external": False, "bands": ["2.4"], "phy": "phy0"}
FLEET = [ALFA, AC1200, BUILTIN]


class Clock:
    def __init__(self, now=1000.0):
        self.now = now

    def __call__(self):
        return self.now

    def tick(self, seconds=4.0):
        self.now += seconds


def device(root, port, usb_id, devnum="2", product="", hub=None, wifi=False, tty=None):
    node = root / port
    node.mkdir(parents=True, exist_ok=True)
    vendor, product_id = usb_id.split(":")
    for name, value in (("idVendor", vendor), ("idProduct", product_id), ("devnum", devnum), ("product", product),
                        ("bDeviceClass", "09" if hub is not None else "00")):
        (node / name).write_text(value + "\n")
    if hub is not None:
        (node / "bmAttributes").write_text(hub + "\n")
    if wifi:
        (node / (port + ":1.0") / "ieee80211" / "phy9").mkdir(parents=True, exist_ok=True)
    if tty:
        (node / (port + ":1.0") / tty).mkdir(parents=True, exist_ok=True)
    return node


def owner_rig(root):
    """The owner's CardputerZero: an unpowered hub with a node, an RTL-SDR and the ALFA, and a USB GPS."""
    usb = root / "usb"
    device(usb, "usb1", "1d6b:0002", devnum="1", hub="e0")
    device(usb, "1-1", "0424:2514", devnum="2", hub="e0")
    device(usb, "1-1.2", "214b:7260", devnum="3", product="USB2.0 HUB", hub="e0")
    device(usb, "1-1.2.2", "303a:1001", devnum="4", product="Espressif ESP32-C5")
    device(usb, "1-1.2.3", "0bda:2838", devnum="5", product="RTL2838UHIDIR")
    device(usb, "1-1.2.4", "0e8d:7961", devnum="6", product="Wireless_Device", wifi=True)
    device(usb, "1-1.3", "10c4:ea60", devnum="7", product="CP2102 USB to UART", tty="ttyUSB0")
    return usb


def guard_for(root, clock, usb=None):
    for name in ("hwmon", "supplies", "net"):
        (root / name).mkdir(exist_ok=True)
    return PowerGuard(sys_usb=usb if usb is not None else root / "usb", sys_net=root / "net", hwmon=root / "hwmon",
                      supplies=root / "supplies", kmsg=root / "no-kmsg", clock=clock)


def alarm(root, on=True):
    sensor = root / "hwmon" / "hwmon0"
    sensor.mkdir(parents=True, exist_ok=True)
    (sensor / "in0_lcrit_alarm").write_text("1\n" if on else "0\n")


def test_the_estimate_counts_the_unpowered_hub_the_port_the_sdr_and_the_node(tmp_path):
    load = powerguard.usb_load(owner_rig(tmp_path))
    kinds = {d["port"]: d["kind"] for d in load["devices"]}
    assert kinds == {"1-1.2.2": "node", "1-1.2.3": "sdr", "1-1.2.4": "wifi", "1-1.3": "gps"}
    hub = load["hubs"]["1-1.2"]
    assert not hub["powered"] and hub["ma"] == 600 + 300 + 150 and hub["over"] and hub["nodes"] == 1
    assert "1-1" not in load["hubs"]
    assert load["ma"] == 600 + 300 + 150 + 50 + 50 and load["over"] and load["budget"] == powerguard.PORT_MA
    cards = powerguard.load_cards(load)
    assert cards[0][0] == "Too many adapters for USB power" and "Use a powered USB hub" in cards[0][1]
    assert any("Unpowered hub" in title for title, _, _ in cards)
    assert any("own battery" in detail and "wirelessly" in detail for _, detail, _ in cards)


def test_a_hub_with_its_own_power_carries_its_devices_and_a_known_bus_powered_one_does_not(tmp_path):
    usb = tmp_path / "usb"
    device(usb, "1-1", "0424:2514", hub="e0")
    device(usb, "1-1.2", "05e3:0610", hub="e0", product="Powered hub")
    device(usb, "1-1.2.1", "0e8d:7961", wifi=True)
    device(usb, "1-1.2.2", "0bda:8812")
    load = powerguard.usb_load(usb)
    assert load["ma"] == 0 and not load["over"] and all(d["powered"] for d in load["devices"])
    (usb / "1-1.2" / "bmAttributes").write_text("a0\n")
    load = powerguard.usb_load(usb)
    assert load["hubs"]["1-1.2"]["ma"] == 1100 and load["over"] and load["ma"] == 1150
    (usb / "1-1.2" / "bmAttributes").write_text("e0\n")
    (usb / "1-1.2" / "idVendor").write_text("214b\n")
    (usb / "1-1.2" / "idProduct").write_text("7260\n")
    assert powerguard.usb_load(usb)["over"]


def test_one_adapter_and_a_gps_fit_and_start_at_once(tmp_path):
    usb = tmp_path / "usb"
    device(usb, "1-1", "0424:2514", hub="e0")
    device(usb, "1-1.2", "0e8d:7961", wifi=True)
    device(usb, "1-1.3", "10c4:ea60", tty="ttyUSB0")
    guard = guard_for(tmp_path, Clock())
    assert guard.start([ALFA, BUILTIN]) == {"stagger": 0.0, "sky": 0.0, "nodes": 0.0}
    assert guard.notice is None and not guard.load["over"]
    title, detail, problem = powerguard.load_cards(guard.load)[0]
    assert title == "USB power: about 650 of 1,000 mA" and "Estimate" in detail and not problem
    assert powerguard.load_cards({"devices": []}) == [] and powerguard.load_cards(None) == []


def test_adapters_the_sdr_and_nodes_start_one_and_a_half_seconds_apart(tmp_path):
    usb = owner_rig(tmp_path)
    device(usb, "1-1.2.1", "0bda:8812")
    guard = guard_for(tmp_path, Clock())
    plan = guard.start([ALFA, AC1200, BUILTIN])
    assert plan == {"stagger": 1.5, "sky": 3.0, "nodes": 4.5}
    assert guard.notice["kind"] == "over" and "powered hub" in guard.notice["text"]


def test_no_trouble_means_no_steps_and_nothing_eased(tmp_path):
    clock = Clock()
    usb = owner_rig(tmp_path)
    guard = guard_for(tmp_path, clock)
    guard.start(FLEET)
    for _ in range(200):
        clock.tick()
        assert guard.check(FLEET, tuned=["wlan1", "wlan2"]) == []
    status = guard.status()
    assert status["level"] == 0 and status["parked"] == [] and status["troubles"] == 0
    assert guard.spaced == set() and guard.untuned == set() and guard.notice["kind"] == "over"
    device(usb, "1-1.5", "0bda:b812")
    clock.tick()
    assert guard.check(FLEET) == []
    (usb / "1-1.5" / "devnum").unlink()
    clock.tick()
    assert guard.check(FLEET) == []


def test_easing_goes_untune_then_space_then_park_and_only_while_trouble_is_seen(tmp_path):
    clock = Clock()
    owner_rig(tmp_path)
    guard = guard_for(tmp_path, clock)
    guard.start(FLEET)
    alarm(tmp_path)
    clock.tick()
    assert guard.check(FLEET, tuned=["wlan1", "wlan2"]) == [("untune", "wlan2")]
    clock.tick()
    assert guard.check(FLEET, tuned=["wlan1"]) == []
    clock.tick(powerguard.SETTLE)
    assert guard.check(FLEET, tuned=["wlan1"]) == [("space", "wlan2", powerguard.SPACE)]
    alarm(tmp_path, on=False)
    for _ in range(10):
        clock.tick(powerguard.SETTLE)
        assert guard.check(FLEET, tuned=["wlan1"]) == []
    alarm(tmp_path)
    clock.tick()
    assert guard.check(FLEET, tuned=["wlan1"]) == [("park", "wlan2")]
    clock.tick(powerguard.SETTLE)
    assert guard.check(FLEET, tuned=["wlan1"]) == []
    assert guard.parked == ["wlan2"] and "wlan1" not in guard.untuned and guard.level == 3


def test_without_radio_tuning_the_first_step_spaces_the_sweeps(tmp_path):
    clock = Clock()
    guard = guard_for(tmp_path, clock, usb=owner_rig(tmp_path))
    guard.start(FLEET)
    alarm(tmp_path)
    clock.tick()
    assert guard.check(FLEET) == [("space", "wlan2", powerguard.SPACE)]
    clock.tick(powerguard.SETTLE)
    assert guard.check(FLEET, can_space=False) == [("park", "wlan2")]


def test_the_last_6_ghz_radio_is_kept_and_the_one_that_adds_least_is_parked(tmp_path):
    clock = Clock()
    guard = guard_for(tmp_path, clock, usb=tmp_path / "none")
    six_only = dict(ALFA, interface="wlan3", bands=["6"])
    fives = dict(AC1200, interface="wlan4", bands=["5"])
    adapters = [six_only, fives, BUILTIN]
    guard.start(adapters)
    assert guard._pick(adapters, (), None) == "wlan4"
    two = [ALFA, dict(ALFA, interface="wlan5"), AC1200]
    assert guard._pick(two, (), None) == "wlan2"
    guard.parked.append("wlan2")
    assert guard._pick(two, (), None) in ("wlan1", "wlan5")
    alone = [ALFA, BUILTIN]
    assert guard._pick(alone, (), None) is None
    hungrier = guard._pick([dict(ALFA, interface="wlan6"), dict(AC1200, interface="wlan7", bands=["2.4", "5", "6"])],
                           ("wifi24", "wifi5"), None)
    assert hungrier == "wlan6"


def test_the_gps_is_never_eased_even_when_it_is_the_device_that_drops(tmp_path):
    clock = Clock()
    usb = owner_rig(tmp_path)
    guard = guard_for(tmp_path, clock)
    guard.start(FLEET)
    seen = []
    for devnum in ("8", "9", "10", "11", "12", "13"):
        (usb / "1-1.3" / "devnum").write_text(devnum + "\n")
        clock.tick(powerguard.SETTLE)
        seen += guard.check(FLEET, tuned=["wlan1", "wlan2"])
    assert seen == [("untune", "wlan2"), ("space", "wlan2", powerguard.SPACE), ("park", "wlan2")]
    assert all(action[1] in ("wlan2",) for action in seen) and guard.reason == "CP2102 USB to UART started again"


def test_one_replug_by_hand_is_not_trouble_but_the_alfa_starting_again_twice_is(tmp_path):
    clock = Clock()
    usb = owner_rig(tmp_path)
    guard = guard_for(tmp_path, clock)
    guard.start(FLEET)
    (usb / "1-1.2.4" / "devnum").write_text("20\n")
    clock.tick()
    assert guard.check(FLEET) == [] and guard.troubles == 0
    clock.tick(powerguard.WINDOW + 1)
    assert guard.check(FLEET) == []
    (usb / "1-1.2.4" / "devnum").unlink()
    clock.tick()
    assert guard.check(FLEET) == []
    (usb / "1-1.2.4" / "devnum").write_text("21\n")
    clock.tick()
    assert guard.check(FLEET) == []
    (usb / "1-1.2.4" / "devnum").write_text("22\n")
    clock.tick()
    assert guard.check(FLEET) == [("space", "wlan2", powerguard.SPACE)] and guard.troubles == 1


def test_a_hub_that_drops_off_after_its_devices_failed_to_start_is_a_brownout(tmp_path):
    kmsg = tmp_path / "kmsg"
    kmsg.write_text("")
    clock = Clock()
    usb = owner_rig(tmp_path)
    guard = guard_for(tmp_path, clock)
    guard.kmsg_path = kmsg
    guard.start(FLEET)
    with open(kmsg, "a") as out:
        out.write("3,2,2,-;usb 1-1.2.3: device descriptor read/64, error -110\n")
    clock.tick()
    assert guard.check(FLEET) == [] and guard.reason == "RTL2838UHIDIR could not start"
    for port in ("1-1.2", "1-1.2.2", "1-1.2.3", "1-1.2.4"):
        (usb / port / "devnum").unlink()
    clock.tick()
    assert guard.check(FLEET) == [("space", "wlan2", powerguard.SPACE)]
    assert guard.reason == "The USB hub dropped off with its devices"


def link(root, iface, port):
    (root / "net" / iface).mkdir(parents=True, exist_ok=True)
    (root / "usb" / port / (port + ":1.0")).mkdir(parents=True, exist_ok=True)
    os.symlink(root / "usb" / port / (port + ":1.0"), root / "net" / iface / "device")


def test_reseating_a_hub_by_hand_is_not_trouble(tmp_path):
    clock = Clock()
    usb = owner_rig(tmp_path)
    device(usb, "1-1.2.1", "0bda:8812")
    guard = guard_for(tmp_path, clock)
    link(tmp_path, "wlan1", "1-1.2.4")
    link(tmp_path, "wlan2", "1-1.2.1")
    guard.start(FLEET)
    numbers = {}
    for port in ("1-1.2", "1-1.2.1", "1-1.2.2", "1-1.2.3", "1-1.2.4"):
        numbers[port] = (usb / port / "devnum").read_text()
        (usb / port / "devnum").unlink()
    clock.tick()
    assert guard.check(FLEET[2:], tuned=["wlan1", "wlan2"]) == []
    for port, number in numbers.items():
        (usb / port / "devnum").write_text(str(int(number) + 10))
    clock.tick()
    assert guard.check(FLEET, tuned=["wlan1", "wlan2"]) == []
    for _ in range(20):
        clock.tick()
        assert guard.check(FLEET, tuned=["wlan1", "wlan2"]) == []
    assert guard.troubles == 0 and guard.level == 0 and guard.untuned == set() and guard.spaced == set()


def test_power_errors_in_the_kernel_log_are_trouble(tmp_path):
    kmsg = tmp_path / "kmsg"
    kmsg.write_text("6,1,1,-;usb 1-1.2.4: new high-speed USB device number 6\n")
    clock = Clock()
    guard = guard_for(tmp_path, clock, usb=tmp_path / "none")
    guard.kmsg_path = kmsg
    guard.start(FLEET)
    clock.tick()
    assert guard.check(FLEET) == []
    for _ in range(2):
        with open(kmsg, "a") as out:
            out.write("3,2,2,-;usb 1-1.2.3: device descriptor read/64, error -110\n SUBSYSTEM=usb\n")
        clock.tick()
        actions = guard.check(FLEET)
    assert actions == [("space", "wlan2", powerguard.SPACE)]
    assert guard.reason == "A USB device could not start"
    with open(kmsg, "a") as out:
        out.write("3,3,3,-;hwmon hwmon1: Undervoltage detected!\n")
    clock.tick(powerguard.SETTLE)
    assert guard.check(FLEET) == [("park", "wlan2")] and guard.reason == "The Pi reports under-voltage"
    for line in ("usb 1-1.2-port3: Cannot enable. Maybe the USB cable is bad?",
                 "hub 1-1.2:1.0: hub_ext_port_status failed (err = -71)", "hwmon hwmon1: Undervoltage detected!"):
        assert powerguard.GUARD_LOG.search(line), line
    assert not powerguard.GUARD_LOG.search("usb 1-1.2.4: new high-speed USB device number 6 using dwc2")
    guard.stop()
    assert powerguard.Kmsg(tmp_path / "missing").lines() == []


def test_sharp_battery_dips_count_and_charging_does_not(tmp_path):
    clock = Clock()
    guard = guard_for(tmp_path, clock, usb=tmp_path / "none")
    gauge = tmp_path / "supplies" / "bq27220-0"
    gauge.mkdir(parents=True)
    for name, value in (("type", "Battery"), ("status", "Discharging"), ("voltage_now", "3900000")):
        (gauge / name).write_text(value + "\n")
    guard.start(FLEET)
    for volts in ("3850000", "3880000", "3500000", "3880000", "3540000"):
        (gauge / "voltage_now").write_text(volts + "\n")
        clock.tick()
        actions = guard.check(FLEET)
    assert actions == [("space", "wlan2", powerguard.SPACE)] and guard.reason == "Battery voltage dipped under load"
    calm = guard_for(tmp_path, clock, usb=tmp_path / "none")
    (gauge / "status").write_text("Charging\n")
    calm.start(FLEET)
    for volts in ("3900000", "3400000", "3900000", "3400000"):
        (gauge / "voltage_now").write_text(volts + "\n")
        clock.tick()
        assert calm.check(FLEET) == []


def parked_guard(tmp_path, clock):
    guard = guard_for(tmp_path, clock, usb=owner_rig(tmp_path))
    guard.start(FLEET)
    alarm(tmp_path)
    clock.tick()
    guard.check(FLEET, can_space=False)
    alarm(tmp_path, on=False)
    assert guard.parked == ["wlan2"]
    return guard


def test_a_parked_adapter_is_tried_again_once_and_kept_parked_when_trouble_comes_back(tmp_path):
    clock = Clock()
    guard = parked_guard(tmp_path, clock)
    clock.tick(powerguard.RETRY - 10)
    assert guard.check(FLEET) == []
    clock.tick(20)
    assert guard.check(FLEET) == [("unpark", "wlan2")] and guard.parked == []
    alarm(tmp_path)
    clock.tick(30)
    assert guard.check(FLEET) == [("park", "wlan2")]
    alarm(tmp_path, on=False)
    assert guard.parked == ["wlan2"] and guard.status()["permanent"] == ["wlan2"]
    for _ in range(50):
        clock.tick(powerguard.RETRY)
        assert guard.check(FLEET) == []


def test_a_retry_that_holds_keeps_the_adapter_and_a_later_park_is_for_good(tmp_path):
    clock = Clock()
    guard = parked_guard(tmp_path, clock)
    clock.tick(powerguard.RETRY + 1)
    assert guard.check(FLEET) == [("unpark", "wlan2")]
    clock.tick(powerguard.PROBATION + 1)
    assert guard.check(FLEET) == [] and guard.status()["retrying"] is None
    alarm(tmp_path)
    clock.tick(powerguard.SETTLE)
    assert guard.check(FLEET, can_space=False) == [("park", "wlan2")]
    alarm(tmp_path, on=False)
    assert guard.status()["permanent"] == ["wlan2"]
    clock.tick(powerguard.RETRY * 3)
    assert guard.check(FLEET) == []


def test_each_message_is_given_once(tmp_path):
    clock = Clock()
    guard = guard_for(tmp_path, clock, usb=owner_rig(tmp_path))
    guard.start(FLEET + [dict(AC1200, interface="wlan3")])
    alarm(tmp_path)
    kinds = [guard.notice["kind"]]
    for _ in range(30):
        clock.tick(powerguard.SETTLE)
        guard.check(FLEET + [dict(AC1200, interface="wlan3")], tuned=["wlan2", "wlan3"])
        if guard.notice["kind"] != kinds[-1]:
            kinds.append(guard.notice["kind"])
    assert kinds == ["over", "eased", "parked"] and guard.notice["id"] == 3
    assert len(guard.parked) == 2 and "wlan1" not in guard.parked
    assert guard.notice["text"] == "wlan3 paused: not enough USB power"


def test_radio_tuning_is_undone_on_an_eased_adapter_until_it_leaves():
    client = FakeClient()
    deep = deepscan.DeepScan({"radio_tuning": True}, connect=lambda: (client, "ready", ""))
    assert deep.open()
    deep.tune(FLEET)
    assert ("tune", {"iface": "wlan1"}) in client.requests and ("tune", {"iface": "wlan2"}) in client.requests
    assert deep.ease("wlan2") and ("untune", {"iface": "wlan2"}) in client.requests
    assert deep.tuned == ["wlan1"] and deep.tuning["wlan2"] == {"eased": True} and not deep.ease("wlan2")
    before = len(client.requests)
    deep.follow(FLEET)
    assert len(client.requests) == before
    deep.forget("wlan1")
    assert deep.tuned == ["wlan1"]
    deep.forget("wlan2")
    deep.follow(FLEET)
    assert client.requests[-1] == ("tune", {"iface": "wlan2"}) and deep.tuned == ["wlan1", "wlan2"]


def test_the_fleet_staggers_first_sweeps_of_usb_adapters_and_spaces_one_on_request():
    clock = Clock()
    fleet = WifiFleet([], lambda networks, adapter: None, clock=clock)
    assert fleet._delay(ALFA) == 0.0
    fleet.stagger = 1.5
    assert [fleet._delay(a) for a in FLEET] == [0.0, 1.5, 0.0]
    fleet._began = True
    clock.tick(10)
    assert fleet._delay(dict(AC1200, interface="wlan3")) == 1.5
    fleet.space("wlan2", 8.0)
    assert fleet.spacing == {"wlan2": 8.0}


def test_the_fleet_waits_before_the_first_sweep_of_a_staggered_adapter():
    swept = []

    class Scanner:
        def __init__(self, adapter):
            self.adapter, self.interface = adapter, adapter["interface"]
            self.last_error = ""

        def scan(self):
            swept.append((self.interface, time.monotonic()))
            return []

    fleet = WifiFleet([ALFA, AC1200], lambda networks, adapter: None, interval=60.0, scanner=Scanner)
    fleet.stagger = 0.3
    began = time.monotonic()
    fleet.start()
    deadline = time.monotonic() + 5
    while len(swept) < 2 and time.monotonic() < deadline:
        time.sleep(0.01)
    fleet.stop()
    when = dict(swept)
    assert when["wlan1"] - began < 0.25 and when["wlan2"] - began >= 0.3


class Recorder:
    """A Wi-Fi fleet that keeps what the wardrive asks of it."""

    def __init__(self, adapters, sink, interval):
        self.adapters, self.sink = list(adapters), sink
        self.stagger = 0.0
        self.spacing, self.refreshed = {}, []

    def start(self):
        pass

    def refresh(self, adapters):
        self.refreshed.append([a["interface"] for a in adapters])

    def space(self, interface, seconds):
        self.spacing[interface] = seconds

    def stop(self):
        pass

    def status(self):
        return []


def guarded(tmp_path, clock, fleet=Recorder, **cfg):
    usb = owner_rig(tmp_path)
    device(usb, "1-1.2.1", "0bda:8812")
    for name in ("hwmon", "supplies", "net"):
        (tmp_path / name).mkdir(exist_ok=True)
    guard = functools.partial(PowerGuard, sys_usb=usb, sys_net=tmp_path / "net", hwmon=tmp_path / "hwmon",
                              supplies=tmp_path / "supplies", kmsg=tmp_path / "no-kmsg", clock=clock)
    settings = dict({"ble": False, "log_rows": "track", "adsb": False}, **cfg)
    return Wardrive(settings, FakeGPS(FIX), root=tmp_path / "drive", adapters=FLEET, fleet=fleet,
                    clock=lambda: NOW, guard=guard)


def test_a_wardrive_spaces_parks_and_brings_back_an_adapter_through_its_fleet(tmp_path):
    clock = Clock()
    drive = guarded(tmp_path, clock)
    drive.start()
    assert drive.fleet.stagger == 1.5 and drive.status()["power"]["load"]["over"]
    alarm(tmp_path)
    clock.tick()
    drive._guard_power()
    assert drive.fleet.spacing == {"wlan2": powerguard.SPACE}
    clock.tick(powerguard.SETTLE)
    drive._guard_power()
    assert drive.fleet.refreshed == [["wlan1", "wlan0"]] and drive.status()["power"]["parked"] == ["wlan2"]
    alarm(tmp_path, on=False)
    clock.tick(powerguard.RETRY + 1)
    drive._guard_power()
    assert drive.fleet.refreshed[-1] == ["wlan1", "wlan2", "wlan0"]
    drive.stop()


def test_power_guard_off_leaves_everything_as_it_was(tmp_path):
    clock = Clock()
    drive = guarded(tmp_path, clock, power_guard=False)
    drive.start()
    alarm(tmp_path)
    clock.tick()
    drive._guard_power()
    assert drive.guard is None and drive.status()["power"] is None and drive.fleet.stagger == 0.0
    assert drive.fleet.spacing == {} and drive.fleet.refreshed == []
    drive.stop()
    assert config.DEFAULTS["power_guard"] is True and config.shown("power_guard", True) == "On"


def test_with_power_fine_the_wigle_log_is_exactly_the_same_with_and_without_the_guard(tmp_path):
    logs = []
    for name, cfg in (("on", {}), ("off", {"power_guard": False})):
        clock = Clock()
        drive = guarded(tmp_path / name, clock, fleet=FakeFleet, **cfg)
        drive.start()
        for _ in range(30):
            clock.tick()
            drive._guard_power()
        status = drive.status()
        drive.stop()
        logs.append((drive.log_path.name, drive.log_path.read_bytes(), status["wifi"], status["rows"]))
        assert (status["power"] is None) == (name == "off")
        if status["power"] is not None:
            assert status["power"]["troubles"] == 0 and status["power"]["parked"] == []
    assert logs[0] == logs[1] and logs[0][2] == 3


def test_the_aircraft_radio_and_nodes_wait_their_turn(tmp_path, monkeypatch):
    clock = Clock()
    started = []
    monkeypatch.setattr(Wardrive, "_listen_sky", lambda self: started.append(("sky", time.monotonic())))
    monkeypatch.setattr(Wardrive, "_attach_nodes", lambda self: started.append(("nodes", time.monotonic())))
    monkeypatch.setattr(powerguard, "STAGGER", 0.05)
    drive = guarded(tmp_path, clock, adsb=True)
    drive.nodes = type("Nodes", (), {"detach": lambda self, owner: None, "wifi_bands": lambda self: ()})()
    began = time.monotonic()
    drive.start()
    assert started == [] and drive._sky_waiting
    deadline = time.monotonic() + 5
    while len(started) < 2 and time.monotonic() < deadline:
        time.sleep(0.01)
    drive.stop()
    assert [kind for kind, _ in started] == ["sky", "nodes"]
    assert started[0][1] - began >= 0.1 and started[1][1] - began >= 0.15 and not drive._sky_waiting


def test_a_stop_before_their_turn_starts_neither(tmp_path, monkeypatch):
    started = []
    monkeypatch.setattr(Wardrive, "_listen_sky", lambda self: started.append("sky"))
    drive = guarded(tmp_path, Clock(), adsb=True)
    drive.start()
    drive.stop()
    time.sleep(0.1)
    assert started == []


def test_alert_logs_reach_the_disk_every_few_seconds(tmp_path, monkeypatch):
    synced = []
    monkeypatch.setattr(wigle, "fsync", lambda fd: synced.append(fd))
    watch = drive_watch.DriveWatch(config.load(tmp_path / "none.json"), FakeGPS(FIX), tmp_path, clock=lambda: NOW)
    watch.log_path = tmp_path / "recon" / "drive.jsonl"
    for _ in range(5):
        watch._write({"time": NOW})
    assert len(synced) == 1 and len(watch.log_path.read_text().splitlines()) == 5
    watch._synced -= wigle.SYNC_SECONDS
    watch._write({"time": NOW})
    assert len(synced) == 2
    watch._log.close()
    watch._log = None


def test_kmsg_records_are_read_one_line_each(tmp_path):
    path = tmp_path / "kmsg"
    path.write_text("")
    reader = powerguard.Kmsg(path)
    with open(path, "a") as out:
        out.write("4,9,9,-;usb 1-1.2.4: USB disconnect, device number 6\n")
    assert reader.lines() == ["usb 1-1.2.4: USB disconnect, device number 6"]
    assert reader.lines() == []
    reader.close()
    assert reader.fd is None and reader.lines() == []
    if os.path.exists("/dev/null"):
        powerguard.Kmsg("/dev/null").close()


@pytest.mark.parametrize("usb_id,driver,expected", [("0e8d:7961", "", (600, 900)), ("", "rtw_8812au", (500, 700)),
                                                     ("0e8d:7612", "", (450, 650)), ("ffff:0001", "x", (500, 700))])
def test_estimates_come_from_the_chip_table(usb_id, driver, expected):
    assert powerguard.adapter_estimate({"external": True, "usb_id": usb_id, "driver": driver}) == expected
    assert powerguard.adapter_estimate({"external": False}) == (0, 0)


def test_the_wardrive_screen_tells_once_and_the_hardware_screen_shows_the_budget(tmp_path, drawn):
    from tests.test_ui import hardware_text, make_app, press, screen_text
    app = make_app(tmp_path)
    app.hw.power = powerguard.usb_load(owner_rig(tmp_path))
    text = hardware_text(app, drawn)
    assert "Too many adapters for USB power" in text and "Unpowered hub: about 1,050 of 900 mA" in text
    app = make_app(tmp_path / "drive")
    press(app, "W")
    toasts = []
    app.toast = lambda text, color=None, seconds=3.0: toasts.append(text)
    guard = app.drive.guard
    guard.notice = {"id": 1, "kind": "eased", "text": powerguard.WORDS["eased"]}
    guard.parked.append("wlan2")
    guard.troubles, guard.reason = 1, "The Pi reports under-voltage"
    for _ in range(5):
        text = screen_text(app, drawn)
    assert toasts == [powerguard.WORDS["eased"]]
    assert "wlan2 paused to save USB power. Use a powered hub." in text
    guard.notice = {"id": 2, "kind": "parked", "text": "wlan2 paused: not enough USB power"}
    screen_text(app, drawn)
    screen_text(app, drawn)
    assert toasts == [powerguard.WORDS["eased"], "wlan2 paused: not enough USB power"]
    text = hardware_text(app, drawn)
    assert "Paused to save USB power" in text and "USB power ran short" in text
    assert "USB power ran short: 1 adapter paused" in text and "The Pi reports under-voltage" in text


def test_settings_offer_power_guard_and_a_powered_hub(tmp_path, drawn):
    help_text = {key: words for key, _, _, words in config.SETTINGS}
    assert "full legal transmit power" in help_text["radio_tuning"]
    assert "USB power" in help_text["power_guard"] and "power supply" in help_text["hub_power"]
    assert config.DEFAULTS["hub_power"] is False
    cfg = config.load(tmp_path / "missing.json")
    assert cfg["power_guard"] is True
    cfg["power_guard"] = False
    config.save(cfg, tmp_path / "config.json")
    assert config.load(tmp_path / "config.json")["power_guard"] is False


def test_with_nothing_to_ease_the_gentle_steps_stay_for_an_adapter_plugged_in_later(tmp_path):
    from quartermaster.ui.screens import Hardware
    clock = Clock()
    guard = guard_for(tmp_path, clock, usb=owner_rig(tmp_path))
    guard.start([ALFA, BUILTIN])
    alarm(tmp_path)
    clock.tick()
    assert guard.check([ALFA, BUILTIN], tuned=["wlan1"]) == []
    status = guard.status()
    assert status["level"] == 0 and status["eased"] == 0 and status["troubles"] == 1
    cards = Hardware.power_cards(None, status)
    assert cards[-1]["title"] == "USB power ran short"
    assert cards[-1]["detail"] == "The Pi reports under-voltage. Use a powered USB hub."
    clock.tick()
    assert guard.check(FLEET, tuned=["wlan1", "wlan2"]) == [("untune", "wlan2")]
    clock.tick(powerguard.SETTLE)
    assert guard.check(FLEET, tuned=["wlan1"]) == [("space", "wlan2", powerguard.SPACE)]
    wlan3 = dict(AC1200, interface="wlan3")
    clock.tick(powerguard.SETTLE)
    assert guard.check(FLEET + [wlan3], tuned=["wlan1", "wlan3"]) == [("untune", "wlan3")]
    assert Hardware.power_cards(None, guard.status())[-1]["title"] == "USB power ran short: adapters eased"


def test_driver_noise_from_an_adapter_unplugged_by_hand_is_not_trouble(tmp_path):
    kmsg = tmp_path / "kmsg"
    kmsg.write_text("")
    clock = Clock()
    usb = owner_rig(tmp_path)
    guard = guard_for(tmp_path, clock)
    guard.kmsg_path = kmsg
    guard.start(FLEET)
    (usb / "1-1.2.4" / "devnum").unlink()
    for line in ("mt7921u 1-1.2.4:1.0: vendor request req:63 off:7c00 failed:-108",
                 "rtw_8822bu 1-1.2.4:1.0: write port txch 2 failed, status: -108",
                 "usb 1-1.2.4: failed to send command: -71"):
        with open(kmsg, "a") as out:
            out.write("3,5,5,-;%s\n" % line)
        clock.tick()
        assert guard.check([AC1200, BUILTIN]) == []
    assert guard.troubles == 0


def test_a_paused_adapter_is_forgotten_when_it_leaves_or_another_takes_its_name(tmp_path):
    clock = Clock()
    guard = parked_guard(tmp_path, clock)
    guard.forget()
    clock.tick()
    assert guard.check([ALFA, BUILTIN]) == [] and guard.parked == ["wlan2"]
    clock.tick(powerguard.REJOIN + 1)
    assert guard.check([ALFA, BUILTIN]) == [] and guard.parked == [] and guard.forget() == ["wlan2"]
    assert guard.forget() == []
    clock.tick()
    assert guard.check(FLEET) == [] and guard.parked == []
    guard = parked_guard(tmp_path / "again", clock)
    guard.forget()
    other = dict(AC1200, usb_id="0bda:b812")
    clock.tick()
    assert guard.check([ALFA, BUILTIN]) == []
    clock.tick()
    assert guard.check([ALFA, other, BUILTIN]) == [] and guard.parked == [] and guard.forget() == ["wlan2"]


def test_a_retry_of_an_adapter_that_has_left_does_not_take_the_next_step(tmp_path):
    clock = Clock()
    guard = parked_guard(tmp_path, clock)
    clock.tick(powerguard.RETRY + 1)
    assert guard.check(FLEET) == [("unpark", "wlan2")]
    wlan3 = dict(AC1200, interface="wlan3")
    adapters = [ALFA, wlan3, BUILTIN]
    clock.tick()
    assert guard.check(adapters) == []
    alarm(tmp_path)
    clock.tick()
    assert guard.check(adapters) == [("space", "wlan3", powerguard.SPACE)]
    assert "wlan2" not in guard.parked and guard.status()["retrying"] is None


def test_a_hub_set_as_powered_in_settings_carries_its_devices(tmp_path):
    usb = owner_rig(tmp_path)
    load = powerguard.usb_load(usb, hubs_powered=True)
    assert load["ma"] == 50 and not load["over"] and load["hubs"]["1-1.2"]["powered"]
    guard = PowerGuard({"hub_power": True}, sys_usb=usb, sys_net=tmp_path / "net", hwmon=tmp_path / "hwmon",
                       supplies=tmp_path / "supplies", kmsg=tmp_path / "no-kmsg", clock=Clock())
    assert guard.start(FLEET) == {"stagger": 0.0, "sky": 0.0, "nodes": 0.0} and guard.notice is None


class Deep:
    """A radio helper session that keeps what the wardrive asks of it."""

    def __init__(self):
        self.tuned, self.calls = ["wlan1", "wlan2"], []

    def ease(self, name):
        self.calls.append(("ease", name))

    def forget(self, name):
        self.calls.append(("forget", name))

    def stop(self):
        pass

    def status(self):
        return None

    def radio_status(self):
        return {}


def test_a_pause_with_the_radio_helper_stops_the_adapter_at_once_and_a_new_adapter_on_its_name_scans(tmp_path):
    clock = Clock()
    drive = guarded(tmp_path, clock)
    drive.start()
    deep = drive.deep = Deep()
    alarm(tmp_path)
    for _ in range(3):
        clock.tick(powerguard.SETTLE)
        drive._guard_power()
    assert deep.calls == [("ease", "wlan2"), ("ease", "wlan2")]
    assert drive.fleet.refreshed == [["wlan1", "wlan0"]] and drive.guard.parked == ["wlan2"]
    alarm(tmp_path, on=False)
    drive._scanning = [ALFA, BUILTIN]
    clock.tick()
    drive._guard_power()
    clock.tick(powerguard.REJOIN + 1)
    drive._guard_power()
    assert deep.calls[-1] == ("forget", "wlan2") and drive.fleet.refreshed[-1] == ["wlan1", "wlan0"]
    assert "wlan2" not in drive.fleet.spacing
    newcomer = dict(AC1200, usb_id="0e8d:7612", driver="mt76x2u")
    assert drive._unparked([ALFA, newcomer, BUILTIN]) == [ALFA, newcomer, BUILTIN]
    drive.deep = None
    drive.stop()
