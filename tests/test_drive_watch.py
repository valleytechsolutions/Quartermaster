import io
import json
import random
import struct
import time
import wave

import numpy as np
import pytest

from quartermaster import config, dragon_link, drive_watch, recon, tones
from quartermaster.drive_watch import DriveWatch, SsidConflicts, WatchList, ble_hint, wifi_hint
from quartermaster.engine import Wardrive
from quartermaster.recon_vendors import VENDOR_PREFIXES
from quartermaster.recon_engine import AlertSound
from tests.fakes import ADAPTERS, Ble, Clock, Fleet, Gps, networks
from tests.test_core import FIX, NOW

AXON_BLE = {"address": "11:22:33:44:55:66", "address_type": 1, "rssi": -70, "name": "", "service_data": {},
            "uuids": ["0000fe6b-0000-1000-8000-00805f9b34fb"], "manufacturer": {0x034D: b"\x01"}}
META_BLE = {"address": "C1:22:33:44:55:77", "address_type": 1, "rssi": -66, "name": "", "service_data": {},
            "uuids": ["0000fd5f-0000-1000-8000-00805f9b34fb"], "manufacturer": {0x01AB: b"\x02\x03"}}
FLIPPER_BLE = {"address": "80:E1:26:44:55:99", "address_type": 1, "rssi": -60, "name": "Flipper Zed",
               "uuids": ["00003082-0000-1000-8000-00805f9b34fb"], "manufacturer": {}, "service_data": {}}
FLOCK_BLE = {"address": "D4:22:33:44:55:88", "address_type": 1, "rssi": -72, "name": "Penguin-1234567890",
             "uuids": [], "manufacturer": {0x09C8: b"\x00"}, "service_data": {}}
WORDS = ["flock", "Penguin-0012345678", "Pigvision", "AXON", "bodyworn cam", "Flipper Zero", "SmartTag2", "AirTag",
         "pwnagotchi", "Pineapple_1", "hak5", "nyanbox", "HaleHound", "Ray-Ban Meta", "rayban", "Oakley Meta HSTN",
         "Spectacles 3", "AB3-1234", "Ring Setup", "raven-01", "pwned", "deauther", "HC-05", "RN42-AB", "BT04-A",
         "FS-1A2B3C", "FS Ext Battery", "1234567890", "biscuit", "VIOFO_A139", "BlackVue750", "70mai_D06",
         "Thinkware_U1000", "nextbase 622", "ROVE_R2", "REDTIGER_F7N", "DGK-001", "ACCQ-12", "HCAM-1",
         "CLOUDCAM_9", "MEYE_1", "GW_IPC_7", "IPC_AP_1", "ABCD-123456-ABCDE", "MV1234567", "x_VANTRUE_y",
         "GoogleHome1234", "Flock Noir", "Dragon Detector", "My Home", "", "NETGEAR42", "DIRECT-xy"]


class Player:
    """Takes tones as the real player would, without a speaker."""

    def __init__(self, ok=True):
        self.calls, self.ok, self.failed = [], ok, False

    def play(self, tone, volume="high", kind=None, priority=50, preview=False):
        if self.ok:
            self.calls.append((tone, kind))
        return self.ok

    def available(self):
        return self.ok

    def stop(self):
        pass


def watch(tmp_path, clock=None, player=None, **cfg):
    clock = clock or Clock()
    w = DriveWatch(cfg, Gps(FIX), tmp_path, player=player or Player(), clock=clock, monotonic=clock, threads=False)
    w.start(NOW)
    return w


def wifi(bssid, ssid="", rssi=-60, security="WPA2"):
    return {"bssid": bssid, "ssid": ssid, "rssi": rssi, "channel": 6, "frequency": 2437, "security": security,
            "band": "2.4", "capabilities": "[WPA2-PSK-CCMP][ESS]" if security != "OPEN" else "[ESS]"}


def flagged_wifi(net):
    result = recon.classify_wifi(net["bssid"], net["ssid"])
    return result["category"] is not None and result["confidence"] >= recon.LOW


def flagged_ble(item):
    ad = recon.advertisement_from_properties(item.get("name"), item.get("uuids", ()), item.get("manufacturer"),
                                             item.get("service_data"))
    result = recon.classify_ble(item["address"], item.get("address_type", 1), ad)
    return result["category"] is not None and result["confidence"] >= recon.LOW


def random_mac(rnd, first=None):
    octets = [rnd.randrange(256) for _ in range(6)]
    if first is not None:
        octets[:3] = [first >> 16, first >> 8 & 255, first & 255]
    return ":".join("%02X" % o for o in octets)


def odd_case(rnd, text):
    return "".join(ch.upper() if rnd.random() < 0.5 else ch.lower() for ch in text)


