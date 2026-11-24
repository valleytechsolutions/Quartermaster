"""The kinds of device in Recon and Drive Alerts are SquachWatch's detection types, and saved settings follow them."""

import json
import time

from quartermaster import config, dragon_link, drive_watch, lora, lorawan, mesh, recon, tones
from quartermaster.engine import Wardrive
from quartermaster.ui import recon_screens
from tests.fakes import ADAPTERS, FIX, FakeCap, Fleet, Gps, NoBle
from tests.test_core import NOW
from tests.test_drive_watch import watch, wifi

SQUACHWATCH = ("flock", "axon", "glasses", "skimmer", "raven", "airtag", "drone", "alpr", "camera", "samsung_tag",
               "google_tag", "tile", "ring", "deauth", "eviltwin", "ibeacon", "hacker", "lorawan", "mine", "listener")


def ble(manufacturer=None, uuids=(), service_data=None, name=None):
    return recon.classify_ble("C1:22:33:44:55:66", 1, recon.advertisement_from_properties(
        name, uuids, manufacturer, service_data))


def uuid16(short):
    return "0000%04x-0000-1000-8000-00805f9b34fb" % short


def test_the_kinds_are_squachwatchs_twenty_with_flock_axon_glasses_and_my_alerts_first():
    assert drive_watch.SQUACHWATCH_ORDER == SQUACHWATCH and len(set(SQUACHWATCH)) == 20
    assert drive_watch.TYPES == ("flock", "axon", "glasses", "mine") + tuple(
        k for k in SQUACHWATCH if k not in ("flock", "axon", "glasses", "mine"))
    for table in (drive_watch.LABELS, drive_watch.SHORT, drive_watch.NAMES, drive_watch.PRIORITY,
                  drive_watch.DEFAULT_TONES, recon_screens.CATEGORY_COLOR):
        assert set(table) == set(SQUACHWATCH)
    assert recon.CATEGORIES == tuple(k for k in SQUACHWATCH if k not in ("deauth", "eviltwin", "lorawan", "mine"))
    assert drive_watch.NAMES["glasses"] == drive_watch.LABELS["glasses"] == "Meta glasses"
    assert drive_watch.LABELS["hacker"] == "Hacker hardware" and drive_watch.LABELS["lorawan"] == "LoRaWAN network"
    assert drive_watch.SHORT["samsung_tag"] == "SMARTTAG" and drive_watch.SHORT["google_tag"] == "FIND HUB"
    assert drive_watch.DEFAULT_TYPES == ["flock", "axon", "glasses", "mine"]
    assert {k for k, v in drive_watch.DEFAULT_TONES.items() if v != "blip"} == {"axon", "flock", "glasses"}
    assert (drive_watch.DEFAULT_TONES["axon"], drive_watch.DEFAULT_TONES["flock"],
            drive_watch.DEFAULT_TONES["glasses"]) == ("siren", "chirp", "ping")
    assert recon.OFF_BY_DEFAULT == ("ibeacon", "listener")
    labels = list(drive_watch.LABELS.values())
    assert len(labels) == len(set(labels)) and not any("Dragon" in label or "candidate" in label for label in labels)


def test_trackers_split_into_samsung_google_and_tile_as_squachwatch_does():
    assert ble(uuids=[uuid16(0xfd5a)])["category"] == "samsung_tag"
    assert ble(name="SmartTag2")["category"] == "samsung_tag"
    hub = ble(service_data={uuid16(0xfeaa): bytes([0x40]) + bytes(20)})
    assert (hub["category"], hub["confidence"]) == ("google_tag", recon.STRONG)
    assert ble(uuids=[uuid16(0xfeaa)])["category"] == "google_tag"
    assert ble({0x08C3: b"\x01"})["category"] == "google_tag"
    assert ble(uuids=[uuid16(0xfe33)])["category"] == "google_tag"
    dult = ble(uuids=[uuid16(0xfcb2)])
    assert (dult["category"], dult["evidence"]) == ("google_tag", "DULT tracker service; owner unknown")
    for short in (0xfeed, 0xfeec, 0xfd84):
        assert ble(uuids=[uuid16(short)])["category"] == "tile"
    assert ble({0x004C: bytes([0x12, 0x19]) + bytes(25)})["category"] == "airtag"
    assert ble({0x004C: bytes([0x07, 0x19]) + bytes(25)})["category"] == "airtag"
    assert ble(name="AirTag")["category"] == "airtag"
    for short, kind in ((0xfd5a, "samsung_tag"), (0xfeed, "tile"), (0xfcb2, "google_tag"), (0xfeaa, "google_tag")):
        assert drive_watch.ble_hint({"uuids": [uuid16(short)]}) == kind


