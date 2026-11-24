import multiprocessing
import random
import struct
import threading
import time

import pytest

from quartermaster import config, deepscan, drive_watch, monitor, radio_helper, recon, wigle
from quartermaster.drive_watch import DriveWatch, SsidConflicts
from quartermaster.engine import Wardrive
from quartermaster.wifi import parse_iw_scan
from tests.fakes import Clock, Fleet, Gps, networks
from tests.test_core import FIX, NOW
from tests.test_radiod import radiod

FLOCK = "70:C9:4E:12:34:56"
AP = "AC:84:C6:00:00:01"
LITEON = bytes((0x50, 0x6f, 0x9a, 0x16, 3, 1, 3))
WPS_OUI = bytes((0, 0x50, 0xf2, 8, 0, 0, 0))
ALFA = {"interface": "wlan1", "phy": "phy1", "driver": "mt7921u", "external": True, "usb_id": "0e8d:7961",
        "product": "ALFA Network AWUS036AXM", "label": "MT7921AU tri-band (ALFA AWUS036AXML / AXM)",
        "bands": ["2.4", "5", "6"], "monitor": True, "type": "1"}
AC1200 = {"interface": "wlan2", "phy": "phy2", "driver": "mt7921u", "external": True, "usb_id": "0e8d:7961",
          "product": "MediaTek Inc. Wireless_Device", "label": "MT7921AU tri-band (ALFA AWUS036AXML / AXM)",
          "bands": ["2.4", "5", "6"], "monitor": True, "type": "1"}
AC1200_WIFI5 = {"interface": "wlan2", "phy": "phy2", "driver": "mt76x2u", "external": True, "usb_id": "0e8d:7612",
                "product": "HackerGadgets AC1200", "label": "ALFA AWUS036ACM (MT7612U dual-band)",
                "bands": ["2.4", "5"], "monitor": True, "type": "1"}
BUILTIN = {"interface": "wlan0", "phy": "phy0", "driver": "brcmfmac", "external": False, "usb_id": "",
           "label": "Broadcom (built-in)", "bands": ["2.4", "5"], "monitor": True, "type": "1"}


def mac(text):
    return bytes(int(part, 16) for part in text.split(":"))


def ie(kind, body=b""):
    return bytes((kind, len(body))) + bytes(body)


def radiotap(freq=2437, rssi=-50, flags=0, tsft=True, ext=False):
    present = (1 << 1) | (1 << 3) | (1 << 5) | (1 if tsft else 0)
    words = struct.pack("<I", present | (0x80000000 if ext else 0))
    if ext:
        words += struct.pack("<I", 0)
    head = 8 + len(words) - 4
    body = bytearray()
    at = head

    def pad(align):
        nonlocal at
        while at % align:
            body.append(0)
            at += 1
    if tsft:
        pad(8)
        body += struct.pack("<Q", 123456)
        at += 8
    body.append(flags)
    at += 1
    pad(2)
    body += struct.pack("<HH", freq, 0x00a0)
    at += 4
    body.append(rssi & 0xff)
    at += 1
    total = head + len(body)
    return struct.pack("<BBH", 0, 0, total) + words + bytes(body)


def header(fc, addr1, addr2, addr3):
    return bytes((fc, 0, 0, 0)) + mac(addr1) + mac(addr2) + mac(addr3) + b"\x10\x00"


def beacon(bssid=AP, ssid=b"Harbor", channel=6, privacy=True, extra=b"", fc=0x80, ibss=False):
    caps = 0x0001 | (0x0010 if privacy else 0) | (0x0002 if ibss else 0)
    body = struct.pack("<QHH", 0, 100, caps) + ie(0, ssid)
    if channel:
        body += ie(3, bytes((channel,)))
    return header(fc, "FF:FF:FF:FF:FF:FF", bssid, bssid) + body + extra


def rsn(pairwise=(4,), akms=(2,), group=4):
    body = struct.pack("<H", 1) + b"\x00\x0f\xac" + bytes((group,))
    body += struct.pack("<H", len(pairwise)) + b"".join(b"\x00\x0f\xac" + bytes((c,)) for c in pairwise)
    body += struct.pack("<H", len(akms)) + b"".join(b"\x00\x0f\xac" + bytes((a,)) for a in akms)
    return ie(48, body + b"\x00\x00")


def wpa(pairwise=(2,), akms=(2,)):
    body = b"\x00\x50\xf2\x01" + struct.pack("<H", 1) + b"\x00\x50\xf2\x02"
    body += struct.pack("<H", len(pairwise)) + b"".join(b"\x00\x50\xf2" + bytes((c,)) for c in pairwise)
    body += struct.pack("<H", len(akms)) + b"".join(b"\x00\x50\xf2" + bytes((a,)) for a in akms)
    return ie(221, body)


def wps():
    return ie(221, b"\x00\x50\xf2\x04\x10\x4a\x00\x01\x10")


def ht(primary):
    return ie(61, bytes((primary,)) + bytes(21))


def he6(primary):
    params = (1 << 17)
    return ie(255, bytes((36,)) + params.to_bytes(3, "little") + bytes((0,)) + b"\xff\xfc" +
              bytes((primary, 0, primary, 0, 0)))


def probe(addr2=FLOCK, ies=None):
    if ies is None:
        ies = flock_ies()
    return header(0x40, "FF:FF:FF:FF:FF:FF", addr2, "FF:FF:FF:FF:FF:FF") + ies


def flock_ies(phantom=False):
    out = ie(0) + ie(2, b"\x02\x04") + ie(12, b"\x01") + ie(127, bytes(8))
    if phantom:
        out += bytes((64, 128))
    return out + ie(221, LITEON) + ie(45, bytes(26)) + ie(191, bytes(12)) + ie(221, WPS_OUI)


def on_air(frame, freq=2437, rssi=-50, **kw):
    return radiotap(freq, rssi, **kw) + frame


IW = """BSS {bssid}(on wlan1)
\tlast seen: 120 ms ago
\tTSF: 0 usec (0d, 00:00:00)
\tfreq: {freq}
\tbeacon interval: 100 TUs
\tcapability: ESS{privacy} ShortSlotTime (0x0411)
\tsignal: -50.00 dBm
\tSSID: {ssid}
{extra}"""

