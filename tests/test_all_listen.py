import random

import pytest

from quartermaster import config, deepscan, planner, powerguard, radio_helper
from quartermaster.engine import Wardrive
from quartermaster.ui import gfx
from tests.fakes import Fleet, Gps, NoBle, networks
from tests.test_core import FIX, NOW
from tests.test_deepscan import AC1200, AC1200_WIFI5, ALFA, BUILTIN, FakeClient, in_thread, on_air, beacon
from tests.test_powerguard import Clock, device, guard_for, link
from tests.test_radiod import ask, radiod

ALL_24 = [2407 + 5 * ch for ch in range(1, 12)]
ALL_5 = [5000 + 5 * ch for _, ch in planner.channels("US") if _ == "5"]
PSC = [5950 + 5 * ch for ch in deepscan.PSC6]
SIX = PSC + [5975, 6015]


def usb(name, bands, driver="mt7921u", label="", **extra):
    return dict({"interface": name, "phy": "phy" + name[-1], "driver": driver, "external": True,
                 "usb_id": "0e8d:7961" if driver == "mt7921u" else "0e8d:7612", "product": label, "label": label,
                 "bands": list(bands), "monitor": True, "type": "1"}, **extra)


def freqs_for(bands):
    out = []
    if "2.4" in bands:
        out += ALL_24
    if "5" in bands:
        out += ALL_5
    if "6" in bands:
        out += SIX
    return out


class MultiClient(FakeClient):
    """The radio helper with as many USB adapters as a test plugs in; each monitor gets its own name."""

    def __init__(self, adapters, fail=()):
        super().__init__(fail)
        self.adapters = adapters
        self.next_vif = 0
        self.live = {}
        self.plans = {}
        self.refuse_start = set()

    def request(self, cmd, timeout=None, **args):
        self.requests.append((cmd, args))
        if cmd in self.fail:
            raise radio_helper.HelperError("%s failed" % cmd)
        if cmd == "adapters":
            return {"ok": True, "adapters": [
                {"interface": a["interface"], "usb": a["external"], "connected": a.get("connected", False),
                 "default_route": False, "monitor": a.get("monitor", True), "freqs": freqs_for(a["bands"])}
                for a in self.adapters]}
        if cmd == "monitor_start":
            if args["iface"] in self.refuse_start:
                raise radio_helper.HelperError("monitor mode failed")
            vif = "qmmon%d" % self.next_vif
            self.next_vif += 1
            self.live[vif] = args["iface"]
            return {"ok": True, "vif": vif}
        if cmd == "monitor_stop":
            self.live.pop(args["vif"], None)
            self.plans.pop(args["vif"], None)
            return {"ok": True}
        if cmd == "hop_plan":
            self.plans[args["vif"]] = args["plan"]
            return {"ok": True, "steps": len(args["plan"]), "cycle_ms": None}
        if cmd == "tune":
            return {"ok": True, "power_save": "off", "txpower": 20.0, "limit": 20.0}
        if cmd == "usb_reset":
            return {"ok": True}
        return {"ok": True}


def deep_for(client, adapters, clock=None, **cfg):
    deep = deepscan.DeepScan(dict({"deep_scan": True}, **cfg), connect=lambda: (client, "ready", ""),
                             process=in_thread, clock=clock or Clock())
    deep.timers = False
    assert deep.open()
    return deep


def started(client, adapters, clock=None, **cfg):
    deep = deep_for(client, adapters, clock, **cfg)
    deep.plan_roles(list(adapters))
    deep.start(lambda networks, adapter: None)
    return deep, deep.managed(list(adapters))


def vifs_of(adapters, deep):
    """What the adapter list looks like with the monitors in it, as hardware.wifi_adapters gives it."""
    originals = {e["original"] for e in deep.listeners.values()}
    out = [a for a in adapters if a["interface"] not in originals]
    return out + [dict(e["adapter"], interface=vif, type="803") for vif, e in deep.listeners.items()]


def channels(plans):
    return [step[0] for steps in plans.values() for step in steps]


def check_split(radios, plans, six):
    """Each channel a listener can take is on exactly one listener; 11, 6, 1 together on one; nothing a radio cannot
    tune; 6 GHz only on the preferred channels, only on 6 GHz radios and only when a 6 GHz radio sweeps normally."""
    by_vif = {vif: (set(bands), set(allowed) if allowed else None) for vif, bands, allowed in radios}
    heard = [f for f in channels(plans)]
    flock = [f for steps in plans.values() for f in {s[0] for s in steps}]
    assert all(flock.count(f) == 1 for f in set(flock)), plans
    wanted = set()
    for vif, (bands, allowed) in by_vif.items():
        for f in ALL_24 * ("2.4" in bands) + ALL_5 * ("5" in bands) + PSC * ("6" in bands and six):
            if allowed is None or f in allowed:
                wanted.add(f)
    assert set(heard) == wanted
    for vif, steps in plans.items():
        bands, allowed = by_vif[vif]
        assert len(steps) <= 64
        for freq, dwell in steps:
            band, ch = deepscan.band_channel(freq)
            assert band in bands and (allowed is None or freq in allowed), (vif, freq)
            assert radiod.MIN_DWELL <= dwell <= radiod.MAX_DWELL
            if band == "6":
                assert six and ch in deepscan.PSC6
    flock_on = [vif for vif, steps in plans.items() if {2412, 2437, 2462} & {f for f, _ in steps}]
    if any({2412, 2437, 2462} <= (allowed or set(ALL_24)) and "2.4" in bands
           for bands, allowed in by_vif.values()):
        assert len(flock_on) == 1
        assert [f for f, _ in plans[flock_on[0]][:3]] == [2462, 2437, 2412]


@pytest.mark.parametrize("count", [2, 3, 4, 5])
def test_the_split_covers_every_channel_once_for_2_to_5_listeners_with_mixed_bands(count):
    rnd = random.Random(count)
    kinds = [("2.4",), ("2.4", "5"), ("2.4", "5", "6"), ("5",), ("5", "6")]
    for round_ in range(150):
        radios = []
        for i in range(count):
            bands = rnd.choice(kinds)
            allowed = freqs_for(bands)
            if rnd.random() < 0.3:
                allowed = [f for f in allowed if rnd.random() < 0.8]
            radios.append(("qmmon%d" % i, bands, allowed))
        six = rnd.random() < 0.5
        plan = deepscan.SplitPlan("US", rnd.choice(["balanced", "flock"]), radios, six)
        if round_ % 3 == 0:
            plan.heard({rnd.choice(ALL_5): 40.0, rnd.choice(ALL_24): 10.0, rnd.choice(PSC): 5.0})
        plans = plan.build_all()
        check_split(radios, plans, six)
        loads = [deepscan.HopPlan.cycle_seconds(steps) for steps in plans.values() if steps]
        assert max(loads) <= deepscan.HopPlan.cycle_seconds([s for steps in plans.values() for s in steps])