def test_fleet_routers_are_axon_but_never_alert_at_the_default_evidence(tmp_path):
    router = wifi("00:30:44:00:00:01", "IBR900-123")
    w = watch(tmp_path, drive_alert_types=["axon"])
    for _ in range(3):
        w.offer_wifi([router])
        w.drain()
    assert w.alert_list() == [] and w.player.calls == []
    w.stop()
    rows = [json.loads(line) for line in w.log_path.read_text().splitlines()]
    assert rows[0]["category"] == "axon" and rows[0]["vendor"] == "CradlePoint fleet router" and not rows[0]["alert"]
    assert rows[0]["confidence"] == "low"


def test_the_dragon_fox_is_a_hacker_entry_that_never_alerts_by_default(tmp_path):
    ad = bytes([0x16, 0x21]) + recon._FOX + bytes([1, 0, 0, 0, 0])
    fox = {"address": "C1:22:33:44:55:01", "address_type": 1, "rssi": -50, "name": "", "uuids": [],
           "manufacturer": {}, "service_data": {}}
    w = watch(tmp_path, drive_alert_types=["hacker"])
    w.offer_ble(dict(fox, raw_ad=ad))
    w.drain()
    w.offer_ble(dict(fox, raw_ad=ad))
    w.drain()
    assert w.alert_list() == []
    alert = dragon_link.parse("ALERT Dragon fox: FOX-1 / -60 dBm / anything")
    assert (alert["category"], alert["confidence"]) == ("hacker", recon.LOW)
    w.offer_external(alert)
    w.drain()
    assert w.alert_list() == []
    w.stop()
    rows = [json.loads(line) for line in w.log_path.read_text().splitlines()]
    assert {row["category"] for row in rows} == {"hacker"} and not any(row["alert"] for row in rows)


def test_dragon_detector_titles_land_on_squachwatch_types(tmp_path):
    assert set(dragon_link.TITLES.values()) <= set(SQUACHWATCH)
    assert dragon_link.parse("ALERT SmartTag: -60 dBm / x")["category"] == "samsung_tag"
    assert dragon_link.parse("ALERT Google Find Hub: -60 dBm / x")["category"] == "google_tag"
    assert dragon_link.parse("ALERT Tile: -60 dBm / x")["category"] == "tile"
    burst = dragon_link.parse("ALERT BLE advert burst: -60 dBm / 30 adverts")
    assert (burst["category"], burst["incident"]) == ("hacker", "burst")
    w = watch(tmp_path, drive_alert_types=["hacker"])
    w.offer_external(burst)
    w.offer_external(burst)
    w.drain()
    shown = w.alert_list()
    assert [(a["category"], a["incident"]) for a in shown] == [("hacker", "burst")]
    assert not recon_screens.DriveAlertDetail(shown[0]).silenceable()


def test_settings_saved_by_earlier_versions_take_the_new_kinds(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({
        "drive_alert_types": ["flock", "tracker", "fleet", "burst", "fox", 7, "nonsense"],
        "drive_alert_mute": ["fleet", "tracker"],
        "recon_categories": ["axon", "tracker", "fox", "fleet"],
        "drive_alert_sounds": {"axon": "dd_axon", "flock": "yelp", "tracker": "ping", "fleet": "siren",
                               "burst": "dd_burst", "glasses": "dd_glasses", "fox": 3}}))
    cfg = config.load(path)
    assert cfg["drive_alert_types"] == ["flock", "axon", "samsung_tag", "google_tag", "tile", "hacker"]
    assert cfg["drive_alert_mute"] == ["samsung_tag", "google_tag", "tile"]
    assert cfg["recon_categories"] == ["axon", "samsung_tag", "google_tag", "tile", "hacker"]
    sounds = cfg["drive_alert_sounds"]
    assert (sounds["axon"], sounds["flock"], sounds["glasses"], sounds["hacker"]) == ("siren", "yelp", "ping", "blip")
    assert sounds["samsung_tag"] == sounds["google_tag"] == sounds["tile"] == "ping"
    assert set(sounds) == set(drive_watch.TYPES) and all(tone in tones.ORDER for tone in sounds.values())
    config.save(cfg, path)
    assert config.load(path) == cfg
    for broken in ([], "x", {"drive_alert_types": "tracker", "drive_alert_sounds": ["dd_fox"]}):
        path.write_text(json.dumps(broken))
        assert config.load(path)["drive_alert_types"] == drive_watch.DEFAULT_TYPES
    fresh = config.load(tmp_path / "none.json")
    assert "ibeacon" not in fresh["recon_categories"] and "listener" not in fresh["recon_categories"]
    assert fresh["drive_alert_sounds"] == drive_watch.DEFAULT_TONES