PARITY = [
    ("WPA2-PSK", 2437, beacon(ssid=b"Harbor", extra=rsn()), "\tDS Parameter set: channel 6\n\tRSN:\t * Version: 1\n"
     "\t\t * Group cipher: CCMP\n\t\t * Pairwise ciphers: CCMP\n\t\t * Authentication suites: PSK\n"),
    ("WPA2/3", 2412, beacon(ssid=b"Mixed", channel=1, extra=rsn(akms=(2, 8))),
     "\tDS Parameter set: channel 1\n\tRSN:\t * Version: 1\n\t\t * Group cipher: CCMP\n"
     "\t\t * Pairwise ciphers: CCMP\n\t\t * Authentication suites: PSK SAE\n"),
    ("WPA3", 2462, beacon(ssid=b"Sea", channel=11, extra=rsn(akms=(8,))),
     "\tDS Parameter set: channel 11\n\tRSN:\t * Version: 1\n\t\t * Group cipher: CCMP\n"
     "\t\t * Pairwise ciphers: CCMP\n\t\t * Authentication suites: SAE\n"),
    ("OWE", 2437, beacon(ssid=b"Open Enhanced", extra=rsn(akms=(18,))),
     "\tDS Parameter set: channel 6\n\tRSN:\t * Version: 1\n\t\t * Group cipher: CCMP\n"
     "\t\t * Pairwise ciphers: CCMP\n\t\t * Authentication suites: OWE\n"),
    ("EAP", 2437, beacon(ssid=b"eduroam", extra=rsn(akms=(1,))),
     "\tDS Parameter set: channel 6\n\tRSN:\t * Version: 1\n\t\t * Group cipher: CCMP\n"
     "\t\t * Pairwise ciphers: CCMP\n\t\t * Authentication suites: IEEE 802.1X\n"),
    ("FT-PSK", 2437, beacon(ssid=b"Fast", extra=rsn(akms=(2, 4))),
     "\tDS Parameter set: channel 6\n\tRSN:\t * Version: 1\n\t\t * Group cipher: CCMP\n"
     "\t\t * Pairwise ciphers: CCMP\n\t\t * Authentication suites: PSK FT/PSK\n"),
    ("SUITE-B", 2437, beacon(ssid=b"Vault", extra=rsn(pairwise=(9,), akms=(12,), group=9)),
     "\tDS Parameter set: channel 6\n\tRSN:\t * Version: 1\n\t\t * Group cipher: GCMP-256\n"
     "\t\t * Pairwise ciphers: GCMP-256\n\t\t * Authentication suites: IEEE 802.1X/SUITE-B-192\n"),
    ("SAE-EXT", 2437, beacon(ssid=b"Wifi7", extra=rsn(akms=(24,))),
     "\tDS Parameter set: channel 6\n\tRSN:\t * Version: 1\n\t\t * Group cipher: CCMP\n"
     "\t\t * Pairwise ciphers: CCMP\n\t\t * Authentication suites: SAE-EXT-KEY\n"),
    ("WPA+WPA2", 2437, beacon(ssid=b"Legacy", extra=wpa(pairwise=(2, 4)) + rsn(pairwise=(2, 4))),
     "\tDS Parameter set: channel 6\n\tWPA:\t * Version: 1\n\t\t * Group cipher: TKIP\n"
     "\t\t * Pairwise ciphers: TKIP CCMP\n\t\t * Authentication suites: PSK\n\tRSN:\t * Version: 1\n"
     "\t\t * Group cipher: CCMP\n\t\t * Pairwise ciphers: TKIP CCMP\n\t\t * Authentication suites: PSK\n"),
    ("WEP", 2437, beacon(ssid=b"Old"), "\tDS Parameter set: channel 6\n"),
    ("open", 2437, beacon(ssid=b"Free WiFi", privacy=False), "\tDS Parameter set: channel 6\n"),
    ("hidden", 2437, beacon(ssid=b""), "\tDS Parameter set: channel 6\n"),
    ("hidden-nul", 2437, beacon(ssid=b"\0\0\0\0"), "\tDS Parameter set: channel 6\n"),
    ("WPS", 2437, beacon(ssid=b"Printer", privacy=False, extra=wps()),
     "\tDS Parameter set: channel 6\n\tWPS:\t * Version: 1.0\n"),
    ("5 GHz HT", 5180, beacon(ssid=b"Harbor 5G", channel=0, extra=ht(36) + rsn()),
     "\tHT operation:\n\t\t * primary channel: 36\n\tRSN:\t * Version: 1\n\t\t * Group cipher: CCMP\n"
     "\t\t * Pairwise ciphers: CCMP\n\t\t * Authentication suites: PSK\n"),
    ("5 GHz 149", 5745, beacon(ssid=b"Upper", channel=0, extra=ht(149) + rsn(akms=(8,))),
     "\tHT operation:\n\t\t * primary channel: 149\n\tRSN:\t * Version: 1\n\t\t * Group cipher: CCMP\n"
     "\t\t * Pairwise ciphers: CCMP\n\t\t * Authentication suites: SAE\n"),
    ("6 GHz HE", 6135, beacon(ssid=b"Kraken_6E", channel=0, extra=he6(37) + rsn(akms=(8,))),
     "\tRSN:\t * Version: 1\n\t\t * Group cipher: CCMP\n\t\t * Pairwise ciphers: CCMP\n"
     "\t\t * Authentication suites: SAE\n"),
    ("escaped", 2437, beacon(ssid=b" two  spaces\\"), "\tDS Parameter set: channel 6\n"),
    ("utf8", 2437, beacon(ssid="Café ☕".encode()), "\tDS Parameter set: channel 6\n"),
    ("IBSS", 2437, beacon(ssid=b"adhoc", privacy=False, ibss=True), "\tDS Parameter set: channel 6\n"),
]


def iw_ssid(raw):
    out = []
    for i, b in enumerate(raw):
        c = chr(b)
        if 32 < b < 127 and c != "\\":
            out.append(c)
        elif b == 32 and 0 < i < len(raw) - 1:
            out.append(" ")
        else:
            out.append("\\x%02x" % b)
    return "".join(out)


@pytest.mark.parametrize("name,freq,frame,extra", PARITY, ids=[p[0] for p in PARITY])
def test_a_beacon_becomes_the_same_network_iw_reports(name, freq, frame, extra):
    ssid_raw = next(body for kind, body in monitor.elements(frame[36:]) if kind == 0)
    privacy = " Privacy" if frame[34] & 0x10 else ""
    if frame[34] & 0x02:
        privacy += " IBSS"
    text = IW.format(bssid=AP.lower(), freq=freq, privacy=privacy, ssid=iw_ssid(bytes(ssid_raw)), extra=extra)
    expected = parse_iw_scan(text)[0]
    worker = monitor.Worker("qmmon0")
    worker.feed(on_air(frame, freq), 0.0)
    got = worker.tick(5.0)
    assert len(got) == 1
    net = got[0]
    for field in ("bssid", "ssid", "capabilities", "security", "channel", "frequency", "band", "hidden", "privacy",
                  "rssi"):
        assert net[field] == expected[field], field
    assert net["interface"] == "qmmon0" and net["last_seen_ms"] == 0


def test_a_neighbour_channel_beacon_is_logged_on_its_own_channel():
    net = monitor.parse_beacon(beacon(channel=3), -60, 2412)
    assert (net["channel"], net["frequency"], net["band"]) == (3, 2422, "2.4")
    net = monitor.parse_beacon(beacon(channel=0), -60, 2412)
    assert (net["channel"], net["frequency"]) == (1, 2412)


def test_radiotap_headers_are_read_and_bad_ones_never_raise():
    frame = beacon()
    for kw in ({}, {"tsft": False}, {"ext": True}, {"tsft": False, "ext": True}):
        raw = on_air(frame, 5180, -71, **kw)
        length, end, rssi, freq = monitor.radiotap(raw)
        assert raw[length:end] == frame and (rssi, freq) == (-71, 5180)
    with_fcs = on_air(frame + b"\xde\xad\xbe\xef", flags=0x10)
    length, end, _, _ = monitor.radiotap(with_fcs)
    assert with_fcs[length:end] == frame
    assert monitor.radiotap(on_air(frame, flags=0x40)) is None
    good = on_air(frame)
    assert monitor.radiotap(good[:6]) is None
    assert monitor.radiotap(b"\x01" + good[1:]) is None
    assert monitor.radiotap(good[:2] + struct.pack("<H", len(good) + 1) + good[4:]) is None
    assert monitor.radiotap(struct.pack("<BBHI", 0, 0, 8, 0x80000000)) is None
    rnd = random.Random(3)
    worker = monitor.Worker()
    for _ in range(3000):
        size = rnd.randrange(0, 80)
        junk = bytes(rnd.randrange(256) for _ in range(size))
        if rnd.random() < 0.5 and size > 8:
            junk = good[:rnd.randrange(8, len(good))] + junk
        monitor.radiotap(junk)
        worker.feed(junk, 1.0, 2437)
        monitor.parse_beacon(junk, -50, 2437)
        recon.classify_wifi(FLOCK, frame=junk)


class Bpf:
    """Enough of a classic BPF machine to run the helper's filter."""

    def __init__(self, program):
        self.program = program

    def run(self, packet):
        a = x = 0
        mem = [0] * 16
        pc = 0

        def load(at, size):
            if at < 0 or at + size > len(packet):
                raise IndexError
            return int.from_bytes(packet[at:at + size], "big")
        while True:
            code, jt, jf, k = self.program[pc]
            pc += 1
            try:
                if code == 0x30:
                    a = load(k, 1)
                elif code == 0x50:
                    a = load(x + k, 1)
                elif code == 0x40:
                    a = load(x + k, 4)
                elif code == 0x60:
                    a = mem[k]
                elif code == 0x02:
                    mem[k] = a
                elif code == 0x07:
                    x = a
                elif code == 0x87:
                    a = x
                elif code == 0x64:
                    a = (a << k) & 0xffffffff
                elif code == 0x74:
                    a >>= k
                elif code == 0x54:
                    a &= k
                elif code == 0x4c:
                    a |= x
                elif code == 0x04:
                    a = (a + k) & 0xffffffff
                elif code == 0x15:
                    pc += jt if a == k else jf
                elif code == 0x45:
                    pc += jt if a & k else jf
                elif code == 0x06:
                    return k
                elif code == 0x16:
                    return a
                else:
                    raise AssertionError("unknown opcode %#x" % code)
            except IndexError:
                return 0