def test_the_wifi_look_up_never_misses_what_recon_would_flag():
    rnd = random.Random(5)
    prefixes = [row[0] for row in VENDOR_PREFIXES] + list(recon._FLOCK_OUIS)
    prefixes += [0x0025df, 0x001d96, 0x0023bd, 0x2824ff, 0xe40540, 0x826bf2]
    corpus = [wifi(random_mac(rnd, prefix), rnd.choice(WORDS)) for prefix in prefixes]
    for _ in range(20000):
        name = rnd.choice(WORDS)
        name = odd_case(rnd, name) if rnd.random() < 0.3 else name
        if rnd.random() < 0.3:
            name = "%s %s" % (rnd.choice(("my", "The", "x")), name)
        corpus.append(wifi(random_mac(rnd, rnd.choice(prefixes) if rnd.random() < 0.2 else None), name[:32]))
    flagged = [net for net in corpus if flagged_wifi(net)]
    assert len(flagged) > 1000
    assert [net for net in flagged if wifi_hint(net["bssid"], net["ssid"]) is None] == []


def test_the_bluetooth_look_up_never_misses_what_recon_would_flag():
    rnd = random.Random(9)
    companies = list(drive_watch._CID_HINT) + [0x0006, 0x0075, 0x00E0, 0x0059, 0x004C]
    uuids = ["0000%04x-0000-1000-8000-00805f9b34fb" % u for u in list(drive_watch._UUID16_HINT) + [0xfe9f, 0x180f]]
    uuids += list(drive_watch._UUID128_HINT) + ["0000fc81-1111-2222-3333-444455556666"]
    apple = [bytes([0x12, 0x19]) + bytes(25), bytes([0x12, 0x02, 0x00, 0x01]), bytes([0x07, 0x19, 0x01, 0x00, 0x55]) +
             bytes(4), bytes([0x08, 0x07]) + bytes([0, 0, 0, 0, 0, 7, 0]), bytes([0x02, 0x15]) + bytes(21), bytes(27),
             bytes([0x10, 0x05, 0x01, 0x18])]
    corpus = []
    for _ in range(15000):
        manufacturer = {}
        for _ in range(rnd.randrange(3)):
            company = rnd.choice(companies)
            manufacturer[company] = rnd.choice(apple) if company == 0x004C else bytes(rnd.randrange(8)) + \
                (b"BWCDEVICE" if rnd.random() < 0.02 else b"")
        address_type = rnd.choice((0, 1, 1))
        prefix = rnd.choice([row[0] for row in VENDOR_PREFIXES]) if address_type == 0 and rnd.random() < 0.4 else None
        corpus.append({"address": random_mac(rnd, prefix), "address_type": address_type, "rssi": -60,
                       "name": rnd.choice(WORDS) if rnd.random() < 0.4 else "",
                       "uuids": rnd.sample(uuids, rnd.randrange(3)), "manufacturer": manufacturer,
                       "service_data": {rnd.choice(uuids): bytes(rnd.randrange(30))} if rnd.random() < 0.2 else {}})
    corpus += [AXON_BLE, META_BLE, FLOCK_BLE]
    flagged = [item for item in corpus if flagged_ble(item)]
    assert len(flagged) > 3000
    assert [item for item in flagged if ble_hint(item) is None] == []


def test_the_look_up_passes_little_of_an_ordinary_street():
    nets = networks(2000, seed=3)
    assert sum(1 for net in nets if wifi_hint(net["bssid"], net["ssid"])) < 20
    assert wifi_hint("02:25:DF:11:22:33", "") is None
    assert wifi_hint("B4:1E:52:00:00:01", "") == "flock" and wifi_hint("00:25:DF:00:00:01", "") == "axon"
    assert ble_hint(dict(AXON_BLE, manufacturer={}, uuids=[])) is None


def test_types_tones_and_settings_start_as_kal_chose(tmp_path):
    cfg = config.load(tmp_path / "none.json")
    assert cfg["drive_alerts"] is True and cfg["drive_alert_tones"] is True
    assert set(cfg["drive_alert_types"]) == {"flock", "axon", "glasses", "mine"}
    sounds = cfg["drive_alert_sounds"]
    assert (sounds["axon"], sounds["flock"], sounds["glasses"]) == ("siren", "chirp", "ping")
    assert {sounds[k] for k in drive_watch.TYPES if k not in ("axon", "flock", "glasses")} == {"blip"}
    assert cfg["drive_alert_min"] == "medium" and cfg["drive_alert_again"] == 900 and cfg["drive_watch"] == []
    assert not {"fleet", "listener", "fox"} & set(cfg["recon_categories"])