def test_busy_5_ghz_channels_are_spread_so_no_listener_gets_them_all():
    radios = [("qmmon0", ("2.4", "5"), None), ("qmmon1", ("2.4", "5"), None), ("qmmon2", ("2.4", "5"), None)]
    plan = deepscan.SplitPlan("US", "balanced", radios, False)
    busy = ALL_5[:9]
    for _ in range(4):
        plan.heard({f: 50.0 for f in busy})
    plans = plan.build_all()
    on = {vif: [f for f, _ in steps if f in busy] for vif, steps in plans.items()}
    assert all(on.values()) and max(len(v) for v in on.values()) < len(busy)
    cycles = [deepscan.HopPlan.cycle_seconds(steps) for steps in plans.values()]
    assert max(cycles) - min(cycles) <= 0.5


def roles_ok(adapters, result, cap, held=(), helper=()):
    roles = result["roles"]
    live = {h["interface"]: h for h in helper}
    listening = [a for a in adapters if roles[a["interface"]] == "monitor"]
    managed = [a for a in adapters if roles[a["interface"]] == "managed"]
    assert len(listening) <= cap
    assert sorted(a["interface"] for a in result["monitors"]) == sorted(a["interface"] for a in listening)
    for band in {b for a in adapters for b in a.get("bands", ()) if a["interface"] not in held}:
        assert any(band in a.get("bands", ()) for a in managed if a["interface"] not in held), band
    for a in listening:
        assert a["external"] and a["interface"] not in held and not live.get(a["interface"], {}).get("connected")
        assert a["driver"] in deepscan.DRIVERS and a["monitor"]
    assert all(roles[a["interface"]] == "managed" for a in adapters if not a["external"])
    return listening


def test_all_listen_roles_keep_every_band_and_6_ghz_managed_for_any_mix():
    rnd = random.Random(5)
    kinds = [["2.4"], ["2.4", "5"], ["2.4", "5", "6"], ["5", "6"], []]
    drivers = ["mt7921u", "mt76x2u", "rtw88_8822bu", "rtl8812au"]
    most = 0
    for _ in range(3000):
        adapters = [dict(BUILTIN, bands=rnd.choice([["2.4"], ["2.4", "5"]]))] if rnd.random() < 0.7 else []
        for i in range(1, rnd.randrange(2, 8)):
            adapters.append(usb("wlan%d" % i, rnd.choice(kinds), rnd.choice(drivers), monitor=rnd.random() < 0.9))
        helper = [{"interface": a["interface"], "connected": rnd.random() < 0.1} for a in adapters]
        cap = rnd.randrange(1, 7)
        held = {a["interface"] for a in adapters if rnd.random() < 0.1}
        result = deepscan.assign_all(adapters, helper, cap, held)
        listening = roles_ok(adapters, result, cap, held, helper)
        most = max(most, len(listening))
        assert bool(listening) == (result["unavailable"] == "")
    assert most >= 5


def test_the_owner_plugs_in_five_adapters_and_four_listen():
    six_b = usb("wlan3", ["2.4", "5", "6"])
    dual = usb("wlan4", ["2.4", "5"], "mt76x2u")
    dual_b = usb("wlan5", ["2.4", "5"], "mt76x2u")
    alfa = dict(ALFA)
    adapters = [alfa, AC1200, six_b, dual, dual_b, BUILTIN]
    result = deepscan.assign_all(adapters, cap=6)
    assert result["roles"]["wlan1"] == "managed" and result["roles"]["wlan0"] == "managed"
    assert [a["interface"] for a in result["monitors"]] == ["wlan4", "wlan5", "wlan2", "wlan3"]
    assert result["reasons"]["wlan1"] == deepscan.REASONS["keeper"]
    capped = deepscan.assign_all(adapters, cap=2)
    assert [a["interface"] for a in capped["monitors"]] == ["wlan4", "wlan5"]
    assert capped["reasons"]["wlan2"] == deepscan.REASONS["cap"]
    assert deepscan.all_listen({"deep_scan": True}, adapters)
    assert not deepscan.all_listen({"deep_scan": True, "deep_scan_all": False}, adapters)
    assert not deepscan.all_listen({"deep_scan": False}, adapters)
    assert not deepscan.all_listen({"deep_scan": True, "deep_scan_adapter": "wlan2"}, adapters)
    assert not deepscan.all_listen({"deep_scan": True}, [ALFA, dict(AC1200_WIFI5, driver="rtl88xxau"), BUILTIN])


def test_6_ghz_stays_with_a_managed_6e_radio_and_monitors_hear_6_ghz_only_then():
    six_b = usb("wlan3", ["2.4", "5", "6"])
    dual = usb("wlan4", ["2.4", "5"], "mt76x2u")
    adapters = [ALFA, AC1200, six_b, dual, BUILTIN]
    client = MultiClient(adapters)
    clock = Clock()
    deep, managed = started(client, adapters, clock)
    try:
        assert [a["interface"] for a in managed] == ["wlan1", "wlan0"]
        assert len(deep.listeners) == 3 and set(client.plans) == set(deep.listeners)
        on6 = {client.live[vif] for vif, plan in client.plans.items() if any(f > 5925 for f, _, _ in plan)}
        assert on6 and on6 <= {"wlan2", "wlan3"}
        assert all(f in PSC for plan in client.plans.values() for f, _, _ in plan if f > 5925)
        check_split([(vif, e["adapter"]["bands"], e["freqs"]) for vif, e in deep.listeners.items()],
                    {vif: [(f, d) for f, _, d in plan] for vif, plan in client.plans.items()}, True)
        unplugged = [a for a in adapters if a is not ALFA]
        present = vifs_of(unplugged, deep)
        deep.follow([a for a in present if a.get("type") != "803"], present)
        assert "handed back" in deep.error and len(deep.listeners) == 2
        assert "wlan2" not in {e["original"] for e in deep.listeners.values()} and "wlan2" not in client.live.values()
        present = vifs_of(unplugged, deep)
        now = deep.follow([a for a in present if a.get("type") != "803"], present)
        assert [a["interface"] for a in now] == ["wlan2", "wlan0"] and deep._six_kept()
        plans = {vif: [(f, d) for f, _, d in plan] for vif, plan in client.plans.items()}
        check_split([(vif, e["adapter"]["bands"], e["freqs"]) for vif, e in deep.listeners.items()], plans, True)
        assert any(f > 5925 for f in channels(plans))
    finally:
        deep.stop()
    plan = deepscan.SplitPlan("US", "balanced", [("qmmon0", ("2.4", "5", "6"), None),
                                                 ("qmmon1", ("2.4", "5", "6"), None)], six=False)
    assert not any(f > 5925 for f in channels(plan.build_all()))