def test_the_kernel_filter_keeps_what_deep_scan_reads_and_drops_the_rest():
    program = radiod.bpf_program()
    assert len(program) <= 4096 and all(0 <= jt <= 255 and 0 <= jf <= 255 for _, jt, jf, _ in program)
    bpf, rt = Bpf(program), radiotap()
    x = len(rt)
    keep = {"beacon": beacon(), "probe response": beacon(fc=0x50), "probe request": probe(AP),
            "deauth": header(0xc0, AP, AP, AP) + b"\x07\x00", "disassoc": header(0xa0, AP, AP, AP) + b"\x08\x00",
            "action": header(0xd0, AP, AP, AP) + b"\x04\x09"}
    for name, frame in keep.items():
        assert bpf.run(rt + frame) == x + 2048, name
    assert bpf.run(rt + header(0xd4, AP, AP, AP)) == 0
    assert bpf.run(rt + header(0xb4, AP, AP, AP)) == 0
    assert bpf.run(rt + header(0x08, AP, "00:11:22:33:44:55", AP)) == 0
    assert bpf.run(rt + header(0x81, AP, AP, AP)) == 0
    assert bpf.run(rt + header(0x08, AP, FLOCK, AP)) == x + 32
    assert bpf.run(rt + header(0x88, FLOCK, AP, AP)) == x + 32
    assert bpf.run(rt + header(0x08, "82:6B:F2:00:00:01", AP, AP)) == x + 32
    assert bpf.run(rt + header(0x08, "3C:71:BF:00:00:01", AP, AP)) == 0
    assert bpf.run(rt + header(0x00, AP, FLOCK, AP)) == x + 24
    assert bpf.run(rt + header(0xb0, AP, AP, "B4:1E:52:00:00:01")) == x + 24
    assert bpf.run(rt + header(0x00, AP, AP, AP)) == 0
    assert bpf.run(rt[:3]) == 0
    quiet = Bpf(radiod.bpf_program(mgmt_only=True))
    assert quiet.run(rt + header(0x08, AP, FLOCK, AP)) == 0 and quiet.run(rt + beacon()) == x + 2048
    assert len(radiod.bpf_program(mgmt_only=True)) < len(program)


def test_the_kernel_takes_the_filter(tmp_path):
    try:
        capture = radiod.Capture("lo", radiod.bpf_program())
    except (PermissionError, OSError) as exc:
        pytest.skip("no packet socket here: %s" % exc)
    capture.close()


def flock_of(frame, address=FLOCK, hopping=False):
    result = recon.classify_wifi(address, frame=frame, hopping=hopping)
    return result["category"], result["confidence"], result.get("address")


def test_flock_tier_4_the_fingerprint_also_with_an_fcs_or_a_phantom_tag():
    assert flock_of(probe()) == ("flock", recon.MEDIUM, FLOCK)
    assert flock_of(probe() + b"\x12\x34\x56\x78") == ("flock", recon.MEDIUM, FLOCK)
    assert flock_of(probe(ies=flock_ies(phantom=True))) == ("flock", recon.MEDIUM, FLOCK)
    assert flock_of(probe(addr2="82:6B:F2:00:00:09"), "82:6B:F2:00:00:09")[1] == recon.MEDIUM
    assert recon._probe_signature(flock_ies(phantom=True)) == recon.FLOCK_IE_SIGNATURE
    assert recon.ie_signature(flock_ies()) == (recon.FLOCK_IE_SIGNATURE, True)


def test_flock_tier_3_a_wildcard_probe_without_the_fingerprint_is_weak_unless_it_hops():
    other = ie(0) + ie(1, b"\x82\x84") + ie(50, b"\x0c") + ie(45, bytes(26))
    assert flock_of(probe(ies=other)) == ("flock", recon.LOW, FLOCK)
    assert flock_of(probe(ies=other), hopping=True) == ("flock", recon.MEDIUM, FLOCK)
    reordered = ie(0) + ie(2, b"\x02\x04") + ie(12, b"\x01") + ie(127, bytes(8)) + ie(221, LITEON) + \
        ie(191, bytes(12)) + ie(45, bytes(26)) + ie(221, WPS_OUI)
    assert flock_of(probe(ies=reordered))[1] == recon.LOW
    named = ie(0, b"HomeNet") + flock_ies()[2:]
    assert flock_of(probe(ies=named))[1] == recon.LOW


def test_flock_tier_3_timing_two_channels_within_3_s_upgrades():
    worker = monitor.Worker()
    other = probe(ies=ie(0) + ie(1, b"\x82\x84"))
    first = worker.feed(on_air(other, 2462), 10.0)
    assert first[0][1]["rule"] == "probe" and first[0][1]["hopping"] is False
    second = worker.feed(on_air(other, 2437), 11.5)
    assert second[0][1]["hopping"] is True
    assert flock_of(second[0][1]["frame"], hopping=True)[1] == recon.MEDIUM
    slow = monitor.Worker()
    assert slow.feed(on_air(other, 2437), 0.0)[0][1]["hopping"] is False
    assert slow.feed(on_air(other, 2437), 4.0)[0][1]["hopping"] is False
    parked = monitor.Worker()
    heard = [parked.feed(on_air(other, freq), t) for t, freq in
             ((0.0, 2437), (0.2, 2437), (0.4, 2432), (1.1, 2442), (1.2, 2427), (1.3, 2447), (2.5, 2437))]
    assert heard[0] and not any(e[0][1]["hopping"] for e in heard if e)
    for first_freq, then in ((2462, 2412), (2437, 5180), (5180, 5200)):
        far = monitor.Worker()
        assert far.feed(on_air(other, first_freq), 0.0)[0][1]["hopping"] is False
        assert far.feed(on_air(other, then), 1.0)[0][1]["hopping"] is True


def test_flock_tiers_2_and_1_other_frames_from_or_to_a_flock_prefix():
    worker = monitor.Worker()
    data = header(0x08, AP, FLOCK, AP) + bytes(8)
    event = worker.feed(on_air(data), 0.0)[0][1]
    assert event["rule"] == "addr2" and event["address"] == FLOCK
    assert flock_of(event["frame"]) == ("flock", recon.LOW, None)
    reply = beacon(fc=0x50)[:4] + mac(FLOCK) + beacon(fc=0x50)[10:]
    events = worker.feed(on_air(reply), 0.0)
    frames = [e[1] for e in events if e[0] == "frame"]
    assert [(f["rule"], f["address"]) for f in frames] == [("addr1", FLOCK)]
    assert flock_of(frames[0]["frame"], FLOCK) == ("flock", recon.LOW, FLOCK)
    assert recon.classify_wifi(FLOCK, frame=frames[0]["frame"])["evidence"].startswith("Second-hand")
    auth = header(0xb0, AP, "00:11:22:33:44:55", "B4:1E:52:00:00:07") + bytes(6)
    events = worker.feed(on_air(auth), 0.0)
    assert [(e[1]["rule"], e[1]["address"]) for e in events] == [("addr3", "B4:1E:52:00:00:07")]
    assert flock_of(events[0][1]["frame"], "B4:1E:52:00:00:07")[2] == "B4:1E:52:00:00:07"
    broadcast = header(0x08, "FF:FF:FF:FF:FF:FF", AP, AP)
    assert worker.feed(on_air(broadcast), 0.0) == []


def test_flock_tier_0_names_and_the_lookalike_that_is_not():
    for name in ("Flock", "Flock-A1B2C3", "Flock Camera net."):
        assert recon.classify_wifi(AP, name)["category"] == "flock", name
    assert recon.classify_wifi(AP, "Flockhart Family WiFi")["category"] is None
    assert recon.classify_wifi(AP, "Flock")["confidence"] == recon.LOW


def test_espressif_prefixes_are_never_flock_and_weak_signals_are_dropped():
    for prefix in ("3C:71:BF", "48:27:EA", "A4:CF:12"):
        address = prefix + ":01:02:03"
        assert recon.classify_wifi(address, frame=probe(address))["category"] != "flock"
        assert monitor.Worker().feed(on_air(probe(address)), 0.0) == []
        assert drive_watch.wifi_hint(address, "") != "flock"
    worker = monitor.Worker()
    assert worker.feed(on_air(probe(), rssi=-96), 0.0) == []
    assert worker.feed(on_air(probe(), rssi=-95), 0.0)[0][1]["rule"] == "probe"


