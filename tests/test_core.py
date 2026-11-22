import csv
import io

from quartermaster import hardware, wigle
from quartermaster.gnss import GPS, nmea_fix
from quartermaster.tracker import Tracker, WIFI
from quartermaster.wifi import parse_iw_scan

SCAN = """BSS aa:bb:cc:dd:ee:01(on wlan1)
\tlast seen: 120 ms ago
\tfreq: 2437.0
\tcapability: ESS Privacy ShortSlotTime (0x0411)
\tsignal: -61.00 dBm
\tSSID: Black Pearl, "Guest"
\tDS Parameter set: channel 6
\tRSN:\t * Version: 1
\t\t * Group cipher: CCMP
\t\t * Pairwise ciphers: CCMP
\t\t * Authentication suites: PSK SAE
\t\t * Capabilities: 16-PTKSA-RC 1-GTKSA-RC MFP-capable (0x008c)
\tWPS:\t * Version: 1.0
BSS aa:bb:cc:dd:ee:02(on wlan1)
\tlast seen: 90000 ms ago
\tfreq: 5180
\tcapability: ESS (0x0001)
\tsignal: -80.00 dBm
\tSSID: \\x00\\x00\\x00
BSS aa:bb:cc:dd:ee:03(on wlan1)
\tlast seen: 10 ms ago
\tfreq: 6135
\tcapability: ESS Privacy (0x0011)
\tsignal: -70.00 dBm
\tSSID: caf\\xc3\\xa9
\tRSN:\t * Version: 1
\t\t * Group cipher: CCMP
\t\t * Pairwise ciphers: CCMP
\t\t * Authentication suites: IEEE 802.1X
\tWPA:\t * Version: 1
\t\t * Group cipher: TKIP
\t\t * Pairwise ciphers: TKIP
\t\t * Authentication suites: PSK
"""

PHY = """Wiphy phy1
\tBand 1:
\t\tFrequencies:
\t\t\t* 2412.0 MHz [1] (20.0 dBm)
\tBand 2:
\t\tFrequencies:
\t\t\t* 5180.0 MHz [36] (20.0 dBm)
\tBand 4:
\t\tFrequencies:
\t\t\t* 5955.0 MHz [1] (disabled)
\t\t\t* 6115.0 MHz [33] (12.0 dBm) (no IR)
\tSupported interface modes:
\t\t * managed
\t\t * monitor
\tsoftware interface modes (can always be added):
"""

FIX = {"valid": True, "latitude": 40.1234567, "longitude": -75.7654321, "altMSL": 101.26, "hdop": 0.9,
       "age_seconds": 0.4}
NOW = 1795000000


def test_iw_scan_capabilities_and_bands():
    a, b, c = parse_iw_scan(SCAN)
    assert a["ssid"] == 'Black Pearl, "Guest"' and a["channel"] == 6 and a["band"] == "2.4"
    assert a["capabilities"] == "[WPA2-PSK-CCMP][RSN-SAE-CCMP][WPS][ESS]" and a["security"] == "WPA2/3"
    assert b["hidden"] and b["ssid"] == "" and b["capabilities"] == "[ESS]" and b["channel"] == 36
    assert c["ssid"] == "café" and c["band"] == "6" and c["channel"] == 37
    assert c["capabilities"] == "[WPA-PSK-TKIP][WPA2-EAP-CCMP][ESS]"


def test_phy_bands():
    info = hardware.parse_iw_phy(PHY)
    assert info == {"bands": ["2.4", "5", "6"], "monitor": True}
    assert hardware.band_label(info["bands"]) == "tri-band"