def test_a_dropped_listener_hands_its_channels_over_at_once_and_rejoins_after_the_stagger():
    dual = usb("wlan4", ["2.4", "5"], "mt76x2u")
    dual_b = usb("wlan5", ["2.4", "5"], "mt76x2u")
    adapters = [ALFA, AC1200, dual, dual_b, BUILTIN]
    client = MultiClient(adapters)
    clock = Clock()
    deep, managed = started(client, adapters, clock)
    try:
        assert [a["interface"] for a in managed] == ["wlan1", "wlan0"] and len(deep.listeners) == 3
        gone_vif = next(vif for vif, e in deep.listeners.items() if e["original"] == "wlan2")
        assert any(f > 5925 for f, _, _ in client.plans[gone_vif])
        present = [a for a in vifs_of(adapters, deep) if a["interface"] != gone_vif]
        sent = len(client.requests)
        now = deep.follow([a for a in present if a.get("type") != "803"], present)
        assert [a["interface"] for a in now] == ["wlan1", "wlan0"] and gone_vif not in deep.listeners
        assert deep.dropped == 1 and "dropped out" in deep.error
        later = [cmd for cmd, args in client.requests[sent:]]
        assert later.count("hop_plan") == 2 and later.index("monitor_stop") < later.index("hop_plan")
        plans = {v: [(f, d) for f, _, d in p] for v, p in client.plans.items()}
        assert set(plans) == set(deep.listeners)
        assert set(channels(plans)) == set(ALL_24 + ALL_5)
        back = vifs_of(adapters, deep)
        clock.tick(0.5)
        now = deep.follow([a for a in back if a.get("type") != "803"], back)
        assert [a["interface"] for a in now] == ["wlan1", "wlan0"] and deep.joining.keys() == {"wlan2"}
        assert len(deep.listeners) == 2
        clock.tick(deepscan.STAGGER)
        now = deep.follow([a for a in back if a.get("type") != "803"], back)
        assert [a["interface"] for a in now] == ["wlan1", "wlan0"] and len(deep.listeners) == 3
        assert deep.joined == 1 and not deep.joining
        plans = {v: [(f, d) for f, _, d in p] for v, p in client.plans.items()}
        check_split([(vif, e["adapter"]["bands"], e["freqs"]) for vif, e in deep.listeners.items()], plans,
                    deep._six_kept())
        assert any(f > 5925 for f in channels(plans))
    finally:
        deep.stop()


def test_a_join_the_helper_refuses_scans_normally_and_is_tried_again_later():
    dual = usb("wlan4", ["2.4", "5"], "mt76x2u")
    adapters = [ALFA, AC1200, dual, BUILTIN]
    client = MultiClient(adapters)
    clock = Clock()
    deep, _ = started(client, adapters, clock)
    try:
        vif = next(vif for vif, e in deep.listeners.items() if e["original"] == "wlan4")
        present = [a for a in vifs_of(adapters, deep) if a["interface"] != vif]
        deep.follow([a for a in present if a.get("type") != "803"], present)
        client.refuse_start.add("wlan4")
        back = vifs_of(adapters, deep)
        flat = [a for a in back if a.get("type") != "803"]
        deep.follow(flat, back)
        clock.tick(deepscan.STAGGER)
        deep.follow(flat, back)
        clock.tick()
        now = deep.follow(flat, back)
        assert "wlan4" in [a["interface"] for a in now] and "wlan4" in deep.refused
        client.refuse_start.clear()
        clock.tick(deepscan.RETRY_JOIN)
        deep.follow(flat, back)
        clock.tick(deepscan.STAGGER)
        now = deep.follow(flat, back)
        assert "wlan4" not in [a["interface"] for a in now] and len(deep.listeners) == 2
    finally:
        deep.stop()


def test_the_cap_follows_the_measured_cost_of_each_frame():
    assert deepscan.listen_cap({}, 3) is None
    assert deepscan.listen_cap({"parse_us": 40.0, "frames_per_s": 0}, 3) is None
    assert deepscan.listen_cap({"parse_us": 40.0, "frames_per_s": 300.0}, 3) == deepscan.LISTEN_CAP
    assert deepscan.listen_cap({"parse_us": 100.0, "frames_per_s": 200.0}, 2) == 6
    assert deepscan.listen_cap({"parse_us": 300.0, "frames_per_s": 600.0}, 3) == 2
    assert deepscan.listen_cap({"parse_us": 300.0, "frames_per_s": 1200.0}, 3) == 1
    assert deepscan.listen_cap({"parse_us": 900.0, "frames_per_s": 3000.0}, 3) == 1
    adapters = [ALFA, AC1200, usb("wlan3", ["2.4", "5", "6"]), usb("wlan4", ["2.4", "5"], "mt76x2u"),
                usb("wlan5", ["2.4", "5"], "mt76x2u"), BUILTIN]
    client = MultiClient(adapters)
    clock = Clock()
    deep, managed = started(client, adapters, clock)
    try:
        assert len(deep.listeners) == deepscan.START_CAP == 3
        assert "wlan3" in [a["interface"] for a in managed]
        deep._handle("stats", {"cpu": 10.0, "parse_us": 900.0, "frames_per_s": 3000.0, "channels": {}})
        assert deep.cap == 1 and len(deep.listeners) == 1 and "Busy CPU" in deep.error
        assert deep.vif == "qmmon0"
        deep._handle("stats", {"cpu": 3.0, "parse_us": 40.0, "frames_per_s": 100.0, "channels": {}})
        assert deep.cap == deepscan.LISTEN_CAP
        present = vifs_of(adapters, deep)
        flat = [a for a in present if a.get("type") != "803"]
        deep.follow(flat, present)
        clock.tick(deepscan.STAGGER)
        now = deep.follow(flat, present)
        assert len(deep.listeners) == 4 and [a["interface"] for a in now] == ["wlan1", "wlan0"]
        deep._handle("stats", {"cpu": 40.0, "parse_us": 40.0, "frames_per_s": 100.0, "channels": {}})
        assert len(deep.listeners) == 3 and deep.cap == 3 and deep.level == 0
    finally:
        deep.stop()