def deauth(sender, to="FF:FF:FF:FF:FF:FF", fc=0xc0):
    return on_air(header(fc, to, sender, sender) + b"\x07\x00")


def burst(worker, frames, start=0.0, step=0.1):
    fired = []
    for i, raw in enumerate(frames):
        fired.extend(e[1] for e in worker.feed(raw, start + i * step) if e[0] == "deauth")
    return fired


def test_disconnect_floods_follow_squachwatch():
    one = "00:11:22:33:44:55"
    assert burst(monitor.Worker(), [deauth(one, to=AP)] * 11) == []
    fired = burst(monitor.Worker(), [deauth(one, to=AP)] * 11 + [deauth(one, to=AP, fc=0xa0)])
    assert len(fired) == 1 and fired[0]["count"] == 12 and fired[0]["sender"] == one and not fired[0]["any"]
    fired = burst(monitor.Worker(), [deauth(one)] * 6)
    assert len(fired) == 1 and fired[0]["bcast"] == 6
    assert burst(monitor.Worker(), [deauth(one)] * 5) == []
    spoofed = ["02:00:00:00:00:%02X" % i for i in range(8)]
    frames = [deauth(s) for _ in range(3) for s in spoofed]
    fired = burst(monitor.Worker(), frames, step=0.05)
    assert len(fired) == 1 and fired[0]["any"] and fired[0]["count"] == 24
    assert burst(monitor.Worker(), [deauth(one, to=AP)] * 12, step=0.3) == []


def test_a_sender_rotation_cannot_push_out_a_live_count_and_the_cooldown_holds():
    counter = monitor.DeauthCounter()
    target = b"\x00\x11\x22\x33\x44\x55"
    for i in range(6):
        assert counter.feed(target, False, i * 0.1) is None
    for n in range(40):
        counter.feed(bytes((2, 0, 0, 0, 0, n)), False, 0.6 + n * 0.01)
    fired = None
    for i in range(6):
        fired = fired or counter.feed(target, False, 1.1 + i * 0.1)
    assert fired is not None and fired["count"] == 12
    assert counter.feed(target, True, 1.8) is None
    for i in range(12):
        assert counter.feed(target, False, 10.0 + i * 0.1) is None
    again = [counter.feed(target, False, 16.8 + i * 0.1) for i in range(12)]
    assert any(again)


def conflicts_after(pairs, rounds=4):
    found = SsidConflicts()
    out = []
    for n in range(rounds):
        out += found.observe([{"bssid": b, "ssid": "Harbor", "security": s} for b, s in pairs], n * 1.0)
    return out


def test_an_evil_twin_from_the_same_maker_never_fires_and_from_another_does():
    assert conflicts_after([("00:11:22:00:00:01", "OPEN"), ("02:11:22:00:00:02", "WPA2")]) == []
    assert conflicts_after([("00:11:22:00:00:01", "OPEN"), ("00:11:22:00:00:09", "WPA2")]) == []
    found = conflicts_after([("00:11:22:00:00:01", "OPEN"), ("AC:84:C6:00:00:02", "WPA2")])
    assert found and found[0] == ("Harbor", "00:11:22:00:00:01", "AC:84:C6:00:00:02")
    assert conflicts_after([("00:11:22:00:00:01", "OPEN"), ("AC:84:C6:00:00:02", "WPA2")], rounds=2) == []
    assert drive_watch.same_vendor("00:11:22:00:00:01", "02:11:22:00:00:02")
    assert not drive_watch.same_vendor("00:11:22:00:00:01", "00:11:23:00:00:01")


class Player:
    def __init__(self):
        self.calls = []

    def play(self, tone, volume="high", kind=None, priority=50, preview=False):
        self.calls.append(tone)
        return True

    def available(self):
        return True

    def stop(self):
        pass


def watch(tmp_path, **cfg):
    clock = Clock()
    w = DriveWatch(cfg, Gps(FIX), tmp_path, player=Player(), clock=clock, monotonic=clock, threads=False)
    w.start(NOW)
    w.started -= drive_watch.START_SILENCE
    return w, clock


def test_a_beacon_clash_is_only_evidence_for_a_twin(tmp_path):
    w, clock = watch(tmp_path, drive_alert_types=["eviltwin"])
    worker = monitor.Worker()
    worker.feed(on_air(beacon(bssid="AC:84:C6:00:00:02", channel=6)), 0.0)
    clash = worker.feed(on_air(beacon(bssid="AC:84:C6:00:00:02", channel=11, ssid=b"Harbor"), 2462), 2.5)
    assert clash and clash[0][0] == "clash"
    assert "channels 6 and 11" in clash[0][1]["evidence"]
    w.offer_clash(clash[0][1])
    w.drain()
    assert w.alert_list() == []
    twin = [{"bssid": "00:11:22:00:00:01", "ssid": "Harbor", "security": "OPEN", "rssi": -40},
            {"bssid": "AC:84:C6:00:00:02", "ssid": "Harbor", "security": "WPA2", "rssi": -50}]
    for _ in range(4):
        w.offer_wifi(twin)
        w.drain()
        clock.advance(1.0)
    alerts = w.alert_list()
    assert len(alerts) == 1 and alerts[0]["category"] == "eviltwin"
    assert "channels 6 and 11" in alerts[0]["evidence"]
    w.stop()


def frame_event(frame, address=FLOCK, rssi=-60, hopping=False, rule="probe"):
    return {"rule": rule, "address": address, "rssi": rssi, "channel": 6, "frequency": 2437, "frame": frame,
            "hopping": hopping}


def test_at_medium_evidence_only_the_fingerprint_and_the_hopping_prober_alert(tmp_path):
    w, clock = watch(tmp_path)
    plain = probe(ies=ie(0) + ie(1, b"\x82\x84"))
    cases = [("F4", frame_event(probe()), True),
             ("F3t", frame_event(probe("70:C9:4E:00:00:02", ie(0) + ie(1, b"\x82\x84")), "70:C9:4E:00:00:02",
                                 hopping=True), True),
             ("F3", frame_event(probe("70:C9:4E:00:00:03", ie(0) + ie(1, b"\x82\x84")), "70:C9:4E:00:00:03"),
              False),
             ("F2", frame_event(header(0x08, AP, "70:C9:4E:00:00:04", AP), "70:C9:4E:00:00:04", rule="addr2"),
              False),
             ("F1", frame_event(header(0x50, "70:C9:4E:00:00:05", AP, AP) + bytes(12), "70:C9:4E:00:00:05",
                                rule="addr1"), False)]
    del plain
    for name, event, alerts in cases:
        before = len(w.alert_list())
        w.offer_frames([event])
        w.drain()
        clock.advance(1.0)
        assert (len(w.alert_list()) > before) is alerts, name
    w.offer_wifi([{"bssid": AP, "ssid": "Flock-A1B2C3", "rssi": -50, "channel": 6, "frequency": 2437,
                   "security": "OPEN"}])
    w.drain()
    assert len(w.alert_list()) == 2
    shown = w.alert_list()[0]
    assert shown["category"] == "flock" and shown["source"] == "monitor"
    assert drive_watch.HEARD_BY["monitor"] == "Wi-Fi monitor"
    w.stop()


def test_offering_frames_never_raises_or_waits(tmp_path):
    w, _ = watch(tmp_path)
    started = time.monotonic()
    w.offer_frames([frame_event(probe())] * (drive_watch.QUEUE_LIMIT + 50))
    w.offer_frames(None)
    w.offer_incident("deauth", "00:11:22:33:44:55", "x")
    w.offer_incident("nonsense", None, None)
    w.offer_clash(None)
    assert time.monotonic() - started < 1.0
    assert w.dropped >= 50
    w.stop()