def test_a_tone_from_an_earlier_version_plays_the_default_of_its_kind(tmp_path):
    from tests.test_ui import make_app
    app = make_app(tmp_path)
    app.cfg["drive_alert_sounds"] = dict(app.cfg["drive_alert_sounds"], axon="dd_axon", flock="dd_flock")
    assert recon_screens.tone_of(app, "axon") == "siren" and recon_screens.tone_of(app, "flock") == "chirp"


def frame(mtype, address):
    return bytes([mtype << 5]) + address.to_bytes(4, "little") + bytes([0, 7, 0, 2]) + bytes(range(0xA0, 0xA4)) + \
        bytes(range(0xD0, 0xD4))


TTN, ORESUND = 0x26011B2C, 0xE0141234


def test_a_lorawan_address_names_its_operator_as_squachwatch_reads_it():
    assert lorawan.lookup(TTN) == (0x13, "The Things Network", True)
    assert lorawan.lookup(0x27FFFFFF)[1] == "The Things Network"
    assert lorawan.lookup(0x78000001)[1] == "Helium"
    assert lorawan.lookup(0xE05A0001) == (0x60002D, "Decentralized Wireless Foundation Inc", False)
    assert lorawan.lookup(0xFC014C01)[1] == "Decentralized Wireless Foundation Inc"
    assert lorawan.lookup(0xFC016001)[1] == "The Things Network Foundation"
    assert lorawan.lookup(0x74123456)[1] == "Minol ZENNER"
    assert "rivate" in lorawan.lookup(0x00000001)[1]
    assert lorawan.lookup(0x5E000000) is None
    assert lorawan.lookup(0x1E000001)[1] == "Orange"
    assert lorawan.lookup(0xFE002001) is not None
    assert lorawan.lookup(ORESUND) == (0x60000A, "Öresundskraft", False)
    assert len(lorawan.REGISTRY) == 143 and lorawan.address_for(0x13) == "02:4C:57:00:00:13"
    assert lorawan.dev_addr(frame(2, TTN)) == (2, TTN)
    for bad in (b"", frame(2, TTN)[:11], bytes([0x00]) + bytes(22), bytes([0x40 | 1]) + bytes(16),
                bytes([0xE0]) + bytes(16), bytes([0x40, 1, 2, 3, 4, 0x0F]) + bytes(10)):
        assert lorawan.dev_addr(bad) is None


def test_an_uplink_counts_at_once_and_a_downlink_needs_a_second():
    operators = lorawan.Operators()
    hit = operators.note(frame(lorawan.UNCONF_UP, TTN), -88.0, 1.0)
    assert hit == {"net_id": 0x13, "operator": "The Things Network", "address": "02:4C:57:00:00:13",
                   "rssi": -88.0, "exact": True}
    assert operators.note(frame(lorawan.UNCONF_DOWN, ORESUND), -101, 1.0) is None
    second = operators.note(frame(lorawan.UNCONF_DOWN, ORESUND + 0x10000), -97, 61.0)
    assert second["operator"] == "Öresundskraft" and not second["exact"]
    late = lorawan.Operators()
    late.note(frame(lorawan.UNCONF_DOWN, ORESUND), -101, 0.0)
    assert late.note(frame(lorawan.UNCONF_DOWN, ORESUND), -101, lorawan.CONFIRM_SECONDS + 1) is None
    assert late.note(frame(lorawan.CONF_DOWN, ORESUND), -101, lorawan.CONFIRM_SECONDS + 5) is not None
    nobody = lorawan.Operators()
    for address in (0x00000000, 0x03ABCDEF, 0x0A123456):
        assert nobody.note(frame(lorawan.UNCONF_UP, address), -60, 1.0) is None
    assert nobody.note(bytes(23), -60, 1.0) is None
    crowd = lorawan.Operators()
    named = sum(crowd.note(frame(lorawan.UNCONF_UP, nwk << 25 | 0x1234), -60, nwk) is not None for nwk in range(2, 64))
    assert named > lorawan.CAP and len(crowd.heard) == lorawan.CAP