def test_power_trouble_with_nothing_left_to_ease_pauses_a_listener_until_it_is_unplugged():
    dual = usb("wlan4", ["2.4", "5"], "mt76x2u")
    adapters = [ALFA, AC1200, dual, BUILTIN]
    client = MultiClient(adapters)
    deep, _ = started(client, adapters)
    try:
        first = deep.vif
        name = deep.shed()
        assert name and deep.vif == first and name in deep.paused and len(deep.listeners) == 1
        assert deep.shed() is None
        present = vifs_of(adapters, deep)
        flat = [a for a in present if a.get("type") != "803"]
        assert name not in [a["interface"] for a in deep.follow(flat, present)]
        present = [a for a in present if a["interface"] != name]
        deep.follow([a for a in present if a.get("type") != "803"], present)
        assert name not in deep.paused
    finally:
        deep.stop()


def run(root, cfg, adapters, client):
    Fleet.found = networks(200, seed=9)
    try:
        def make(c):
            deep = deepscan.DeepScan(c, connect=lambda: (client, "ready", ""), process=in_thread)
            deep.timers = False
            return deep
        drive = Wardrive(dict(cfg, drive_alerts=False), Gps(FIX), root=root, adapters=adapters, ble=NoBle(),
                         fleet=Fleet, clock=lambda: NOW, deep=make if client is not None else None)
        drive.start()
        scanning = [a["interface"] for a in drive.fleet.adapters]
        drive.stop()
    finally:
        Fleet.found = None
    return drive, scanning


def test_with_all_adapters_listen_off_the_wigle_log_is_byte_identical_and_one_adapter_listens(tmp_path):
    adapters = [ALFA, AC1200, usb("wlan3", ["2.4", "5", "6"]), usb("wlan4", ["2.4", "5"], "mt76x2u"), BUILTIN]
    plain, _ = run(tmp_path / "plain", {}, adapters, None)
    client = MultiClient(adapters)
    off, scanning = run(tmp_path / "off", {"deep_scan": True, "deep_scan_all": False}, adapters, client)
    assert [cmd for cmd, _ in client.requests].count("monitor_start") == 1
    assert scanning == ["wlan1", "wlan2", "wlan3", "wlan0"]
    plan = next(args["plan"] for cmd, args in client.requests if cmd == "hop_plan")
    assert plan == [[f, 20, d] for f, d in deepscan.HopPlan("US", "balanced", freqs_for(["2.4", "5"])).build()]
    assert [p.name for p in plain.log_paths] == [p.name for p in off.log_paths] and plain.log_paths
    for a, b in zip(plain.log_paths, off.log_paths):
        assert a.read_bytes() == b.read_bytes()
    on_client = MultiClient(adapters)
    on, scanning = run(tmp_path / "on", {"deep_scan": True}, adapters, on_client)
    assert scanning == ["wlan1", "wlan0"]
    assert [cmd for cmd, _ in on_client.requests].count("monitor_start") == 3
    for a, b in zip(plain.log_paths, on.log_paths):
        assert a.read_bytes() == b.read_bytes()


def test_every_listener_logs_what_it_hears_through_the_one_frame_reader(tmp_path):
    from tests.test_deepscan import wait
    adapters = [ALFA, AC1200, usb("wlan4", ["2.4", "5"], "mt76x2u"), BUILTIN]
    client = MultiClient(adapters)
    drive = Wardrive({"deep_scan": True, "drive_alerts": False}, Gps(FIX), root=tmp_path, adapters=adapters,
                     ble=NoBle(), fleet=Fleet, clock=lambda: NOW,
                     deep=lambda c: deepscan.DeepScan(c, connect=lambda: (client, "ready", ""), process=in_thread))
    drive.start()
    try:
        assert len(drive.deep.listeners) == 2
        start = drive.tracker.summary()["wifi"]
        client.send(on_air(beacon(bssid="AC:84:C6:00:00:21", ssid=b"Five", channel=0), 5745), freq=5745)
        client.send(on_air(beacon(bssid="AC:84:C6:00:00:22", ssid=b"Two", channel=4), 2427), freq=2427)
        assert wait(lambda: drive.tracker.summary()["wifi"] >= start + 2)
        status = drive.status()
        assert len(status["deep"]["listeners"]) == 2 and status["deep"]["all"] is True
        assert sum(1 for r in drive.radio_status() if r["method"] == "monitor") == 2
    finally:
        drive.stop()


def test_the_settings_has_all_adapters_listen_on_auto_and_loads_it_safely(tmp_path):
    cfg = config.load(tmp_path / "none.json")
    assert cfg["deep_scan_all"] == "auto"
    path = tmp_path / "config.json"
    config.save(dict(cfg, deep_scan_all=False), path)
    assert config.load(path)["deep_scan_all"] is False
    path.write_text('{"deep_scan_all": "yes"}')
    assert config.load(path)["deep_scan_all"] == "auto"
    keys = [key for key, *_ in dict(config.SECTIONS)["DEEP SCAN"]]
    assert keys.index("deep_scan_all") == keys.index("deep_scan_plan") + 1


def stalled_rig(tmp_path):
    usb_root = tmp_path / "usb"
    device(usb_root, "1-1", "0424:2514", devnum="2", hub="e0")
    device(usb_root, "1-1.3", "0e8d:7961", devnum="7", product="Wireless_Device", wifi=True)
    link(tmp_path, "wlan3", "1-1.3")
    return usb_root