def test_a_disconnect_flood_from_deep_scan_is_one_quiet_incident(tmp_path):
    from quartermaster.engine import deauth_words
    w, clock = watch(tmp_path, drive_alert_types=["deauth"])
    words = deauth_words({"sender": "00:11:22:33:44:55", "count": 14, "bcast": 6, "channel": 6, "any": False})
    assert words == "Disconnect burst: 14 frames in 3 s from 00:11:22:33:44:55; 6 to everyone, ch 6"
    for _ in range(3):
        w.offer_incident("deauth", "00:11:22:33:44:55", words, -40)
        w.drain()
        clock.advance(60)
    alerts = w.alert_list()
    assert len(alerts) == 1 and alerts[0]["category"] == "deauth" and alerts[0]["evidence"] == words
    w.stop()


def roles_ok(adapters, result, helper=(), nodes=()):
    roles = result["roles"]
    chosen = [a for a in adapters if roles[a["interface"]] == "monitor"]
    assert len(chosen) <= 1
    live = {h["interface"]: h for h in helper}
    for a in chosen:
        assert a["external"]
        assert not live.get(a["interface"], {}).get("connected")
        assert not live.get(a["interface"], {}).get("default_route")
        covered = {"wifi24": "2.4", "wifi5": "5"}
        for band in a["bands"]:
            assert any(band in o["bands"] for o in adapters if roles[o["interface"]] == "managed") or \
                band != "6" and band in {covered[n] for n in nodes}, band
    if any("6" in a["bands"] for a in adapters):
        assert any("6" in a["bands"] for a in adapters if roles[a["interface"]] == "managed")
    assert all(roles[a["interface"]] == "managed" for a in adapters if not a["external"])


def test_roles_never_cost_a_band_and_6_ghz_always_keeps_a_managed_radio():
    rnd = random.Random(11)
    bands = [["2.4"], ["2.4", "5"], ["2.4", "5", "6"], ["5", "6"], []]
    drivers = ["mt7921u", "mt76x2u", "rtw88_8822bu", "rtl8812au", "brcmfmac"]
    monitors = 0
    for _ in range(3000):
        adapters = []
        for i in range(rnd.randrange(0, 5)):
            adapters.append({"interface": "wlan%d" % i, "external": rnd.random() < 0.8,
                             "bands": rnd.choice(bands), "monitor": rnd.random() < 0.8,
                             "driver": rnd.choice(drivers), "label": "", "product": ""})
        helper = [{"interface": a["interface"], "connected": rnd.random() < 0.2,
                   "default_route": rnd.random() < 0.1} for a in adapters]
        nodes = rnd.choice([(), ("wifi24",), ("wifi24", "wifi5")])
        result = deepscan.assign(adapters, nodes, helper)
        roles_ok(adapters, result, helper, nodes)
        if result["monitor"] is not None:
            monitors += 1
            assert result["unavailable"] == ""
        else:
            assert result["unavailable"]
    assert monitors > 300


def test_the_owners_adapters_get_the_right_roles():
    result = deepscan.assign([ALFA, AC1200, BUILTIN])
    assert result["monitor"] is AC1200 and result["roles"] == {"wlan1": "managed", "wlan2": "monitor",
                                                               "wlan0": "managed"}
    assert deepscan.assign([AC1200, ALFA, BUILTIN])["monitor"] is AC1200
    assert deepscan.assign([ALFA, AC1200_WIFI5, BUILTIN])["monitor"] is AC1200_WIFI5
    alone = deepscan.assign([ALFA, BUILTIN])
    assert alone["monitor"] is None and "keeps 6 GHz" in alone["unavailable"]
    assert alone["reasons"] == {"wlan1": deepscan.REASONS["only6"], "wlan0": deepscan.REASONS["builtin"]}
    assert deepscan.assign([ALFA])["monitor"] is None
    picked = deepscan.assign([ALFA, AC1200, BUILTIN], choice="wlan1")
    assert picked["monitor"] is ALFA and deepscan.keeps_six([ALFA, AC1200, BUILTIN], picked["roles"])
    assert deepscan.assign([ALFA, AC1200_WIFI5, BUILTIN], choice="wlan1")["monitor"] is None
    busy = deepscan.assign([ALFA, AC1200, BUILTIN], helper=[{"interface": "wlan2", "connected": True}])
    assert busy["monitor"] is ALFA
    routed = deepscan.assign([ALFA, AC1200_WIFI5], helper=[{"interface": "wlan2", "default_route": True}])
    assert routed["monitor"] is None and routed["reasons"]["wlan2"] == deepscan.REASONS["connected"]
    no_driver = dict(AC1200_WIFI5, driver="rtl88xxau")
    assert deepscan.assign([ALFA, no_driver])["reasons"]["wlan2"] == deepscan.REASONS["no_monitor"]


def test_the_balanced_pattern_starts_on_11_6_1_at_250_ms_and_never_goes_to_6_ghz():
    plan = deepscan.HopPlan("US", "balanced")
    steps = plan.build()
    assert steps[:3] == [(2462, 250), (2437, 250), (2412, 250)]
    fill = steps[3:11]
    assert [f for f, _ in fill] == [2417, 2422, 2427, 2432, 2442, 2447, 2452, 2457]
    assert all(d >= 110 for _, d in fill)
    five = steps[11:]
    assert five and all(5000 < f < 5925 for f, _ in five) and sum(d for _, d in five) <= 1500
    assert all(f < 5925 for f, _ in steps) and len(steps) <= 64
    plan.heard({2417: 40.0, 2442: 2.0, 5180: 30.0, 5745: 10.0})
    busy = plan.build()
    dwell = dict(busy)
    assert dwell[2417] > dwell[2442] >= 110 and dwell[2422] == 110
    assert dwell[5180] == dwell[5745] == 250
    eu = deepscan.HopPlan("DE", "balanced").build()
    assert {2467, 2472} <= {f for f, _ in eu}
    slow = deepscan.HopPlan("US", "balanced")
    slow.slower()
    assert min(d for _, d in slow.build()) >= 150


def test_flock_first_stays_on_11_6_1_with_a_fill_every_fourth_round():
    steps = deepscan.HopPlan("US", "flock").build()
    assert steps[:12] == [(2462, 250), (2437, 250), (2412, 250)] * 4
    assert [d for _, d in steps[12:]] == [110] * 8 and all(f < 3000 for f, _ in steps)
    limited = deepscan.HopPlan("US", "balanced", allowed=[2412, 2437, 2462, 5180]).build()
    assert {f for f, _ in limited} <= {2412, 2437, 2462, 5180}


class FakeClient:
    """The radio helper as the app sees it, keeping every request."""

    def __init__(self, fail=()):
        self.requests = []
        self.fail = set(fail)
        self.frames = None
        self.closed = False

    def request(self, cmd, timeout=None, **args):
        self.requests.append((cmd, args))
        if cmd in self.fail:
            raise radio_helper.HelperError("%s failed" % cmd)
        if cmd == "adapters":
            return {"ok": True, "adapters": [
                {"interface": "wlan0", "usb": False, "connected": True, "default_route": True, "monitor": True,
                 "freqs": [2412, 2437, 2462]},
                {"interface": "wlan1", "usb": True, "connected": False, "default_route": False, "monitor": True,
                 "freqs": list(range(2412, 2463, 5)) + [5180, 5745, 6135]},
                {"interface": "wlan2", "usb": True, "connected": False, "default_route": False, "monitor": True,
                 "freqs": list(range(2412, 2463, 5)) + [5180, 5745, 6135]}]}
        if cmd == "monitor_start":
            return {"ok": True, "vif": "qmmon0"}
        if cmd == "scan":
            return {"ok": True, "text": "BSS 00:11:22:33:44:66(on wlan1)\n\tfreq: 5180\n\tsignal: -61.00 dBm\n"
                                        "\tSSID: Direct\n"}
        if cmd == "tune":
            return {"ok": True, "power_save": "off", "txpower": 20.0, "limit": 20.0}
        return {"ok": True}

    def send(self, *frames, freq=2437):
        body = radiod.Relay.HEADER.pack(len(frames), 0, freq, 0.0) + b"".join(
            struct.pack("<H", len(f)) + f for f in frames)
        self.frames(body)

    def close(self):
        self.closed = True

    def names(self):
        return [cmd for cmd, _ in self.requests]