def test_tracker_dedup_strongest_weakest():
    t = Tracker(relog_seconds=30, relog_meters=25, rssi_step=4)
    fix = dict(FIX)
    d, new, why = t.observe(WIFI, "AA", -70, NOW, fix, name="x", band="5", security="WPA2")
    assert new and why == "new"
    assert t.observe(WIFI, "AA", -69, NOW + 1, fix)[1:] == (False, None)
    assert t.observe(WIFI, "AA", -50, NOW + 2, fix)[2] == "stronger"
    far = dict(fix, latitude=40.1244567)
    assert t.observe(WIFI, "AA", -90, NOW + 3, far, name="")[2] == "weaker"
    assert t.observe(WIFI, "AA", -80, NOW + 40, fix)[2] == "moved"
    assert d.best[0] == -50 and d.worst[0] == -90 and d.worst[1] == far["latitude"] and d.name == "x"
    assert d.sightings == 5 and d.rows == 4
    assert t.summary()["wifi"] == 1 and t.summary()["bands"]["5"] == 1
    assert t.observe(WIFI, "BB", -60, NOW, {"valid": False})[2] is None


def test_wigle_file_is_spec_exact(tmp_path):
    path = tmp_path / wigle.log_filename(NOW)
    w = wigle.WigleWriter(path, "13")
    net = parse_iw_scan(SCAN)[0]
    dev = {"mac": net["bssid"], "name": net["ssid"], "capabilities": net["capabilities"],
           "channel": net["channel"], "frequency": net["frequency"]}
    assert w.write("WIFI", dev, -61.0, FIX, NOW)
    assert w.write("BLE", {"mac": "11:22:33:44:55:66", "name": "bad\nname", "mfgr": 76}, -88, FIX, NOW)
    assert not w.write("WIFI", dev, -61, {"valid": False}, NOW)            # no fix
    assert not w.write("WIFI", dev, -61, FIX, 1000)                         # clock not set
    assert not w.write("WIFI", dev, -61, dict(FIX, latitude=0.0, longitude=0.0), NOW)
    w.close()
    lines = path.read_text(encoding="utf-8").split("\n")
    assert lines[0].startswith("WigleWifi-1.6,appRelease=Quartermaster ") and "device=Quartermaster" in lines[0]
    assert lines[1] == ",".join(wigle.COLUMNS)
    rows = list(csv.reader(io.StringIO("\n".join(lines[2:]))))
    assert rows[0] == ["aa:bb:cc:dd:ee:01", 'Black Pearl, "Guest"', "[WPA2-PSK-CCMP][RSN-SAE-CCMP][WPS][ESS]",
                       "2026-11-18 11:06:40", "6", "2437", "-61", "40.1234567", "-75.7654321", "101.3", "4.5",
                       "", "", "WIFI"]
    assert rows[1][1] == "bad name" and rows[1][12] == "76" and rows[1][13] == "BLE"
    assert len(rows) == 2 and all(len(r) == 14 for r in rows) and path.name.startswith("quartermaster_")


def test_gps_keeps_fix_and_hdop():
    gga = b"$GPGGA,123519,4807.038,N,01131.000,E,1,08,0.9,545.4,M,46.9,M,,*47\r\n"
    report = nmea_fix(gga)
    gps = GPS()
    gps.update(report, now=100)
    snap = gps.snapshot(now=101)
    assert snap["valid"] and snap["hdop"] == 0.9 and round(snap["latitude"], 3) == 48.117
    assert not gps.snapshot(now=200)["valid"]


def test_gps_candidates_orders_usb_first(tmp_path):
    tty, dev = tmp_path / "tty", tmp_path / "dev"
    usb = tmp_path / "usb1"
    (usb / "1-1:1.0").mkdir(parents=True)
    (usb / "idVendor").write_text("1a86\n")
    (usb / "idProduct").write_text("7523\n")
    (tty / "ttyUSB0").mkdir(parents=True)
    (tty / "ttyUSB0" / "device").symlink_to(usb / "1-1:1.0")
    dev.mkdir()
    (dev / "ttyS0").write_text("")
    found = hardware.gps_candidates(tty, dev)
    assert [c["kind"] for c in found] == ["usb", "uart"] and found[0]["label"] == "CH340 USB GPS"