def test_an_adapter_that_dropped_out_is_reset_three_times_further_apart_then_needs_power(tmp_path):
    clock = Clock()
    stalled_rig(tmp_path)
    guard = guard_for(tmp_path, clock)
    three = usb("wlan3", ["2.4", "5", "6"])
    guard.start([ALFA, three, BUILTIN])
    item = {"usb_id": "0e8d:7961", "label": "MT7921AU", "port": "1-1.3", "disc": False}
    resets = []

    def reset(port):
        resets.append((clock(), port))
        return True
    assert guard.revive_stalled(reset, [dict(item, port="9-9")]) == []
    assert guard.revive_stalled(reset, [dict(item, disc=True)]) == []
    start = clock()
    for _ in range(120):
        guard.revive_stalled(reset, [item])
        clock.tick(2.0)
    assert [round(at - start) for at, _ in resets] == [10, 40, 130]
    state = guard.status()["revive"]["1-1.3"]
    assert state["needs_power"] and state["tries"] == powerguard.RESET_TRIES
    assert guard.notice["text"] == "MT7921AU needs power"
    from quartermaster.ui.screens import Hardware
    cards = Hardware.power_cards(type("App", (), {"hw": None}), guard.status())
    assert any(c["title"] == "MT7921AU needs power" for c in cards)
    guard.revive_stalled(reset, [])
    assert guard.status()["revive"] == {}
    no_helper = guard_for(tmp_path, clock)
    no_helper.start([ALFA, three, BUILTIN])
    for _ in range(20):
        no_helper.revive_stalled(lambda port: None, [item])
        clock.tick(10.0)
    state = no_helper.revive["1-1.3"]
    assert state["tries"] == 0 and not state["needs_power"] and not state["possible"]


def test_the_wardrive_resets_through_the_helper_and_says_so_when_there_is_none(tmp_path):
    client = MultiClient([ALFA, AC1200, BUILTIN])
    drive = Wardrive({"deep_scan": True, "drive_alerts": False}, Gps(FIX), root=tmp_path,
                     adapters=[ALFA, AC1200, BUILTIN], fleet=Fleet, clock=lambda: NOW, ble=NoBle(),
                     deep=lambda c: deepscan.DeepScan(c, connect=lambda: (client, "ready", ""), process=in_thread))
    drive.start()
    try:
        assert drive._reset_port("1-1.3") is True and client.requests[-1] == ("usb_reset", {"port": "1-1.3"})
        client.fail.add("usb_reset")
        assert drive._reset_port("1-1.3") is False
        drive.deep.client = None
        assert drive._reset_port("1-1.3") is None
        drive.deep.client = client
    finally:
        drive.stop()


def test_usb_host_channel_and_eproto_errors_count_as_trouble_and_show(tmp_path):
    kmsg = tmp_path / "kmsg"
    kmsg.write_text("")
    clock = Clock()
    guard = guard_for(tmp_path, clock, usb=tmp_path / "none")
    guard.kmsg_path = kmsg
    fleet = [ALFA, dict(AC1200, driver="mt76x2u"), BUILTIN]
    guard.start(fleet)
    lines = ["3,1,1,-;dwc2 3f980000.usb: dwc2_hc_chhltd_intr_dma: Channel 5 - ChHltd set, but reason is unknown\n",
             "3,2,2,-;mt76x2u 1-1.2.4:1.0: vendor request req:07 off:0080 failed:-71\n"]
    for _ in range(3):
        with open(kmsg, "a") as out:
            out.write(lines[0])
        clock.tick()
        assert guard.check(fleet) == [] and guard.reason == "" and guard.usb_limit == 0
    with open(kmsg, "a") as out:
        out.write("3,3,3,-;dwc2 3f980000.usb: no free channel\n")
    clock.tick()
    assert guard.check(fleet) == [] and guard.reason == "The USB port ran out of channels"
    with open(kmsg, "a") as out:
        out.write("3,3,3,-;dwc2 3f980000.usb: no free host channels\n")
    clock.tick()
    assert guard.check(fleet) == [("space", "wlan2", powerguard.SPACE)]
    assert guard.usb_limit == 2 and guard.status()["limit_reason"] == "The USB port ran out of channels"
    for line in (lines[1], "3,4,4,-;usb 1-1.2.4: urb status -EPROTO\n"):
        with open(kmsg, "a") as out:
            out.write(line)
        clock.tick()
        guard.check(fleet)
    assert guard.usb_limit == 4 and "USB errors (-71)" in guard.limit_reason
    guard._gone["1-1.3"] = ("0e8d:7961", clock.now)
    with open(kmsg, "a") as out:
        out.write("3,5,5,-;dwc2 3f980000.usb: no free channel\n")
    clock.tick()
    guard.check(fleet)
    assert guard.usb_limit == 4
    from quartermaster.ui.screens import Hardware
    cards = Hardware.power_cards(type("App", (), {"hw": None}), guard.status())
    assert any(c["title"] == "USB port at its limit" for c in cards)
    for line in ("dwc2 3f980000.usb: Channel 3 - ChHltd set", "dwc2 3f980000.usb: dwc2_hc_chhltd_intr_dma: Channel 5 - "
                 "ChHltd set, but reason is unknown", "dwc2 3f980000.usb: Channel 2: no space"):
        assert not powerguard.USB_LIMIT_LOG.search(line), line
    for line in ("dwc2 3f980000.usb: no free host channel",
                 "usb 1-1.3: error -71", "mt7921u 1-1.3:1.0: status -71"):
        assert powerguard.GUARD_LOG.search(line), line
    assert not powerguard.GUARD_LOG.search("wlan1: channel 6 switch")


def test_the_hardware_line_counts_active_adapters_estimates_amps_and_fits_the_screen(tmp_path):
    clock = Clock()
    usb_root = tmp_path / "usb"
    device(usb_root, "1-1", "0424:2514", devnum="2", hub="e0")
    device(usb_root, "1-1.2", "214b:7260", devnum="3", product="USB2.0 HUB", hub="e0")
    for n, port in ((1, "1-1.2.1"), (2, "1-1.2.2"), (3, "1-1.2.3")):
        device(usb_root, port, "0e8d:7961", devnum=str(4 + n), product="Wireless_Device", wifi=True)
    link(tmp_path, "wlan1", "1-1.2.1")
    link(tmp_path, "wlan2", "1-1.2.2")
    link(tmp_path, "qmmon0", "1-1.2.3")
    guard = guard_for(tmp_path, clock)
    guard.start([ALFA, AC1200, BUILTIN])
    full = guard.load["ma"]
    line = guard.adapters_line([ALFA, AC1200, BUILTIN], ["qmmon0"])
    saved = int(600 * (1 - powerguard.LISTEN_SHARE))
    assert line == "Adapters: 3 active, est. %.1f A, hub: unpowered" % ((full - saved) / 1000.0)
    guard.parked.append("wlan2")
    assert guard.adapters_line([ALFA, AC1200, BUILTIN], ["qmmon0"]).startswith("Adapters: 2 active")
    powered = guard_for(tmp_path, clock)
    powered.cfg = {"hub_power": True}
    powered.start([ALFA])
    assert powered.adapters_line([ALFA], []).endswith("hub: powered")
    alone = guard_for(tmp_path, clock, usb=tmp_path / "none")
    alone.start([])
    assert alone.adapters_line([], []) == "Adapters: 0 active, est. 0.0 A, hub: none"
    from quartermaster.ui.screens import Hardware
    longest = "Adapters: 10 active, est. 12.5 A, hub: unpowered"
    card = Hardware.power_cards(type("App", (), {"hw": None}), {"line": longest})[0]
    assert card["title"] == longest and card["tag"] == "USB"
    canvas = gfx.Canvas()
    assert canvas.text_width(longest, gfx.font("bold", 10)) <= gfx.W - 52
    assert canvas.text_width(card["detail"], gfx.font("ui", 9)) <= gfx.W - 20