def in_thread(vif, step):
    """The frame reader in a thread instead of a process, with the same pipes."""
    context = multiprocessing.get_context("spawn")
    reader, inbox = context.Pipe(duplex=False)
    outbox, writer = context.Pipe(duplex=False)

    class Runner(threading.Thread):
        def join(self, timeout=None):
            super().join(timeout)

    runner = Runner(target=monitor.run, args=(reader, writer, vif, step), daemon=True)
    runner.start()
    return inbox, outbox, runner


def deep_with(client):
    def make(cfg):
        return deepscan.DeepScan(cfg, connect=lambda: (client, "ready", ""), process=in_thread)
    return make


def wait(condition, seconds=5.0):
    deadline = time.monotonic() + seconds
    while not condition() and time.monotonic() < deadline:
        time.sleep(0.01)
    return condition()


def test_deep_scan_logs_what_it_hears_and_puts_everything_back_at_stop(tmp_path):
    client = FakeClient()
    heard = []
    Fleet.found = [dict(networks(1)[0], bssid=AP, ssid="Harbor", rssi=-70)]
    try:
        cfg = {"deep_scan": True, "radio_tuning": True, "direct_scan": True, "drive_alerts": False}
        drive = Wardrive(cfg, Gps(FIX), root=tmp_path, adapters=[ALFA, AC1200, BUILTIN], fleet=Fleet,
                         clock=lambda: NOW, deep=deep_with(client))
        drive.start()
        assert [a["interface"] for a in drive.fleet.adapters] == ["wlan1", "wlan0"]
        assert drive.fleet.adapters[0]["helper"] is client and "helper" not in drive.fleet.adapters[1]
        assert client.names()[:5] == ["adapters", "monitor_start", "hop_plan", "capture", "tune"]
        assert ("tune", {"iface": "wlan1"}) in client.requests
        assert not any(args.get("iface") == "wlan0" for _, args in client.requests)
        plan = dict(client.requests)["hop_plan"]["plan"]
        assert plan[:3] == [[2462, 20, 250], [2437, 20, 250], [2412, 20, 250]]
        assert all(f < 5925 for f, _, _ in plan)
        client.send(on_air(beacon(bssid=AP, ssid=b"Harbor", extra=rsn())),
                    on_air(beacon(bssid="AC:84:C6:00:00:07", ssid=b"Only The Monitor", channel=3, privacy=False), 2422))
        assert wait(lambda: drive.tracker.summary()["wifi"] >= 2)
        heard.append(drive.status()["deep"])
        drive.stop()
    finally:
        Fleet.found = None
    status = heard[0]
    assert status["monitor"]["interface"] == "qmmon0" and status["monitor"]["original"] == "wlan2"
    assert drive.tracker.summary()["wifi"] == 2
    names = client.names()
    assert names[-2:] == ["monitor_stop", "untune"] and client.closed
    rows = [line.split(",") for p in drive.log_paths for line in p.read_text().splitlines()[2:]]
    found = {r[0]: r for r in rows}
    assert found["ac:84:c6:00:00:07"][1:3] == ["Only The Monitor", "[ESS]"]
    assert found["ac:84:c6:00:00:07"][4:6] == ["3", "2422"] and found["ac:84:c6:00:00:07"][-1] == "WIFI"
    assert len(found["ac:84:c6:00:00:07"]) == len(wigle.COLUMNS)


def test_a_monitor_that_fails_leaves_every_adapter_scanning(tmp_path):
    client = FakeClient(fail={"capture"})
    drive = Wardrive({"deep_scan": True, "drive_alerts": False}, Gps(FIX), root=tmp_path,
                     adapters=[ALFA, AC1200, BUILTIN], fleet=Fleet, clock=lambda: NOW, deep=deep_with(client))
    drive.start()
    assert [a["interface"] for a in drive.fleet.adapters] == ["wlan1", "wlan2", "wlan0"]
    assert "monitor_stop" in client.names()
    assert drive.status()["deep"]["error"].startswith("Monitor failed")
    drive.stop()
    alone = FakeClient()
    drive = Wardrive({"deep_scan": True, "drive_alerts": False}, Gps(FIX), root=tmp_path / "alone",
                     adapters=[ALFA, BUILTIN], fleet=Fleet, clock=lambda: NOW, deep=deep_with(alone))
    drive.start()
    assert [a["interface"] for a in drive.fleet.adapters] == ["wlan1", "wlan0"]
    assert "monitor_start" not in alone.names()
    assert "keeps 6 GHz" in drive.status()["deep"]["unavailable"]
    drive.stop()
    assert alone.closed


def test_flock_probes_and_floods_heard_by_deep_scan_reach_drive_alerts(tmp_path):
    client = FakeClient()
    drive = Wardrive({"deep_scan": True, "drive_alert_types": ["flock", "deauth"]}, Gps(FIX), root=tmp_path,
                     adapters=[ALFA, AC1200, BUILTIN], fleet=Fleet, clock=lambda: NOW, deep=deep_with(client))
    drive.start()
    client.send(on_air(probe()), *[deauth("00:11:22:33:44:55")] * 6)
    assert wait(lambda: len(drive.watch.alert_list()) >= 2)
    kinds = sorted(a["category"] for a in drive.watch.alert_list())
    assert kinds == ["deauth", "flock"]
    drive.stop()


def run_drive(root, cfg, deep):
    Fleet.found = networks(200, seed=9)
    try:
        drive = Wardrive(dict(cfg, drive_alerts=False), Gps(FIX), root=root, adapters=[ALFA, AC1200, BUILTIN],
                         fleet=Fleet, clock=lambda: NOW, deep=deep)
        drive.start()
        drive.stop()
    finally:
        Fleet.found = None
    return drive


def test_wigle_logs_are_byte_identical_with_deep_scan_off(tmp_path):
    made = []

    def spy(cfg):
        made.append(cfg)
        raise AssertionError("Deep scan must not start")
    before = run_drive(tmp_path / "before", {}, None)
    off = run_drive(tmp_path / "off", {"deep_scan": False, "direct_scan": False, "radio_tuning": False,
                                       "deep_scan_plan": "flock", "deep_scan_adapter": "wlan2"}, spy)
    assert made == [] and off.deep is None and off.status()["deep"] is None
    assert [p.name for p in before.log_paths] == [p.name for p in off.log_paths] and before.log_paths
    for a, b in zip(before.log_paths, off.log_paths):
        assert a.read_bytes() == b.read_bytes()
    assert [a["interface"] for a in off.fleet.adapters] == ["wlan1", "wlan2", "wlan0"]


def test_deep_scan_is_off_by_default_and_its_settings_load_safely(tmp_path):
    cfg = config.load(tmp_path / "none.json")
    assert cfg["deep_scan"] is False and cfg["direct_scan"] is False and cfg["radio_tuning"] is False
    assert cfg["deep_scan_plan"] == "balanced" and cfg["deep_scan_adapter"] == "auto"
    assert not radio_helper.wanted(cfg)
    path = tmp_path / "config.json"
    config.save(dict(cfg, deep_scan=True, deep_scan_adapter="wlan2", radio_tuning=True), path)
    again = config.load(path)
    assert again["deep_scan"] is True and again["deep_scan_adapter"] == "wlan2" and again["radio_tuning"] is True
    path.write_text('{"deep_scan": "yes", "deep_scan_adapter": "wlan2; reboot", "direct_scan": 1}')
    bad = config.load(path)
    assert bad["deep_scan"] is False and bad["deep_scan_adapter"] == "auto" and bad["direct_scan"] is False
    sections = [name for name, _ in config.SECTIONS]
    assert sections.index("DEEP SCAN") == sections.index("SCANNING") + 1


def test_the_settings_screen_shows_deep_scan_off_and_says_when_the_helper_is_missing(tmp_path, drawn):
    from tests.test_ui import make_app, press, screen_text
    app = make_app(tmp_path)
    app.home()
    press(app, "S")
    app.screen.index = next(i for i, item in enumerate(app.screen.items) if item[0] == "deep_scan")
    text = screen_text(app, drawn)
    assert "Deep scan\nOff" in text and "DEEP SCAN" in text
    press(app, "RIGHT")
    assert app.cfg["deep_scan"] is True and config.load(tmp_path / "config.json")["deep_scan"] is True
    text = screen_text(app, drawn).replace("\n", " ")
    assert "Helper: Radio helper not installed." in text
    press(app, "RIGHT")
    assert app.cfg["deep_scan"] is False