def test_drive_alert_settings_are_saved_and_bad_values_dropped(tmp_path):
    path = tmp_path / "config.json"
    cfg = config.load(path)
    cfg.update(drive_alert_types=["alpr", "axon"], drive_alert_mute=["axon"], drive_watch=["veh*"],
               drive_alert_sounds=dict(cfg["drive_alert_sounds"], axon="yelp"), drive_alert_volume="low")
    config.save(cfg, path)
    again = config.load(path)
    assert again["drive_alert_types"] == ["axon", "alpr"] and again["drive_alert_mute"] == ["axon"]
    assert again["drive_watch"] == ["veh*"] and again["drive_alert_sounds"]["axon"] == "yelp"
    assert again["drive_alert_volume"] == "low"
    data = json.loads(path.read_text())
    data.update(drive_alert_types=["axon", "nope", 3],
                drive_watch=["a" * 99, 5, " ", "b"] + ["c%d" % i for i in range(20)],
                drive_alert_sounds={"axon": "klaxon", "flock": "yelp", "nope": "siren", "glasses": 4})
    path.write_text(json.dumps(data))
    loaded = config.load(path)
    assert loaded["drive_alert_types"] == ["axon"] and loaded["drive_watch"][:2] == ["a" * 32, "b"]
    assert len(loaded["drive_watch"]) == config.MAX_WATCH
    assert loaded["drive_alert_sounds"]["axon"] == "siren" and loaded["drive_alert_sounds"]["flock"] == "yelp"
    assert loaded["drive_alert_sounds"]["glasses"] == "ping" and "nope" not in loaded["drive_alert_sounds"]


def test_a_device_alerts_once_per_visit_and_again_after_it_has_gone(tmp_path):
    clock = Clock()
    w = watch(tmp_path, clock)
    for _ in range(5):
        w.offer_wifi([wifi("B4:1E:52:00:00:01", "cam")])
        w.drain()
        clock.advance(10)
    assert len(w.alert_list()) == 1 and w.alert_list()[0]["count"] == 5
    assert w.player.calls == [("chirp", "flock")]
    clock.advance(901)
    w.offer_wifi([wifi("B4:1E:52:00:00:01", "cam")])
    w.drain()
    assert len(w.alert_list()) == 2


def test_time_with_bluetooth_down_is_not_time_away(tmp_path):
    clock = Clock()
    w = watch(tmp_path, clock)
    w.offer_ble(AXON_BLE)
    w.drain()
    w.listening("ble", False)
    clock.advance(2000)
    w.listening("ble", True)
    w.offer_ble(AXON_BLE)
    w.drain()
    assert len(w.alert_list()) == 1


def test_other_types_need_two_sightings_apart_and_off_types_are_only_logged(tmp_path):
    clock = Clock()
    w = watch(tmp_path, clock, drive_alert_types=["airtag", "axon"])
    airtag = {"address": "D1:22:33:44:55:66", "address_type": 1, "rssi": -69, "name": "", "uuids": [],
              "manufacturer": {0x004C: bytes([0x12, 0x19]) + bytes(25)}, "service_data": {}}
    w.offer_ble(airtag)
    w.drain()
    assert w.alert_list() == []
    clock.advance(0.2)
    w.offer_ble(airtag)
    w.drain()
    assert w.alert_list() == []
    clock.advance(1)
    w.offer_ble(airtag)
    w.offer_wifi([wifi("B4:1E:52:00:00:01", "cam")])
    w.drain()
    assert [a["category"] for a in w.alert_list()] == ["airtag"]
    w.stop()
    rows = [json.loads(line) for line in w.log_path.read_text().splitlines()]
    assert {r["category"] for r in rows} == {"airtag", "flock"}
    assert not any(r["alert"] for r in rows if r["category"] == "flock")
    assert w.log_path.stat().st_mode & 0o777 == 0o600


def test_evidence_signal_floor_and_ignore_list_hold_alerts_back(tmp_path):
    w = watch(tmp_path, drive_alert_min="high", drive_alert_floor=-75, ignore=["Ignored Cam"])
    w.offer_wifi([wifi("B4:1E:52:00:00:01", "cam")])
    w.offer_ble(dict(AXON_BLE, rssi=-90))
    w.offer_ble(dict(AXON_BLE, address="11:22:33:44:55:67", rssi=-60))
    w.offer_ble(dict(META_BLE, address="C1:22:33:44:55:79"))
    w.drain()
    assert [a["address"] for a in w.alert_list()] == ["11:22:33:44:55:67"]
    w2 = watch(tmp_path / "2", ignore=["B4:1E:52:00:00:02"])
    w2.offer_wifi([wifi("B4:1E:52:00:00:02", "cam")])
    w2.drain()
    w2.stop()
    assert w2.alert_list() == [] and json.loads(w2.log_path.read_text())["ignored"] is True