class ResetSystem(radiod.System):
    def __init__(self, root):
        super().__init__(sys_net=str(root / "net"), sys_usb=str(root / "usb"), dev_usb=str(root / "dev"))
        self.resets = []

    def usb_reset(self, bus, dev):
        self.resets.append((bus, dev))


def reset_rig(tmp_path):
    root = tmp_path / "rig"
    (root / "net").mkdir(parents=True)

    def add(port, usb_id, working=False, bus="1", dev="5"):
        node = root / "usb" / port
        (node / (port + ":1.0")).mkdir(parents=True)
        vendor, product = usb_id.split(":")
        for name, value in (("idVendor", vendor), ("idProduct", product), ("busnum", bus), ("devnum", dev)):
            (node / name).write_text(value + "\n")
        if working:
            (node / (port + ":1.0") / "net" / "wlan2").mkdir(parents=True)
    add("1-1.3", "0e8d:7961", dev="7")
    add("1-1.4", "0e8d:7961", working=True, dev="8")
    add("1-1.5", "0781:5567", dev="9")
    add("1-1.6", "0bda:2838", dev="10")
    return root


def test_the_helper_resets_only_a_stalled_known_adapter_and_spaces_resets_out(tmp_path):
    root = reset_rig(tmp_path)
    system = ResetSystem(root)
    helper = radiod.Helper(system)
    sess = helper.open_session(lambda data: None)
    for bad in ("", "1-1;reboot", "../1-1", "1-1/../..", "usb1", "1-1.2.3.4.5.6.7", "0-1", 13, None, ["1-1"],
                "1-1 ", "-1-1"):
        assert ask(sess, cmd="usb_reset", port=bad)["ok"] is False, bad
    assert ask(sess, cmd="usb_reset")["error"] == "missing argument"
    assert ask(sess, cmd="usb_reset", port="1-1.3", iface="wlan1")["error"] == "unexpected argument"
    assert ask(sess, cmd="usb_reset", port="1-1.9")["error"] == "no USB device there"
    assert ask(sess, cmd="usb_reset", port="1-1.5")["error"] == "not a known Wi-Fi adapter"
    assert ask(sess, cmd="usb_reset", port="1-1.6")["error"] == "not a known Wi-Fi adapter"
    assert ask(sess, cmd="usb_reset", port="1-1.4")["error"] == "the adapter is working"
    assert system.resets == []
    clock = [1000.0]
    helper.clock = lambda: clock[0]
    assert ask(sess, cmd="usb_reset", port="1-1.3") == {"ok": True}
    assert system.resets == [(1, 7)]
    assert ask(sess, cmd="usb_reset", port="1-1.3")["error"] == "reset again later"
    clock[0] += radiod.RESET_GAP
    assert ask(sess, cmd="usb_reset", port="1-1.3")["ok"]
    clock[0] += radiod.RESET_GAP
    assert ask(sess, cmd="usb_reset", port="1-1.3")["ok"]
    clock[0] += radiod.RESET_GAP
    assert ask(sess, cmd="usb_reset", port="1-1.3")["error"] == "reset again later"
    clock[0] += radiod.RESET_WINDOW
    assert ask(sess, cmd="usb_reset", port="1-1.3")["ok"] and len(system.resets) == 4
    assert "usb_reset" in radiod.COMMANDS


def test_the_reset_ids_match_the_udev_rule_and_only_the_helper_gets_the_device_nodes():
    from tests.test_packaging import PACKAGING, settings, unit
    rules = (PACKAGING / "70-quartermaster.rules").read_text()
    granted = set()
    for line in rules.splitlines():
        if "GROUP=" in line:
            assert 'GROUP="quartermaster-usbreset"' in line and 'MODE="0664"' in line
            assert "RUN" not in line and "PROGRAM" not in line and "IMPORT" not in line
            vendor = line.split('ATTR{idVendor}=="')[1][:4]
            product = line.split('ATTR{idProduct}=="')[1][:4]
            granted.add("%s:%s" % (vendor, product))
    assert granted == set(radiod.RESET_IDS)
    service = settings(unit("quartermaster-radio.service"))
    assert service["SupplementaryGroups"] == ["quartermaster-usbreset"]
    assert service["DeviceAllow"] == ["char-usb_device rw"] and service["PrivateDevices"] == ["yes"]
    postinst = (PACKAGING / "postinst").read_text()
    assert "addgroup --system quartermaster-usbreset" in postinst
    assert "quartermaster-usbreset" not in postinst.split("for grp in")[1].split("; do")[0]


def test_the_hardware_and_wardrive_screens_show_every_listener_and_the_usb_line(tmp_path, drawn):
    from tests.test_ui import hardware_text, make_app, press, screen_text
    app = make_app(tmp_path)
    press(app, "W")
    real = app.drive.status
    extra = {"interface": "qmmon1", "label": "MT7612U dual-band", "original": "wlan3", "bands": ["2.4", "5"],
             "channels": 17}
    first = {"interface": "qmmon0", "label": "RTL8812AU dual-band", "original": "wlan2", "bands": ["2.4", "5"],
             "channels": 21}
    deep = {"state": "ready", "monitor": dict(first), "vif": "qmmon0", "reasons": {}, "unavailable": "",
            "stats": {"frames_per_s": 410.0, "dropped_share": 0.0}, "counts": {"flock": 0}, "error": "",
            "tuning": {}, "plan": "balanced", "level": 0, "all": True, "listeners": [first, extra], "cap": 3,
            "paused": [], "joining": ["wlan4"], "dropped": 1, "joined": 1}

    def status():
        found = real()
        power = dict(found.get("power") or {}, line="Adapters: 4 active, est. 2.1 A, hub: unpowered", usb_limit=2,
                     limit_reason="The USB port ran out of channels",
                     revive={"1-1.4": {"label": "MT7921AU", "tries": 3, "needs_power": True, "stalled": True}})
        return dict(found, deep=deep, power=power)
    app.drive.status = status
    text = screen_text(app, drawn)
    assert "DEEPx2" in text
    text = hardware_text(app, drawn)
    for _ in range(12):
        press(app, "X")
        text += screen_text(app, drawn)
    assert "qmmon0" in text and "ch 11·6·1 + 18 more, 2 listen" in text
    assert "qmmon1" in text and "Listens on its own 17 channels" in text
    assert "Adapters: 4 active, est. 2.1 A, hub: unpowered" in text
    assert "USB port at its limit" in text and "MT7921AU needs power" in text
    app.drive.status = real
    app.drive.stop()