def test_lorawan_uplinks_are_one_more_setting_to_listen_on():
    assert lora.plan("US") == lora.plan("US", lorawan_too=False) and len(lora.plan("US")) == 2
    us = lora.plan("US", lorawan_too=True)
    assert us[:2] == lora.plan("US") and (us[2].protocol, us[2].frequency_hz, us[2].bandwidth_hz,
                                          us[2].spreading_factor, us[2].sync_word) == ("lorawan", 903900000, 125000,
                                                                                        7, 0x34)
    assert lora.plan("GB", True)[-1].frequency_hz == 868100000 and lora.plan("AU", True)[-1].frequency_hz == 916800000
    assert lora.plan("JP", True) == [] and us[2].short == "LoRaWAN uplinks 903.900 MHz"


def wait_for(test, seconds=5):
    deadline = time.monotonic() + seconds
    while not test() and time.monotonic() < deadline:
        time.sleep(0.002)
    assert test()


def test_the_listener_hands_lorawan_operators_on_and_keeps_them_out_of_the_mesh_file(tmp_path):
    cap = FakeCap()
    heard = []
    plan = [lora.lorawan("US")]
    listener = mesh.Listener(tmp_path, Gps(FIX), cap.radio, plan, clock=lambda: NOW, dwell=0.05)
    listener.lorawan_sink = heard.append
    listener.start(NOW)
    cap.the_radio.hear(frame(lorawan.UNCONF_UP, TTN), rssi=-90)
    cap.the_radio.hear(frame(lorawan.UNCONF_UP, TTN), rssi=-80, crc=False)
    cap.the_radio.hear(b"\x40" * 5)
    wait_for(lambda: not cap.the_radio.packets)
    wait_for(lambda: len(heard) == 1)
    us = lora.plan("US", lorawan_too=True)
    listener.replan(us)
    wait_for(lambda: listener.plan == us)
    listener.stop()
    assert heard[0]["operator"] == "The Things Network" and listener.packets == 0 and len(listener.mesh) == 0
    assert listener.profile == us[2]


def drive(tmp_path, **settings):
    cfg = dict(config.DEFAULTS, ble=False, **settings)
    return Wardrive(cfg, Gps(FIX), root=tmp_path, adapters=ADAPTERS, ble=NoBle(), fleet=Fleet, clock=lambda: NOW,
                    cap=FakeCap())


def test_a_wardrive_listens_for_lorawan_only_while_lorawan_networks_alert(tmp_path):
    off = drive(tmp_path / "off")
    off.start()
    assert off.mesh.plan == lora.plan("US")
    off.set_alerts({"drive_alert_types": ["flock", "lorawan"]})
    wait_for(lambda: off.mesh.plan == lora.plan("US", lorawan_too=True))
    off.set_alerts({"drive_alerts": False})
    wait_for(lambda: off.mesh.plan == lora.plan("US"))
    off.stop()
    on = drive(tmp_path / "on", drive_alert_types=["lorawan"])
    on.start()
    assert on.mesh.plan == lora.plan("US", lorawan_too=True)
    hit = lorawan.Operators().note(frame(lorawan.UNCONF_UP, TTN), -90, 1.0)
    for _ in range(3):
        on.mesh.lorawan_sink(dict(hit))
    assert on.watch.idle()
    on.stop()
    shown = on.watch.alert_list()
    assert len(shown) == 1 and shown[0]["category"] == "lorawan" and shown[0]["name"] == "The Things Network"
    assert shown[0]["label"] == "LoRaWAN network" and shown[0]["address"] == "02:4C:57:00:00:13"
    assert shown[0]["confidence"] == recon.STRONG and shown[0]["transport"] == "lora"
    assert not list((tmp_path / "on" / "logs").glob("*.mesh.*"))


