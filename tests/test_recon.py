import json
import struct

import pytest

from quartermaster import radios, recon
from quartermaster.engine import Wardrive
from quartermaster.recon_engine import Recon
from tests.test_core import FIX, NOW
from tests.test_engine import ADAPTERS, FakeGPS


class QuietFleet:
    def __init__(self, adapters, sink, interval):
        self.adapters, self.sink = adapters, sink

    def start(self):
        pass

    def stop(self):
        pass

    def status(self):
        return [{"interface": a["interface"], "error": ""} for a in self.adapters]


class NoBle:
    def scan(self):
        return []

    def close(self):
        pass


class Sound:
    played = 0

    def play(self):
        Sound.played += 1


def make(tmp_path, cfg=None, gps=None, clock=None):
    Sound.played = 0
    settings = {"recon_categories": list(recon.CATEGORIES), "recon_display_min": "low", "recon_alert_min": "medium",
                "recon_sound": True}
    settings.update(cfg or {})
    return Recon(settings, gps or FakeGPS(FIX), root=tmp_path, adapters=ADAPTERS, ble=NoBle(), fleet=QuietFleet,
                 sound=Sound(), clock=clock or (lambda: NOW))


def wifi(bssid, ssid="", rssi=-60):
    return {"bssid": bssid, "ssid": ssid, "rssi": rssi, "channel": 6, "frequency": 2437, "security": "WPA2",
            "band": "2.4"}


def test_classifier_grades_the_known_families():
    assert recon.classify_wifi("00:25:DF:11:22:33", "")["category"] == "axon"
    assert recon.classify_wifi("B4:1E:52:00:00:01", "Flock-1A2B")["category"] == "flock"
    ring = recon.classify_wifi("00:B4:63:00:00:01", "Ring Setup 1f")
    assert ring["category"] == "ring" and ring["confidence"] == recon.MEDIUM
    assert recon.classify_wifi("AA:BB:CC:00:00:01", "My Home WiFi")["category"] is None
    # A locally administered (randomised) address never supplies vendor evidence.
    assert recon.classify_wifi("02:25:DF:11:22:33", "")["category"] is None
    axon = recon.advertisement_from_properties(manufacturer={0x034D: b"\x01\x02"}, uuids=["0000fe6b-0000-1000-8000-00805f9b34fb"])
    graded = recon.classify_ble("11:22:33:44:55:66", 1, axon)
    assert graded["category"] == "axon" and graded["confidence"] == recon.STRONG
    airtag = recon.advertisement_from_properties(manufacturer={0x004C: bytes([0x12, 0x19]) + bytes(25)})
    assert recon.classify_ble("C1:22:33:44:55:66", 1, airtag)["category"] == "airtag"
    beacon = recon.advertisement_from_properties(manufacturer={0x004C: bytes([2, 0x15]) + bytes(16) + struct.pack(">HHb", 1, 2, -59)})
    assert recon.classify_ble("C1:22:33:44:55:66", 1, beacon)["beacon"]["minor"] == 2
    assert len(recon.VENDOR_PREFIXES) > 150 and set(recon.CATEGORY_LABELS) == set(recon.CATEGORIES)


def test_recon_flags_alerts_and_logs_only_flagged_devices(tmp_path):
    r = make(tmp_path)
    r.start()
    r.observe("wifi", wifi("00:25:DF:11:22:33", "", -48))
    r.observe("wifi", wifi("AA:BB:CC:00:00:01", "Coffee Shop"))
    r.observe("ble", {"address": "11:22:33:44:55:66", "address_type": 1, "rssi": -70, "name": "",
                      "manufacturer": {0x004C: bytes([0x12, 0x19]) + bytes(25)}, "uuids": [], "service_data": {}})
    summary = r.summary()
    assert (summary["wifi"], summary["ble"], summary["flagged"]) == (2, 1, 2)
    assert [d["category"] for d in r.device_list()] == ["axon", "airtag"]
    assert len(r.device_list(show_all=True)) == 3
    assert len(r.alerts) == 2 and r.alerts[-1]["category"] == "axon" and Sound.played == 2
    r.observe("wifi", wifi("00:25:DF:11:22:33", "", -40))
    assert len(r.alerts) == 2                                  # one alert per device every five minutes
    assert r.device("wifi", "00:25:DF:11:22:33")["best_rssi"] == -40
    r.stop()
    lines = [json.loads(line) for line in r.log_path.read_text().splitlines()]
    assert {line["address"] for line in lines} == {"00:25:DF:11:22:33", "11:22:33:44:55:66"}
    assert lines[0]["confidence"] == "medium" and lines[0]["receiver"]["latitude"] == FIX["latitude"]
    assert r.log_path.stat().st_mode & 0o777 == 0o600 and r.log_path.parent.name == "recon"


def test_recon_respects_the_watch_list_and_evidence_level(tmp_path):
    r = make(tmp_path, {"recon_categories": ["flock"], "recon_display_min": "medium", "recon_sound": False})
    r.start()
    r.observe("wifi", wifi("00:25:DF:11:22:33"))                     # axon: not on the watch list
    r.observe("wifi", wifi("AA:BB:CC:00:00:02", "Flock-test"))       # flock by name only: weak
    r.observe("wifi", wifi("B4:1E:52:00:00:01", "x"))                # flock OUI: medium
    assert [d["address"] for d in r.device_list()] == ["B4:1E:52:00:00:01"] and Sound.played == 0
    r.stop()


def test_recon_notices_a_device_that_travels_with_you(tmp_path):
    clock = [NOW]
    gps = FakeGPS(dict(FIX))
    r = make(tmp_path, gps=gps, clock=lambda: clock[0])
    r.start()
    tag = {"address": "11:22:33:44:55:66", "address_type": 1, "rssi": -70, "name": "", "uuids": [],
           "manufacturer": {0x004C: bytes([0x12, 0x19]) + bytes(25)}, "service_data": {}}
    for minute in range(14):
        clock[0] = NOW + minute * 60
        gps.fix["latitude"] = FIX["latitude"] + minute * 0.001      # about 110 m further each minute
        r.observe("ble", dict(tag, rssi=-70 - minute % 3))
    device = r.device("ble", "11:22:33:44:55:66")
    assert device["follow"] and r.summary()["following"] == 1 and r.alerts[0]["follow"]
    r.stop()


def test_wardrive_and_recon_never_run_together(tmp_path):
    drive = Wardrive({"ble": False}, FakeGPS(FIX), root=tmp_path, adapters=ADAPTERS, fleet=QuietFleet,
                     clock=lambda: NOW)
    r = make(tmp_path)
    drive.start()
    with pytest.raises(radios.Busy, match="Stop the wardrive first"):
        r.start()
    assert r.state == "idle" and radios.owner() == "wardrive"
    drive.stop()
    r.start()
    with pytest.raises(radios.Busy, match="Stop Recon first"):
        Wardrive({"ble": False}, FakeGPS(FIX), root=tmp_path, adapters=ADAPTERS, fleet=QuietFleet).start()
    r.stop()
    assert radios.owner() is None