def test_tones_are_spaced_capped_and_follow_the_settings(tmp_path):
    clock = Clock()
    w = watch(tmp_path, clock)
    w.offer_wifi([wifi("B4:1E:52:00:00:01")])
    w.drain()
    clock.advance(2)
    w.offer_wifi([wifi("B4:1E:52:00:00:02")])
    w.offer_ble(META_BLE)
    w.drain()
    clock.advance(1)
    w.offer_ble(AXON_BLE)
    w.drain()
    assert w.player.calls == [("chirp", "flock"), ("siren", "axon")]
    assert len(w.alert_list()) == 4 and w.alert_list()[2]["fold"] == 2
    muted = watch(tmp_path / "m", drive_alert_mute=["axon"])
    muted.offer_ble(AXON_BLE)
    muted.drain()
    silent = watch(tmp_path / "s", drive_alert_tones=False)
    silent.offer_ble(AXON_BLE)
    silent.drain()
    chosen = watch(tmp_path / "c", drive_alert_sounds={"axon": "dd_axon"})
    chosen.offer_ble(AXON_BLE)
    chosen.drain()
    assert muted.player.calls == silent.player.calls == [] and len(muted.alert_list()) == len(silent.alert_list())
    assert chosen.player.calls == [("dd_axon", "axon")]
    w = watch(tmp_path / "h", clock)
    for i in range(40):
        w.offer_wifi([wifi("B4:1E:52:00:01:%02X" % i)])
        w.drain()
        clock.advance(61)
    assert len(w.player.calls) == drive_watch.TONES_PER_HOUR and len(w.alert_list()) == 40
    w.offer_ble(AXON_BLE)
    w.drain()
    assert w.player.calls[-1] == ("siren", "axon") and w.alert_list()[0]["tone"] is True


def test_a_flood_of_new_devices_mutes_its_kind_but_never_cameras(tmp_path):
    clock = Clock()
    w = watch(tmp_path, clock, drive_alert_types=["hacker", "axon"], drive_alert_min="low")
    for i in range(30):
        w.offer_ble(dict(FLIPPER_BLE, address="80:E1:26:44:56:%02X" % i))
        w.offer_ble(dict(AXON_BLE, address="11:22:33:44:56:%02X" % i))
        w.drain()
        clock.advance(0.5)
    shown = w.alert_list()
    assert [a["name"] for a in shown if a.get("flood")] == ["Flood: 16 new radio tools"]
    assert sum(1 for a in shown if a["category"] == "hacker" and not a.get("flood")) == 15
    assert sum(1 for a in shown if a["category"] == "axon") == 30
    w.stop()
    assert sum(1 for _ in w.log_path.read_text().splitlines()) == 60


def test_apple_adverts_wait_only_when_an_apple_kind_is_on(tmp_path):
    advert = {"address": "D1:22:33:44:57:01", "address_type": 1, "rssi": -50, "name": "", "uuids": [],
              "manufacturer": {0x004C: bytes([0x10, 0x05, 0x01])}, "service_data": {}}
    w = watch(tmp_path)
    w.offer_ble(advert)
    assert not w.urgent and not w.rest
    w = watch(tmp_path / "a", drive_alert_types=["airtag"])
    w.offer_ble(advert)
    assert len(w.urgent) == 1


def test_my_alerts_alert_on_any_device_named_or_addressed(tmp_path):
    entries = WatchList(["veh*", "Patrol", "aa-bb-cc-00-00-09", "x[1]"])
    assert entries.match("", "VEH912") == "veh*" and entries.match("", "patrol 7") == "Patrol"
    assert entries.match("AA:BB:CC:00:00:09", "") == "aa-bb-cc-00-00-09" and entries.match("", "x[1]") == "x[1]"
    assert entries.match("", "my veh") is None and not WatchList([])
    w = watch(tmp_path, drive_watch=["veh*"], drive_alert_types=["mine"])
    w.offer_wifi([wifi("AA:BB:CC:00:00:01", "veh912"), wifi("AA:BB:CC:00:00:02", "home")])
    w.drain()
    shown = w.alert_list()
    assert [a["category"] for a in shown] == ["mine"] and shown[0]["evidence"] == "My alerts: veh*"


def test_an_open_twin_of_a_protected_network_alerts_once(tmp_path):
    conflicts = SsidConflicts()
    mesh = [wifi("10:00:00:00:00:01", "Cafe"), wifi("10:00:00:00:00:02", "Cafe")]
    assert [conflicts.observe(mesh, t) for t in (0, 1, 2, 3)] == [[], [], [], []]
    clock = Clock()
    w = watch(tmp_path, clock, drive_alert_types=["eviltwin"])
    for _ in range(4):
        w.offer_wifi([wifi("10:00:00:00:00:01", "Cafe"), wifi("20:00:00:00:00:02", "Cafe", security="OPEN")])
        w.drain()
        clock.advance(4)
    shown = w.alert_list()
    assert [a["category"] for a in shown] == ["eviltwin"] and "different makers" in shown[0]["evidence"]
    clock.advance(600)
    for _ in range(4):
        w.offer_wifi([wifi("10:00:00:00:00:01", "Cafe"), wifi("20:00:00:00:00:02", "Cafe", security="OPEN")])
        w.drain()
        clock.advance(4)
    assert len(w.alert_list()) == 1