def test_the_hardware_screen_names_the_monitor_and_the_6_ghz_keeper():
    from quartermaster.ui.screens import Hardware
    deep = {"state": "ready", "monitor": {"interface": "qmmon0", "label": "MT7921AU (AC1200)", "original": "wlan2"},
            "reasons": {"wlan0": deepscan.REASONS["builtin"]}, "stats": {"frames_per_s": 1240.4,
                                                                        "dropped_share": 0.3},
            "counts": {"flock": 3}, "plan": "balanced", "level": 0, "tuning": {"wlan1": {"txpower": 20.0,
                                                                                         "limit": 30.0}},
            "error": "", "unavailable": ""}
    card = Hardware.monitor_card(deep["monitor"], deep, None)
    assert card["tag"] == "MON" and card["right"] == "3 Flock"
    assert card["detail"] == "Deep scan: ch 11·6·1 + 2.4 + 5 GHz busy  ·  1,240 fr/s  ·  0.3% dropped"
    keeper = Hardware.deep_words(ALFA, deep, deep["monitor"])
    assert keeper["detail"].startswith("Keeps 6 GHz") and "tx 20 dBm (limit 30)" in keeper["detail"]
    assert Hardware.deep_words(BUILTIN, deep, deep["monitor"])["detail"] == "Built-in: always scans normally"

    class App:
        cfg = {"deep_scan": True}
    assert Hardware.deep_card(App, dict(deep, unavailable="Needs a second USB adapter"))["title"] == \
        "Deep scan unavailable"
    assert "not installed" in Hardware.deep_card(App, dict(deep, state="missing"))["detail"]
    App.cfg = {"deep_scan": False}
    assert Hardware.deep_card(App, deep) is None


def test_direct_scans_go_through_the_helper_and_fall_back_when_it_refuses(monkeypatch):
    from quartermaster import wifi
    from quartermaster.wifi import AdapterScanner
    monkeypatch.setattr(wifi.shutil, "which", lambda name: None)
    client = FakeClient()
    scanner = AdapterScanner(dict(AC1200, helper=client), runner=lambda *a, **k: None)
    found = scanner.scan()
    assert scanner.method == "helper" and [n["ssid"] for n in found] == ["Direct"]
    assert client.requests[-1] == ("scan", {"iface": "wlan2"})
    refusing = FakeClient(fail={"scan"})
    calls = []

    def runner(args, **kwargs):
        calls.append(args)
        import subprocess
        return subprocess.CompletedProcess(args, 0, "BSS 00:11:22:33:44:77(on wlan2)\n\tfreq: 2412\n"
                                                    "\tsignal: -40.00 dBm\n\tSSID: Old way\n", "")
    scanner = AdapterScanner(dict(AC1200, helper=refusing), runner=runner)
    with pytest.raises(wifi.ScanUnavailable):
        scanner.scan()
    monkeypatch.setattr(wifi.shutil, "which", lambda name: "/usr/sbin/" + name)
    scanner = AdapterScanner(dict(AC1200, helper=FakeClient(fail={"scan"})), runner=runner)
    assert [n["ssid"] for n in scanner.scan()] == ["Old way"] and calls[0][1:] == ["dev", "wlan2", "scan"]
    assert scanner.adapter["helper"] is None


def test_the_frame_reader_runs_as_its_own_process():
    context = multiprocessing.get_context("spawn")
    reader, inbox = context.Pipe(duplex=False)
    outbox, writer = context.Pipe(duplex=False)
    worker = context.Process(target=monitor.run, args=(reader, writer, "qmmon0", 4), daemon=True)
    worker.start()
    reader.close()
    writer.close()
    try:
        frames = [on_air(beacon()), on_air(probe())]
        body = radiod.Relay.HEADER.pack(2, 0, 2437, 0.0) + b"".join(struct.pack("<H", len(f)) + f for f in frames)
        inbox.send_bytes(body)
        got = {}
        deadline = time.monotonic() + 20
        while len(got) < 2 and time.monotonic() < deadline:
            if outbox.poll(0.2):
                kind, payload = outbox.recv()
                got[kind] = payload
        assert got["frame"]["rule"] == "probe" and got["networks"][0]["bssid"] == AP
        inbox.send_bytes(b"")
        worker.join(timeout=10)
        assert worker.exitcode == 0
    finally:
        if worker.is_alive():
            worker.terminate()
            worker.join()
        inbox.close()
        outbox.close()


def test_recon_hears_deep_scan_too(tmp_path):
    from quartermaster.recon_engine import Recon
    from tests.fakes import NoBle, QuietFleet
    client = FakeClient()
    r = Recon({"deep_scan": True}, Gps(FIX), root=tmp_path, adapters=[ALFA, AC1200, BUILTIN], ble=NoBle(),
              fleet=QuietFleet, clock=lambda: NOW, deep=deep_with(client))
    r.start()
    try:
        assert [a["interface"] for a in r.fleet.adapters] == ["wlan1", "wlan0"]
        reply = beacon(fc=0x50)[:4] + mac(FLOCK) + beacon(fc=0x50)[10:]
        client.send(on_air(probe("70:C9:4E:00:00:08")), on_air(reply))
        assert wait(lambda: ("wifi", FLOCK) in r.devices and ("wifi", "70:C9:4E:00:00:08") in r.devices)
        assert r.devices[("wifi", "70:C9:4E:00:00:08")]["confidence"] == recon.MEDIUM
        assert r.devices[("wifi", FLOCK)]["confidence"] == recon.LOW
    finally:
        r.stop()
    assert "monitor_stop" in client.names() and client.closed


def lap(path, rows):
    lines = ["WigleWifi-1.6,appRelease=Quartermaster", ",".join(wigle.COLUMNS)]
    for mac_, freq, second in rows:
        stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(NOW + second))
        lines.append("%s,net,[ESS],%s,1,%d,-60,25.0,-77.0,1,5,,,WIFI" % (mac_, stamp, freq))
    path.write_text("\n".join(lines) + "\n")
    return path


def test_the_ab_tool_counts_each_band_and_fails_when_a_band_drops(tmp_path):
    import importlib.util
    from pathlib import Path
    tools = Path(__file__).resolve().parents[1] / "tools"
    spec = importlib.util.spec_from_file_location("deepscan_ab", tools / "deepscan_ab.py")
    ab = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ab)
    a = lap(tmp_path / "a.csv", [("aa:00:00:00:00:01", 2412, 0), ("aa:00:00:00:00:02", 5180, 30),
                                 ("aa:00:00:00:00:03", 6135, 60)])
    b = lap(tmp_path / "b.csv", [("aa:00:00:00:00:01", 2412, 0), ("aa:00:00:00:00:04", 2437, 5),
                                 ("aa:00:00:00:00:02", 5180, 10), ("aa:00:00:00:00:03", 6135, 20)])
    result = ab.compare([a], [b])
    assert {k: (v["a"], v["b"], v["pass"]) for k, v in result.items()} == \
        {"2.4": (1, 2, True), "5": (1, 1, True), "6": (1, 1, True)}
    assert result["2.4"]["share"] == 1.0 and result["5"]["b_delay"] == 10
    worse = lap(tmp_path / "worse.csv", [("aa:00:00:00:00:01", 2412, 0), ("aa:00:00:00:00:02", 5180, 10)])
    assert ab.compare([a], [worse])["6"]["pass"] is False


def test_the_bench_replays_a_dense_street_through_the_reader():
    import importlib.util
    from pathlib import Path
    tools = Path(__file__).resolve().parents[1] / "tools"
    spec = importlib.util.spec_from_file_location("bench_monitor", tools / "bench_monitor.py")
    bench = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bench)
    worker = monitor.Worker()
    for raw in bench.street(400, 20):
        worker.feed(raw, 0.0)
    assert len(worker.tick(2.0)) == 20


def started_deep(client, adapters=(ALFA, AC1200, BUILTIN), **cfg):
    deep = deepscan.DeepScan(dict({"deep_scan": True}, **cfg), connect=lambda: (client, "ready", ""),
                             process=in_thread)
    assert deep.open()
    deep.plan_roles(list(adapters))
    assert deep.start(lambda networks, adapter: None)
    return deep


