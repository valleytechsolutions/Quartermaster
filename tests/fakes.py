"""Stand-ins for the hardware, shared by the tests and the screenshot renderer."""

import random

from quartermaster.wifi import band_of, channel_of

FIX = {"valid": True, "latitude": 25.07805, "longitude": -77.34312, "satellites": 14, "hdop": 0.8, "age_seconds": 0.3,
       "source": "CP210x USB GPS (/dev/ttyUSB0)", "altMSL": 96.0, "status": "fix"}
NO_FIX = {"valid": False, "source": "none", "status": "no_fix", "age_seconds": None}
ADAPTERS = [
    {"interface": "wlan1", "phy": "phy1", "driver": "mt7921u", "external": True, "usb_id": "0e8d:7961",
     "label": "MT7921AU tri-band (ALFA AWUS036AXM)", "bands": ["2.4", "5", "6"], "band_label": "tri-band",
     "monitor": True},
    {"interface": "wlan2", "phy": "phy2", "driver": "rtw_8812au", "external": True, "usb_id": "0bda:8812",
     "label": "RTL8812AU dual-band", "bands": ["2.4", "5"], "band_label": "dual-band", "monitor": True},
    {"interface": "wlan0", "phy": "phy0", "driver": "brcmfmac", "external": False, "usb_id": "",
     "label": "Broadcom (built-in)", "bands": ["2.4"], "band_label": "single-band", "monitor": False},
]
NAMES = ["Black Pearl", "Flying Dutchman 5G", "xfinitywifi", "", "Tortuga Guest", "NETGEAR88-6G", "eduroam",
         "Jolly Roger IoT", "DIRECT-roku-471", "Davy Jones", "ATTm9xK2s", "Queen Anne's Revenge", "Kraken_6E"]
SECURITY = [("WPA2", "[WPA2-PSK-CCMP][ESS]"), ("WPA2", "[WPA2-PSK-CCMP][ESS]"), ("WPA3", "[RSN-SAE-CCMP][ESS]"),
            ("OPEN", "[ESS]"), ("EAP", "[WPA2-EAP-CCMP][ESS]")]


def networks(count=412, seed=7):
    rnd = random.Random(seed)
    found = []
    for i in range(count):
        freq = rnd.choice([2412, 2437, 2462, 5180, 5745, 5500, 6135, 5955, 2437, 5220])
        security, capabilities = rnd.choice(SECURITY)
        found.append({"bssid": "AA:BB:CC:%02X:%02X:%02X" % (i >> 8, i & 255, rnd.randrange(256)),
                      "ssid": NAMES[i % len(NAMES)], "rssi": rnd.randrange(-92, -38), "frequency": freq,
                      "channel": channel_of(freq), "band": band_of(freq), "security": security,
                      "capabilities": capabilities})
    return found


class Gps:
    mode, power = "auto", True

    def __init__(self, fix=FIX):
        self.fix = fix

    def snapshot(self):
        return dict(self.fix)


class Display:
    def __init__(self):
        self.frames = 0
        self.last = None

    def show(self, image):
        self.frames += 1
        self.last = image

    def close(self):
        pass


class Hardware:
    def __init__(self, adapters=ADAPTERS, stalled=(), addresses=("192.168.1.50",)):
        self.adapters = list(adapters)
        self.stalled = list(stalled)
        self.bluetooth = [{"name": "hci0", "external": False, "label": "Built-in Bluetooth"}]
        self.gps_ports = [{"path": "/dev/ttyUSB0", "label": "CP210x USB GPS", "kind": "usb", "likely": True},
                          {"path": "/dev/ttyS0", "label": "Cap / Grove UART", "kind": "uart", "likely": True}]
        self._addresses = list(addresses)

    def addresses(self):
        return list(self._addresses)

    def start(self):
        pass

    def stop(self):
        pass


class Fleet:
    """Reports one sweep of sample networks from the first adapter."""

    found = None

    def __init__(self, adapters, sink, interval):
        self.adapters, self.sink = adapters, sink

    def start(self):
        if self.adapters:
            self.sink(self.found if self.found is not None else networks(), self.adapters[0])

    def stop(self):
        pass

    def status(self):
        return [{"interface": a["interface"], "method": "iw", "sweeps": 12, "sweep_seconds": 3.1, "error": "",
                 "label": a.get("label", ""), "bands": a.get("bands", [])} for a in self.adapters]


class QuietFleet(Fleet):
    def start(self):
        pass


class Ble:
    def __init__(self, devices=()):
        self.devices = [{"address": address, "name": name, "rssi": rssi, "address_type": 1, "classic": False,
                         "manufacturer": {}} for address, name, rssi in devices]

    def scan(self):
        return [dict(device) for device in self.devices]

    def close(self):
        pass


class NoBle(Ble):
    pass


class Silent:
    def play(self):
        return False