def test_a_burst_of_tracker_adverts_is_one_incident(tmp_path):
    clock = Clock()
    w = watch(tmp_path, clock, drive_alert_types=["burst"])
    for i in range(20):
        w.offer_ble({"address": "D1:22:33:44:57:%02X" % i, "address_type": 1, "rssi": -50, "name": "", "uuids": [],
                     "manufacturer": {0x004C: bytes([0x12, 0x19]) + bytes(25)}, "service_data": {}})
        w.drain()
        clock.advance(0.1)
    assert [a["category"] for a in w.alert_list()] == ["burst"]


def test_alerts_from_a_dragon_detector_join_in(tmp_path):
    w = watch(tmp_path)
    w.offer_external(dragon_link.parse("ALERT Flock: 4 / -61 dBm / Flock research OUI + wildcard probe IE "
                                       "fingerprint; spoofable"))
    w.offer_external(dragon_link.parse("ALERT Body cameras: -70 dBm / Shared radio vendor OUI; shared/research "
                                       "match, product unverified"))
    w.drain()
    assert [a["category"] for a in w.alert_list()] == ["flock"] and w.alert_list()[0]["source"] == "dragon"


def test_every_dragon_detector_alert_is_a_device_of_its_own(tmp_path):
    clock = Clock()
    w = watch(tmp_path, clock, drive_alert_types=["flock", "raven", "skimmer", "tracker"])
    for line in ("ALERT Flock: -50 dBm / Flock Safety OUI", "ALERT Flock: -70 dBm / Flock Safety OUI",
                 "ALERT Acoustic sensors: -60 dBm / Raven service UUID 0x3100",
                 "ALERT Serial module: HC-05 / -60 dBm / HC-05 name", "ALERT Tile: -60 dBm / Tile service UUID"):
        w.offer_external(dragon_link.parse(line))
        w.drain()
        clock.advance(120)
    assert [a["category"] for a in w.alert_list()] == ["tracker", "skimmer", "raven", "flock", "flock"]
    w.stop()
    rows = [json.loads(line) for line in w.log_path.read_text().splitlines()]
    assert len(rows) == 5 and all(row["alert"] for row in rows)


def test_dragon_detector_lines_are_read_and_graded():
    alert = dragon_link.parse("ALERT Camera glasses: Ray-Ban / Meta / -55 dBm / Meta/Luxottica + Meta service "
                              "composite; recording unknown")
    assert alert["category"] == "glasses" and alert["name"] == "Ray-Ban / Meta" and alert["rssi"] == -55
    assert alert["confidence"] == recon.MEDIUM
    assert dragon_link.parse("ALERT Flock: -80 dBm / Flock research OUI; shared chip vendor possible")[
        "confidence"] == recon.LOW
    assert dragon_link.parse("ALERT WATCH LIST HIT: car / -40 dBm / anything")["category"] == "mine"
    assert dragon_link.parse("ALERT Radio memory error: Could not allocate packet pool") is None
    assert dragon_link.parse("Commands: status, contacts") is None
    assert dragon_link.recognised([b"Commands: status, contacts, selftest"])

    class Port:
        closed = False

        def close(self):
            Port.closed = True
    heard = []
    link = dragon_link.DragonLink("/dev/ttyACM0", Port(), heard.append)
    link.take(b"BADGE_INFO 2.4.1 320x170 READY s3\nALERT Flock: -61 dBm / Flock Safety OUI; product/act")
    link.take(b"ivity unverified\r\n" + b"x" * 700 + b"\nALERT Ring: -70 dBm / Ring LLC OUI; product/activity "
              b"unverified\n")
    assert link.version == "2.4.1" and [a["category"] for a in heard] == ["flock", "ring"]
    link.stop()
    assert Port.closed


def test_an_alert_failure_never_stops_the_wardrive(tmp_path, monkeypatch, noted):
    def broken(*args, **kwargs):
        raise RuntimeError("boom")
    monkeypatch.setattr(drive_watch, "wifi_hint", broken)
    monkeypatch.setattr(drive_watch, "ble_hint", broken)
    found = networks(50, seed=2) + [wifi("B4:1E:52:00:00:01", "cam")]
    Fleet.found = found
    try:
        drive = Wardrive({"log_rows": "track"}, Gps(FIX), root=tmp_path, adapters=ADAPTERS[:1], fleet=Fleet,
                         ble=Ble([("11:22:33:44:55:66", "Axon", -60)]), clock=lambda: NOW)
        drive.start()
        drive.stop()
    finally:
        Fleet.found = None
    assert drive.rows > 40 and drive.watch is not None and "boom" in noted.read_text()


def run_drive(root, alerts):
    Fleet.found = networks(300, seed=4) + [wifi("B4:1E:52:00:00:01", "Flock-1"), wifi("00:25:DF:01:02:03", "")]
    try:
        drive = Wardrive({"drive_alerts": alerts}, Gps(FIX), root=root, adapters=ADAPTERS[:1], fleet=Fleet,
                         ble=Ble([("11:22:33:44:55:66", "Axon Body 3", -60), ("22:33:44:55:66:77", "Buds", -70)]),
                         clock=lambda: NOW)
        drive.start()
        deadline = time.monotonic() + 5
        while drive.tracker.summary()["ble"] < 2 and time.monotonic() < deadline:
            time.sleep(0.01)
        drive.stop()
    finally:
        Fleet.found = None
    return drive