def test_one_refused_monitor_scans_normally_and_a_busy_cpu_at_the_last_step_stops_rejoins():
    dual = usb("wlan4", ["2.4", "5"], "mt76x2u")
    adapters = [ALFA, AC1200, dual, BUILTIN]
    client = MultiClient(adapters)
    client.refuse_start.add("wlan2")
    clock = Clock()
    deep, managed = started(client, adapters, clock)
    try:
        assert [e["original"] for e in deep.listeners.values()] == ["wlan4"]
        assert [a["interface"] for a in managed] == ["wlan1", "wlan2", "wlan0"] and "wlan2" in deep.refused
        hot = {"cpu": 40.0, "channels": {}}
        for _ in range(4):
            deep._handle("stats", hot)
        assert not deep.listeners and deep.cap == 0 and deep.error.startswith("Busy CPU")
        present = vifs_of(adapters, deep)
        for _ in range(3):
            clock.tick(deepscan.STAGGER + deepscan.RETRY_JOIN)
            now = deep.follow(present, present)
        assert not deep.listeners and not deep.joining
        assert [a["interface"] for a in now] == ["wlan1", "wlan2", "wlan4", "wlan0"]
    finally:
        deep.stop()


def test_a_paused_or_parked_adapter_never_counts_as_keeping_its_bands():
    builtin = dict(BUILTIN, bands=["2.4"])
    six_b, six_c = usb("wlan2", ["2.4", "5", "6"]), usb("wlan3", ["2.4", "5", "6"])
    result = deepscan.assign_all([builtin, six_b, six_c], held={"wlan3"}, listening={"wlan2"})
    assert result["roles"]["wlan2"] == "managed" and not result["monitors"]
    assert result["reasons"]["wlan2"] == deepscan.REASONS["keeper"]
    assert result["reasons"]["wlan3"] == deepscan.REASONS["held"]
    stays = deepscan.assign_all([builtin, six_b, six_c], stay={"wlan3"})
    assert [a["interface"] for a in stays["monitors"]] == ["wlan2"]
    assert stays["reasons"]["wlan3"] == deepscan.REASONS["refused"]
    adapters = [ALFA, six_b, six_c, builtin]
    client = MultiClient(adapters)
    clock = Clock()
    deep, managed = started(client, adapters, clock)
    try:
        assert [a["interface"] for a in managed] == ["wlan1", "wlan0"] and len(deep.listeners) == 2
        assert deep.shed() == "wlan3" and "wlan3" in deep.paused
        left = [six_b, six_c, builtin]
        present = vifs_of(left, deep)
        now = deep.follow([a for a in present if a.get("type") != "803"], present)
        assert not deep.listeners and "handed back" in deep.error
        present = vifs_of(left, deep)
        now = deep.follow([a for a in present if a.get("type") != "803"], present)
        assert [a["interface"] for a in now] == ["wlan2", "wlan0"]
        assert deepscan.keeps_six(left, {a["interface"]: "managed" for a in now})
        deep.paused.clear()
        deep.listeners.clear()
        deep.follow([a for a in present if a.get("type") != "803"], present, held=["wlan2"])
        assert "wlan3" not in deep.joining and deep.roles["reasons"]["wlan3"] == deepscan.REASONS["keeper"]
    finally:
        deep.stop()


def test_an_adapter_plugged_in_mid_drive_listens_only_on_the_channels_its_phy_allows():
    adapters = [ALFA, AC1200, BUILTIN]
    client = MultiClient(adapters)
    clock = Clock()
    deep, _ = started(client, adapters, clock)
    try:
        assert len(deep.listeners) == 1
        late = usb("wlan5", ["2.4", "5"], "mt76x2u")
        narrow = [f for f in freqs_for(late["bands"]) if f not in (5845, 5865, 5885)]
        real = client.request

        def request(cmd, timeout=None, **args):
            reply = real(cmd, timeout, **args)
            if cmd == "adapters":
                for entry in reply["adapters"]:
                    if entry["interface"] == "wlan5":
                        entry["freqs"] = narrow
            if cmd == "hop_plan" and client.live.get(args["vif"]) == "wlan5":
                bad = [step[0] for step in args["plan"] if step[0] not in narrow]
                if bad:
                    raise radio_helper.HelperError("frequency %d not allowed" % bad[0])
            return reply
        client.request = request
        client.adapters = adapters + [late]
        present = vifs_of(client.adapters, deep)
        flat = [a for a in present if a.get("type") != "803"]
        deep.follow(flat, present)
        clock.tick(deepscan.STAGGER)
        deep.follow(flat, present)
        assert "wlan5" in {e["original"] for e in deep.listeners.values()} and "wlan5" not in deep.refused
        vif = next(v for v, e in deep.listeners.items() if e["original"] == "wlan5")
        assert deep.listeners[vif]["freqs"] == narrow
        assert client.plans[vif] and all(step[0] in narrow for step in client.plans[vif])
        asked = [cmd for cmd, _ in client.requests].count("adapters")
        present = vifs_of(client.adapters, deep)
        deep.follow([a for a in present if a.get("type") != "803"], present)
        assert [cmd for cmd, _ in client.requests].count("adapters") == asked
    finally:
        deep.stop()