def test_the_monitor_is_handed_back_when_the_6_ghz_keeper_is_unplugged():
    client = FakeClient()
    deep = started_deep(client)
    try:
        now = deep.follow([ALFA, dict(AC1200, interface="qmmon0", type="803"), BUILTIN])
        assert [a["interface"] for a in now] == ["wlan1", "wlan0"] and deep.vif == "qmmon0"
        assert [a["interface"] for a in deep.follow([BUILTIN, dict(AC1200, interface="qmmon0", type="803")])] == \
            ["wlan0"]
        assert deep.vif is None and "handed back" in deep.error and "monitor_stop" in client.names()
        assert [a["interface"] for a in deep.follow([AC1200, BUILTIN])] == ["wlan2", "wlan0"]
    finally:
        deep.stop()


def test_a_busy_cpu_steps_down_and_at_last_hands_the_adapter_back():
    client = FakeClient()
    deep = started_deep(client)
    try:
        hot = {"cpu": 40.0, "channels": {}}
        deep._handle("stats", hot)
        assert client.requests[-1] == ("capture", {"vif": "qmmon0", "mgmt_only": True})
        deep._handle("stats", hot)
        assert deep.level == 2
        deep._handle("stats", hot)
        assert deep.plan.mode == "flock" and client.requests[-1][0] == "hop_plan"
        assert client.requests[-1][1]["plan"][:3] == [[2462, 20, 250], [2437, 20, 250], [2412, 20, 250]]
        plan = deep.plan
        deep._handle("stats", dict(hot, switch_gap_ms=55))
        assert deep.vif is None and deep.error.startswith("Busy CPU") and plan.min_dwell == 150
        sent = len(client.requests)
        deep._plan_at = 0.0
        deep._upkeep()
        assert len(client.requests) == sent and deep.plan is None and deep.error.startswith("Busy CPU")
    finally:
        deep.stop()
    assert client.closed


def test_a_batch_that_starts_like_the_slow_parse_word_is_still_read():
    context = multiprocessing.get_context("spawn")
    reader, inbox = context.Pipe(duplex=False)
    outbox, writer = context.Pipe(duplex=False)
    runner = threading.Thread(target=monitor.run, args=(reader, writer, "qmmon0", 4), daemon=True)
    runner.start()
    try:
        frame = on_air(probe())
        body = radiod.Relay.HEADER.pack(0x53, 0, 2437, 0.0) + struct.pack("<H", len(frame)) + frame
        assert body[:1] == monitor.SLOW_PARSE
        inbox.send_bytes(body)
        assert outbox.poll(5) and outbox.recv()[0] == "frame"
        inbox.send_bytes(monitor.SLOW_PARSE)
        inbox.send_bytes(b"")
        runner.join(timeout=5)
        assert not runner.is_alive()
    finally:
        inbox.close()
        outbox.close()


def test_frames_the_kernel_filter_cut_short_keep_their_last_bytes_with_an_fcs_flag():
    head = header(0xb0, AP, FLOCK, AP)
    raw = on_air(head, flags=0x10)
    length, end, _, _ = monitor.radiotap(raw)
    assert raw[length:end] == head
    event = monitor.Worker().feed(raw, 0.0)
    assert event and event[0][1]["rule"] == "addr2"
    data = (header(0x08, AP, FLOCK, AP) + bytes(8))[:32]
    length, end, _, _ = monitor.radiotap(on_air(data, flags=0x10))
    assert end - length == 32
    short = on_air(header(0x48, AP, FLOCK, AP) + b"\x00\x00" + b"\xde\xad\xbe\xef", flags=0x10)
    length, end, _, _ = monitor.radiotap(short)
    assert end - length == 26


def test_silent_5_ghz_channels_cool_down_and_the_quiet_ones_get_their_turn_again():
    plan = deepscan.HopPlan("US", "balanced")
    busy = {deepscan.freq_of("5", ch): 30.0 for ch in plan.ghz5[:7]}
    for _ in range(3):
        plan.heard(busy)
    five = [f for f, _ in plan._slice5()]
    assert len(five) == 6 and set(five) <= set(busy)
    plan.heard({**busy, deepscan.freq_of("5", plan.ghz5[6]): 90.0})
    assert deepscan.freq_of("5", plan.ghz5[6]) in [f for f, _ in plan._slice5()]
    for _ in range(40):
        plan.heard({})
    assert not any(plan.rates5.values()) and plan.rates2 == {}
    quiet = plan._slice5()
    assert len(quiet) == 2 and all(d == deepscan.LOOK5_QUIET for _, d in quiet)


class TimedClient(FakeClient):
    def __init__(self, cycle_ms):
        super().__init__()
        self.cycle_ms = cycle_ms

    def request(self, cmd, timeout=None, **args):
        reply = super().request(cmd, timeout, **args)
        return dict(reply, cycle_ms=self.cycle_ms) if cmd == "hop_plan" else reply


def test_the_plan_is_sent_again_only_after_the_helper_finished_a_round():
    deep = started_deep(TimedClient(4750))
    try:
        assert deep._cycle == 4.75
        steps = [(f, d) for f, d in deep.plan.build()]
        assert deepscan.HopPlan.cycle_seconds(steps) > sum(d for _, d in steps) / 1000.0
    finally:
        deep.stop()
    quick = started_deep(TimedClient(None))
    try:
        assert quick._cycle == deepscan.HopPlan.cycle_seconds(quick.plan.build())
    finally:
        quick.stop()


def test_a_monitor_that_went_away_is_let_go_and_its_name_scans_again():
    client = FakeClient()
    deep = started_deep(client)
    try:
        monitor_vif = dict(AC1200, interface="qmmon0", type="803")
        now = deep.follow([ALFA, BUILTIN], present=[ALFA, monitor_vif, BUILTIN])
        assert [a["interface"] for a in now] == ["wlan1", "wlan0"] and deep.vif == "qmmon0"
        back = deep.follow([ALFA, AC1200, BUILTIN])
        assert [a["interface"] for a in back] == ["wlan1", "wlan2", "wlan0"]
        assert deep.vif is None and deep.monitor is None and deep.plan is None and "went away" in deep.error
    finally:
        deep.stop()
    helper = FakeClient()
    helper.closed = threading.Event()
    deep = started_deep(helper)
    try:
        helper.closed.set()
        deep._upkeep()
        assert deep.vif is None and "helper stopped" in deep.error
    finally:
        deep.stop()


def test_the_monitor_is_handed_back_when_the_node_covering_its_band_leaves():
    client = FakeClient()
    deep = deepscan.DeepScan({"deep_scan": True}, connect=lambda: (client, "ready", ""), process=in_thread)
    builtin = dict(BUILTIN, bands=["2.4"])
    assert deep.open()
    assert deep.plan_roles([builtin, AC1200_WIFI5], ("wifi24", "wifi5")) == [builtin]
    assert deep.start(lambda networks, adapter: None)
    try:
        vif = dict(AC1200_WIFI5, interface="qmmon0", type="803")
        assert deep.follow([builtin, vif], nodes=("wifi24", "wifi5")) == [builtin] and deep.vif == "qmmon0"
        deep.follow([builtin, vif], nodes=("wifi24",))
        assert deep.vif is None and "handed back" in deep.error
    finally:
        deep.stop()


def test_the_cpu_ladder_counts_the_helper_and_the_threads_in_the_app_too(tmp_path):
    (tmp_path / "42").mkdir()
    (tmp_path / "42" / "stat").write_text("42 (python3 radiod) S 1 42 42 0 -1 4194560 1 0 0 0 250 50 0 0 20 0\n")
    tick = deepscan.os.sysconf("SC_CLK_TCK")
    assert deepscan._process_cpu(42, str(tmp_path)) == 300 / tick
    assert deepscan._process_cpu(None) == 0.0 and deepscan._process_cpu(43, str(tmp_path)) == 0.0
    client = FakeClient()
    deep = started_deep(client)
    try:
        client.cpu = 0.0
        deep._cpu_at = (time.monotonic() - 10.0, 0.0)
        client.cpu = 2.0
        deep._handle("stats", {"cpu": 20.0, "channels": {}})
        assert deep.stats["worker_cpu"] == 20.0 and deep.stats["cpu"] >= 39.0
        assert deep.level == 1 and client.requests[-1] == ("capture", {"vif": "qmmon0", "mgmt_only": True})
    finally:
        deep.stop()