def test_wigle_logs_are_the_same_with_alerts_on_or_off(tmp_path):
    on, off = run_drive(tmp_path / "on", True), run_drive(tmp_path / "off", False)
    assert off.watch is None and on.watch is not None and on.watch.alert_list()
    assert [p.name for p in on.log_paths] == [p.name for p in off.log_paths]
    for a, b in zip(on.log_paths, off.log_paths):
        assert a.read_bytes() == b.read_bytes()
        summary = a.name.replace(".wigle.csv", ".summary.csv")
        assert (a.parent / summary).read_bytes() == (b.parent / summary).read_bytes()
    assert not list((tmp_path / "on" / "logs").glob("drive_*")) and list((tmp_path / "on" / "recon").glob("drive_*"))


def test_alerts_turn_on_and_off_during_a_wardrive(tmp_path):
    drive = Wardrive({"drive_alerts": False}, Gps(FIX), root=tmp_path, adapters=ADAPTERS[:1], fleet=Fleet,
                     clock=lambda: NOW)
    drive.start()
    assert drive.watch is None
    drive.set_alerts({"drive_alerts": True, "drive_alert_types": ["alpr"]})
    assert drive.watch is not None and drive.watch.settings["types"] == {"alpr"}
    drive.set_alerts({"drive_alerts": False})
    assert drive.watch is None
    drive.stop()


def test_rtttl_follows_dragon_detector():
    notes = tones.parse_rtttl("Test:d=4,o=5,b=120:8c#,p,16a.6,b6.")
    assert len(notes) == 4 and notes[0] == (554, 250) and notes[1][0] == 0 and notes[2][1] == 187
    assert tones.parse_rtttl("") == []
    for bad in ("a", "a:", "a:d", "a:d=", "a:d=4", "a::", "a:d=0:c", "a:o=3:c", "a:b=0:c", "a:b=901:c",
                "a:d=4,d=8:c", "a::c#8", "a::x", "a::c,", "a::c..", "a::p#", "a::b#", "a::4", "a::0c", "a::c\n",
                "a:b=25:1c,1c", "a:d=4,:c", "x" * 33 + "::c", "a::" + "c," * 96 + "c", "a::" + "c" * 600):
        assert tones.parse_rtttl(bad) is None, bad
    assert all(tones.parse_rtttl(text) for _, _, text in tones.MELODIES)
    rnd = random.Random(1)
    alphabet = "abcdefgp#.,:=0123456789dob \n"
    for _ in range(50000):
        tones.parse_rtttl("".join(rnd.choice(alphabet) for _ in range(rnd.randrange(24))))


def dominant(samples):
    spectrum = np.abs(np.fft.rfft(samples * np.hanning(len(samples))))
    return int(np.argmax(spectrum) * tones.RATE / len(samples))


