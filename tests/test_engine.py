import json

from quartermaster.engine import Wardrive
from quartermaster.wifi import parse_iw_scan
from tests.test_core import SCAN, FIX, NOW


class FakeGPS:
    def __init__(self, fix):
        self.fix = fix

    def snapshot(self):
        return dict(self.fix)


class FakeFleet:
    def __init__(self, adapters, sink, interval):
        self.adapters, self.sink = adapters, sink

    def start(self):
        for adapter in self.adapters:
            self.sink(parse_iw_scan(SCAN), adapter)
            self.sink(parse_iw_scan(SCAN), adapter)   # same sweep again: deduplicated

    def stop(self):
        pass

    def status(self):
        return []


ADAPTERS = [{"interface": "wlan1", "external": True, "bands": ["2.4", "5", "6"], "phy": "phy1"},
            {"interface": "wlan0", "external": False, "bands": ["2.4", "5"], "phy": "phy0"}]


def test_wardrive_dedups_across_adapters_and_logs(tmp_path):
    drive = Wardrive({"ble": False}, FakeGPS(FIX), root=tmp_path, adapters=ADAPTERS, fleet=FakeFleet,
                     clock=lambda: NOW)
    drive.start()
    status = drive.status()
    assert status["wifi"] == 3 and status["rows"] == 3 and status["bands"] == {"2.4": 1, "5": 1, "6": 1}
    drive.stop()
    lines = drive.log_path.read_text().splitlines()
    assert len(lines) == 5 and drive.log_path.name == "quartermaster_20261118-110640.wigle.csv"
    sidecar = json.loads(drive.log_path.with_suffix("").with_suffix(".devices.json").read_text())
    assert len(sidecar["devices"]) == 3 and sidecar["devices"][0]["strongest"]["rssi"] is not None


def test_no_fix_means_no_rows_but_devices_still_counted(tmp_path):
    drive = Wardrive({"ble": False, "wifi_adapters": "external"}, FakeGPS({"valid": False}), root=tmp_path,
                     adapters=ADAPTERS, fleet=FakeFleet, clock=lambda: NOW)
    drive.start()
    assert drive.status()["wifi"] == 3 and drive.status()["rows"] == 0 and drive.log_path is None
    assert drive.skipped_no_fix == 6
    drive.stop()