def test_a_moving_fleet_router_or_fox_never_raises_following_in_recon(tmp_path):
    from quartermaster.recon_engine import Recon
    from tests.fakes import QuietFleet

    class Moving(Gps):
        lat = 37.0

        def snapshot(self):
            return {"valid": True, "latitude": self.lat, "longitude": -122.0}

    cfg = config.load(tmp_path / "config.json")
    gps = Moving()
    r = Recon(cfg, gps, root=tmp_path, adapters=ADAPTERS, ble=NoBle(), fleet=QuietFleet, clock=lambda: 0)
    r.log_path = tmp_path / "recon.jsonl"
    fox = bytes([0x16, 0x21]) + recon._FOX + bytes([1, 0, 0, 0, 0])
    t = 1000.0
    for _ in range(15):
        r.observe("wifi", {"bssid": "00:30:44:AA:BB:01", "ssid": "IBR900-B01", "rssi": -50, "channel": 6}, now=t)
        r.observe("ble", {"address": "C1:22:33:44:55:01", "address_type": 1, "rssi": -50, "raw_ad": fox}, now=t)
        gps.lat += 0.002
        t += 70
    assert r.devices[("wifi", "00:30:44:AA:BB:01")]["follow"]
    assert r.devices[("wifi", "00:30:44:AA:BB:01")]["category"] == "axon"
    assert r.devices[("ble", "C1:22:33:44:55:01")]["category"] == "hacker"
    assert list(r.alerts) == []


def test_the_fox_wins_over_a_camera_address_and_stays_a_weak_hint():
    ad = bytes([0x16, 0x21]) + recon._FOX + bytes([1, 0, 0, 0, 0])
    for address in ("B4:1E:52:00:00:01", "00:25:DF:00:00:01"):
        result = recon.classify_ble(address, 0, ad)
        assert (result["category"], result["confidence"]) == ("hacker", recon.LOW)
        assert result["vendor"] == "Dragon fox test beacon"


def test_hacker_hardware_sends_apple_adverts_to_the_slow_line(tmp_path):
    apple = {"address": "C1:22:33:44:55:02", "address_type": 1, "rssi": -50, "name": "", "uuids": [],
             "manufacturer": {0x004C: bytes([0x10, 5, 1, 2, 3, 4, 5])}, "service_data": {}}
    w = watch(tmp_path, drive_alert_types=["hacker"])
    w.offer_ble(apple)
    assert (len(w.urgent), len(w.rest)) == (0, 1)
    w.stop()
    w = watch(tmp_path, drive_alert_types=["flock"])
    w.offer_ble(apple)
    assert (len(w.urgent), len(w.rest)) == (0, 0)
    w.stop()
    w = watch(tmp_path, drive_alert_types=["axon"])
    w.offer_wifi([wifi("00:30:44:00:00:01", "IBR900-123")])
    assert [item[0] for item in w.urgent] == []
    w.stop()


def test_recon_lists_all_twenty_with_four_wardrive_only(tmp_path, drawn):
    from tests.test_ui import make_app, press, screen_text
    app = make_app(tmp_path)
    press(app, "R")
    assert "14 of 16 kinds, 4 Wardrive only" in screen_text(app, drawn)
    press(app, "T")
    screen = app.screen
    assert isinstance(screen, recon_screens.Categories)
    seen = []
    for _ in SQUACHWATCH:
        seen.append(drive_watch.SQUACHWATCH_ORDER[screen.index])
        before = list(app.cfg["recon_categories"])
        if seen[-1] not in recon.CATEGORIES:
            assert "Wardrive only" in screen_text(app, drawn)
            press(app, "ENTER")
            assert app.cfg["recon_categories"] == before
        press(app, "DOWN")
    assert seen == list(SQUACHWATCH)


def test_a_lorawan_alert_shows_its_netid(tmp_path, drawn):
    from tests.test_ui import make_app, screen_text
    app = make_app(tmp_path)
    shown = {"time": time.time(), "category": "lorawan", "label": "LoRaWAN network", "confidence": recon.STRONG,
             "address": lorawan.address_for(0x000013), "name": "The Things Network", "count": 1}
    app.push(recon_screens.DriveAlertDetail(shown))
    text = screen_text(app, drawn)
    assert "NetID" in text and "000013" in text and "02:4C:57" not in text