def test_a_busy_cpu_lets_one_listener_go_per_look_and_it_does_not_come_straight_back():
    adapters = [ALFA, AC1200, usb("wlan3", ["2.4", "5", "6"]), usb("wlan4", ["2.4", "5"], "mt76x2u"),
                usb("wlan5", ["2.4", "5"], "mt76x2u"), BUILTIN]
    client = MultiClient(adapters)
    clock = Clock()
    deep, _ = started(client, adapters, clock)
    try:
        assert len(deep.listeners) == 3
        cheap = {"parse_us": 40.0, "frames_per_s": 300.0, "channels": {}}
        assert deepscan.listen_cap(cheap, 3) == deepscan.LISTEN_CAP
        deep._handle("stats", dict(cheap, cpu=30.0))
        assert len(deep.listeners) == 2 and deep.cap == 2 and deep.ceiling == 2
        present = vifs_of(adapters, deep)
        flat = [a for a in present if a.get("type") != "803"]
        starts = [cmd for cmd, _ in client.requests].count("monitor_start")
        for _ in range(20):
            deep._handle("stats", dict(cheap, cpu=15.0))
            assert deep.cap == 2
            clock.tick(deepscan.STAGGER)
            deep.follow(flat, present)
        assert len(deep.listeners) == 2 and not deep.joining
        assert [cmd for cmd, _ in client.requests].count("monitor_start") == starts
        for _ in range(deepscan.CALM * 2):
            deep._handle("stats", dict(cheap, cpu=5.0))
        assert deep.ceiling == 3
        deep._handle("stats", dict(cheap, cpu=5.0))
        assert deep.cap == 3
        deep.follow(flat, present)
        clock.tick(deepscan.STAGGER)
        deep.follow(flat, present)
        assert len(deep.listeners) == 3
        deep._handle("stats", {"parse_us": 300.0, "frames_per_s": 600.0, "channels": {}, "cpu": 40.0})
        assert len(deep.listeners) == 2 and deep.ceiling == 2 and deep._calm_need == deepscan.CALM * 4
    finally:
        deep.stop()


def test_the_frame_reader_never_waits_on_a_join_and_the_monitor_survives_a_listener_leaving():
    import threading
    adapters = [ALFA, AC1200, usb("wlan3", ["2.4", "5", "6"]), BUILTIN]
    client = MultiClient(adapters)
    deep, _ = started(client, adapters)
    try:
        held = threading.Event()
        free = threading.Event()

        def hold():
            with deep._lock:
                held.set()
                free.wait(5)
        holder = threading.Thread(target=hold)
        holder.start()
        held.wait(5)
        done = threading.Event()

        def look():
            deep._plan_at = 0.0
            deep._upkeep()
            deep._handle("stats", {"cpu": 40.0, "parse_us": 900.0, "frames_per_s": 3000.0, "channels": {}})
            deep._handle("networks", [])
            done.set()
        threading.Thread(target=look, daemon=True).start()
        assert done.wait(2)
        free.set()
        holder.join()
        seen = []
        deep.sinks["networks"] = lambda payload, adapter: seen.append(adapter)
        deep.listeners.clear()
        deep._handle("networks", [{"bssid": "x"}])
        assert seen == [None]
    finally:
        deep.stop()


def test_listening_and_handing_back_is_not_taken_for_power_trouble(tmp_path):
    usb_root = tmp_path / "usb"
    for i, port in enumerate(("1-1.2.1", "1-1.2.2", "1-1.2.3")):
        device(usb_root, port, "0e8d:7961", devnum=str(3 + i), wifi=True)
    for iface, port in (("wlan1", "1-1.2.1"), ("wlan2", "1-1.2.2"), ("wlan3", "1-1.2.3")):
        link(tmp_path, iface, port)
    link(tmp_path, "qmmon0", "1-1.2.3")
    six_b = usb("wlan3", ["2.4", "5", "6"])
    fleet = [ALFA, AC1200, six_b, BUILTIN]
    for moving, trouble in (({"wlan3"}, False), (set(), True)):
        clock = Clock()
        guard = guard_for(tmp_path, clock)
        guard.start(fleet)
        guard.moving = moving
        clock.tick()
        guard.check([ALFA, AC1200, BUILTIN])
        guard.listening = [(six_b, "qmmon0")]
        clock.tick()
        guard.check([ALFA, AC1200, BUILTIN])
        guard.listening, guard.moving = [], moving
        clock.tick()
        guard.check([ALFA, AC1200, BUILTIN])
        clock.tick()
        guard.check(fleet)
        assert ("came back" in guard.reason) == trouble, guard.reason
    adapters = [ALFA, AC1200, usb("wlan3", ["2.4", "5", "6"]), usb("wlan4", ["2.4", "5"], "mt76x2u"), BUILTIN]
    client = MultiClient(adapters)
    clock = Clock()
    deep, _ = started(client, adapters, clock)
    try:
        last = deep._last_name()
        deep._handle("stats", {"cpu": 30.0, "parse_us": 40.0, "frames_per_s": 300.0, "channels": {}})
        assert deep.moving() == {last}
        deep.ceiling = deep.cap = deepscan.LISTEN_CAP
        present = vifs_of(adapters, deep)
        flat = [a for a in present if a.get("type") != "803"]
        deep.follow(flat, present)
        assert deep.joining.keys() == {last} and deep.moving() == {last}
        clock.tick(deepscan.STAGGER)
        deep.follow(flat, present)
        assert not deep.joining and deep.moving() == set()
        gone = next(vif for vif, e in deep.listeners.items() if e["original"] == last)
        present = [a for a in vifs_of(adapters, deep) if a["interface"] != gone]
        deep.follow([a for a in present if a.get("type") != "803"], present)
        assert last not in deep.moving()
    finally:
        deep.stop()


def test_the_helper_stays_up_while_a_usb_reset_still_counts_against_its_port(tmp_path):
    import socket
    root = reset_rig(tmp_path)
    system = ResetSystem(root)
    helper = radiod.Helper(system)
    clock = [5000.0]
    helper.clock = lambda: clock[0]
    sess = helper.open_session(lambda data: None)
    assert not helper.resetting()
    assert ask(sess, cmd="usb_reset", port="1-1.3") == {"ok": True}
    helper.close_session(sess)
    assert helper.resetting()
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET)
    listener.bind(str(tmp_path / "radio.sock"))
    listener.listen(1)
    helper.last_client -= radiod.IDLE_EXIT + 1
    import threading
    serving = threading.Thread(target=helper.serve, args=(listener,), daemon=True)
    serving.start()
    serving.join(1.5)
    assert serving.is_alive()
    clock[0] += radiod.RESET_WINDOW
    assert not helper.resetting()
    serving.join(3)
    assert not serving.is_alive()
    listener.close()