def test_every_tone_is_a_valid_wav_and_the_siren_rises_and_falls():
    for tone in tones.ORDER:
        if tone == "silent":
            continue
        with wave.open(io.BytesIO(tones.wav_bytes(tone)), "rb") as w:
            assert (w.getnchannels(), w.getsampwidth(), w.getframerate()) == (1, 2, tones.RATE)
            assert 0 < w.getnframes() < tones.RATE * 4
    samples = tones.render("siren").astype(float)
    window = tones.RATE // 20
    pitches = [dominant(samples[i:i + window]) for i in range(0, len(samples) - window, window)]
    assert max(pitches) - min(pitches) > 600 and len(set(p // 100 for p in pitches)) > 5
    rising = pitches[1] < pitches[len(pitches) // 4] and pitches[len(pitches) // 4 + 2] > pitches[len(pitches) // 2]
    assert rising
    assert np.abs(tones.render("siren", "low")).max() < np.abs(tones.render("siren", "high")).max()
    assert set(tones.ORDER[:6]) == {"siren", "yelp", "chirp", "shutter", "ping", "blip"} and tones.ORDER[-1] == "silent"


class Child:
    def __init__(self, argv, **kwargs):
        self.argv, self.code, self.ended = argv, None, False

    def poll(self):
        return self.code

    def terminate(self):
        self.ended, self.code = True, -15


def test_the_tone_queue_plays_one_at_a_time_by_priority(tmp_path, monkeypatch):
    queue = tones.ToneQueue()
    assert queue.request("flock", "a", 90, 0) and queue.request("axon", "b", 99, 0)
    assert queue.take(0)[0] == "axon" and queue.take(0)[0] == "flock"
    assert not queue.request("flock", "a", 90, 3) and queue.request("flock", "a", 90, 5)
    assert queue.take(16) is None
    monkeypatch.setattr(tones, "find_player", lambda: "/usr/bin/aplay")
    started = []

    def popen(argv, **kwargs):
        started.append(Child(argv))
        return started[-1]
    clock = Clock()
    player = tones.Player(tmp_path, popen, clock)
    player._thread = object()
    assert player.play("chirp", kind="flock", priority=90) and player.play("ping", kind="glasses", priority=90)
    assert player.step() == "flock" and player.step() is None and len(started) == 1
    assert started[0].argv[-1].endswith("chirp-high.wav") and (tmp_path / "chirp-high.wav").exists()
    player.play("siren", kind="axon", priority=99)
    assert player.step() == "axon" and started[0].ended and len(started) == 2
    started[1].code = 0
    clock.advance(1)
    assert player.step() == "glasses"
    assert not player.play("silent") and player.play("blip", preview=True)
    started[2].code = 0
    assert player.step() == "preview"
    monkeypatch.setattr(tones, "find_player", lambda: None)
    assert not tones.Player(tmp_path).play("siren")


def test_a_failed_tone_backs_off_and_tries_again(tmp_path, monkeypatch):
    monkeypatch.setattr(tones, "find_player", lambda: "/usr/bin/aplay")
    started = []

    def popen(argv, **kwargs):
        started.append(Child(argv))
        return started[-1]
    clock = Clock()
    player = tones.Player(tmp_path, popen, clock)
    player._thread = object()
    assert player.play("chirp", kind="flock", priority=90) and player.step() == "flock"
    started[0].code = 1
    assert player.play("siren", kind="axon", priority=99) and player.step() is None
    assert player.available() and not player.failed
    clock.advance(tones.RETRY_SECONDS[0])
    assert player.step() == "axon"
    started[1].code = 0
    clock.advance(5)
    assert player.play("ping", kind="glasses") and player.step() == "glasses" and player.failures == 0

    def missing(argv, **kwargs):
        raise OSError("no aplay")
    broken = tones.Player(tmp_path, missing, clock)
    broken._thread = object()
    assert broken.play("chirp") and broken.step() is None and broken.failed and not broken.available()


def test_recon_asks_for_its_beep_without_waiting(monkeypatch):
    asked = []

    class Queue:
        def play(self, *args, **kwargs):
            asked.append(args)
            return True
    assert AlertSound(Queue()).play() and asked == [("beep", "high")]


def test_recon_knows_what_squachwatch_and_dragon_detector_know():
    keys = [row[0] for row in VENDOR_PREFIXES]
    assert keys == sorted(set(keys)) and 0xbcddc2 not in keys and len(keys) == 238
    assert recon.classify_wifi("00:30:44:00:00:01", "")["category"] == "fleet"
    assert recon.classify_wifi("B8:59:C8:00:00:01", "")["category"] == "camera"
    sonos = recon.classify_wifi("34:7E:5C:00:00:01", "")
    assert sonos["category"] == "listener" and sonos["vendor"] == "Sonos"

    def ble(manufacturer=None, uuids=(), address="C1:22:33:44:55:66", kind=1):
        return recon.classify_ble(address, kind, recon.advertisement_from_properties(
            uuids=uuids, manufacturer=manufacturer))
    near = ble({0x004C: bytes([0x12, 0x02, 0x24, 0x01])})
    assert near["category"] == "airtag" and near["confidence"] == recon.MEDIUM and near["owner"] == "with owner"
    unpaired = ble({0x004C: bytes([0x07, 0x19, 0x05, 0x00, 0x55, 0x10, 0x00])})
    assert unpaired["category"] == "airtag" and unpaired["owner"] == "not set up"
    assert ble({0x004C: bytes([0x12, 0x19]) + bytes(25)})["owner"] == "away from owner"
    homepod = ble({0x004C: bytes([0x08, 0x07, 1, 2, 3, 4, 0x00, 0x07, 9])})
    assert homepod["category"] == "listener" and homepod["vendor"] == "Apple"
    assert ble({0x05A7: b"\x01"})["category"] == "listener"
    assert ble(uuids=["0000fe07-0000-1000-8000-00805f9b34fb"])["category"] == "listener"
    flipper = ble({0x0E29: b"\x01"})
    assert flipper["category"] == "hacker" and flipper["confidence"] == recon.MEDIUM
    assert recon.classify_wifi("0C:FA:22:00:00:01", "")["category"] == "hacker"
    for name in ("VIOFO_A139", "ABCD-123456-ABCDE", "MV1234567", "cam_VANTRUE_x"):
        assert recon.classify_wifi("AA:BB:CC:00:00:01", name)["category"] == "camera", name
    for name in ("abcd-123456-ABCDE", "MV12345", "viofo"):
        assert recon.classify_wifi("AA:BB:CC:00:00:01", name)["category"] is None, name
    assert recon.classify_wifi("AA:BB:CC:00:00:01", "GoogleHome1234")["category"] == "listener"
    fox = bytes([0x16, 0x21]) + recon._FOX + bytes([1, 0, 0, 0, 0])
    assert recon.classify_ble("C1:22:33:44:55:66", 1, fox)["category"] == "fox"
    assert recon.classify_ble("BC:DD:C2:11:22:33", 0, bytes([2, 1, 6]))["category"] is None


@pytest.mark.parametrize("tone", ["siren", "chirp", "ping"])
def test_the_three_defaults_sound_different(tone):
    others = {"siren", "chirp", "ping"} - {tone}
    mine = tones.render(tone).astype(float)
    for other in others:
        theirs = tones.render(other).astype(float)
        assert len(mine) != len(theirs) or not np.allclose(mine, theirs)
    assert struct.calcsize("<h") == 2


def test_settings_list_drive_alerts_and_pick_tones_with_a_preview(tmp_path, drawn):
    from tests.test_ui import make_app, press
    from quartermaster.ui import recon_screens, screens
    app = make_app(tmp_path)
    app.player = Player()
    press(app, "S")
    app.screen.index = next(i for i, item in enumerate(app.screen.items) if item[0] == "drive_alerts")
    press(app)
    assert "DRIVE ALERTS" in drawn and "Alerts while wardriving" in drawn
    app.screen.index = next(i for i, item in enumerate(app.screen.items) if item[0] == "drive_alert_types")
    press(app)
    assert "4 of %d" % len(drive_watch.TYPES) in drawn
    press(app, "ENTER")
    assert isinstance(app.screen, recon_screens.AlertTypes) and "Chirp" in drawn and "Siren" in drawn
    press(app, "RIGHT")
    assert app.cfg["drive_alert_sounds"]["flock"] == "shutter" and app.player.calls == [("shutter", None)]
    press(app, "DOWN", "T", "P", "DOWN", "DOWN", "DOWN", "ENTER")
    assert app.cfg["drive_alert_mute"] == ["axon"] and "alpr" in app.cfg["drive_alert_types"]
    assert app.player.calls[-1] == ("siren", None)
    press(app, "D")
    assert isinstance(app.screen, screens.Confirm)
    press(app, "Y")
    assert app.cfg["drive_alert_types"] == drive_watch.DEFAULT_TYPES and app.cfg["drive_alert_mute"] == []
    assert config.load(tmp_path / "config.json")["drive_alert_sounds"]["flock"] == "chirp"
    press(app, "ESC")
    app.screen.index = next(i for i, item in enumerate(app.screen.items) if item[0] == "drive_watch")
    press(app, "ENTER", "A")
    app.keyboard.type("veh*")
    press(app, "ENTER")
    assert app.cfg["drive_watch"] == ["veh*"]


def test_the_wardrive_banner_never_takes_a_key(tmp_path, drawn):
    from tests.test_ui import make_app, press
    from quartermaster.ui import recon_screens, screens
    Fleet.found = networks(30, seed=8) + [wifi("B4:1E:52:00:00:01", "Flock-1A")]
    try:
        app = make_app(tmp_path)
        press(app, "W")
    finally:
        Fleet.found = None
    assert app.drive.watch.idle()
    drawn.clear()
    press(app)
    assert "Flock / accessory candidate" in drawn and "!1" in drawn and "A to list" in drawn
    press(app, "A")
    assert isinstance(app.screen, recon_screens.DriveAlerts)
    press(app, "ENTER")
    assert isinstance(app.screen, recon_screens.DriveAlertDetail)
    press(app, "ESC", "ESC", "ENTER")
    assert isinstance(app.screen, screens.Confirm)
    press(app, "ENTER")
    assert app.toast_text == "Alerts on this drive: 1 flock" and not app.wardriving


class Dragon:
    """A Dragon Detector on USB: anything it does not know gets its list of commands."""

    plugged, taken = True, False

    def __init__(self):
        self.out, self.heard = bytearray(), bytearray()

    def tick(self):
        pass

    def reset(self):
        pass

    def receive(self, data):
        self.heard += data
        if data.startswith(b"badge info"):
            self.out += b"BADGE_INFO 2.4.1 320x170 READY s3\r\n"
        else:
            self.out += b"Commands: status, contacts, selftest, resume, screen radar, screenshot\r\n"


def test_a_dragon_detector_plugged_in_feeds_the_drive_alerts(tmp_path):
    from tests.test_nodes import NATIVE, Rig
    dragon = Dragon()
    rig = Rig(tmp_path, {"ttyACM0": ("303a:1001", NATIVE, dragon)})
    rig.manager.scan()
    assert len(rig.manager.dragons) == 1 and not rig.manager.links
    heard = []
    rig.manager.dragon_sink = heard.append
    dragon.out += b"ALERT Flock: -58 dBm / Flock Safety OUI; product/activity unverified\r\n"
    rig.manager.pump()
    assert [a["category"] for a in heard] == ["flock"]
    rig.manager.scan()
    assert len(rig.manager.dragons) == 1 and len(rig.bus.ports) == 1
    rig.manager.stop()
    assert not rig.manager.dragons
