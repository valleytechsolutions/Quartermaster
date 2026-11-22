import hashlib
import http.client
import json
import urllib.parse

import pytest

from quartermaster import accounts, hardware, qr, uploads
from quartermaster.setup_server import SetupServer
from tests.test_uploads import KEYS, make_log


class Browser:
    def __init__(self, port):
        self.port, self.cookie = port, ""

    def request(self, method, path, form=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        headers = {"Cookie": self.cookie} if self.cookie else {}
        body = urllib.parse.urlencode(form) if form is not None else None
        if body is not None:
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        conn.request(method, path, body, headers)
        reply = conn.getresponse()
        data = reply.read()
        if reply.getheader("Set-Cookie"):
            self.cookie = reply.getheader("Set-Cookie").split(";")[0]
        conn.close()
        return reply.status, data.decode("utf-8", "replace"), dict(reply.getheaders())


@pytest.fixture
def setup(tmp_path):
    make_log(tmp_path / "logs")
    (tmp_path / "logs" / "quartermaster_20261118-110640.summary.csv").write_text("MAC\n")
    (tmp_path / "secret.txt").write_text("keep out")
    events = []
    cfg = {"ignore": ["Old"]}
    server = SetupServer(tmp_path / "logs", cfg, tmp_path / "accounts.json", port=0, host="127.0.0.1",
                         on_keys=lambda: events.append("keys"), on_ignore=lambda items: events.append(items),
                         on_upload=lambda: events.append("upload"),
                         tester=lambda service, saved: uploads.Outcome(uploads.DONE, "ok for " + service),
                         marks=lambda name: {"wigle": "done", "wdgwars": "waiting", "wardrift": ""})
    server.start()
    server.events, server.dir = events, tmp_path
    yield server
    server.stop()


def unlocked(setup):
    browser = Browser(setup.port)
    status, _, headers = browser.request("POST", "/unlock", {"pin": setup.pin})
    assert status == 303 and "HttpOnly" in headers["Set-Cookie"] and "SameSite=Strict" in headers["Set-Cookie"]
    return browser


def test_nothing_is_served_without_the_pin(setup):
    browser = Browser(setup.port)
    status, page, headers = browser.request("GET", "/")
    assert status == 200 and "Enter the PIN" in page and "API token" not in page
    assert headers["Cache-Control"] == "no-store" and "default-src 'none'" in headers["Content-Security-Policy"]
    assert browser.request("GET", "/log/quartermaster_20261118-110640.wigle.csv")[1].count("Enter the PIN") == 1
    assert browser.request("POST", "/save", {"wdgwars_key": "x" * 64, "csrf": setup.csrf})[0] == 403
    assert accounts.load(setup.dir / "accounts.json")["wdgwars_key"] == ""


def test_wrong_pins_lock_the_page(setup):
    browser = Browser(setup.port)
    for _ in range(5):
        assert browser.request("POST", "/unlock", {"pin": "000000" if setup.pin != "000000" else "111111"})[0] == 403
    status, page, _ = browser.request("POST", "/unlock", {"pin": setup.pin})
    assert status == 429 and "Too many wrong PINs" in page and setup.locked


def test_keys_are_saved_tested_and_never_shown(setup):
    browser = unlocked(setup)
    status, page, _ = browser.request("GET", "/")
    assert status == 200 and "WiGLE" in page and "Not set" in page
    token = page.split('name="csrf" value="')[1].split('"')[0]
    assert browser.request("POST", "/save", dict(KEYS))[0] == 403
    assert browser.request("POST", "/save", dict(KEYS, csrf=token))[2]["Location"] == "/?keys"
    saved = accounts.load(setup.dir / "accounts.json")
    assert saved == KEYS and setup.events == ["keys"]
    page = browser.request("GET", "/?keys")[1]
    assert "Keys saved." in page and "Saved: ••••" in page
    for secret in KEYS.values():
        assert secret not in page
    reply = json.loads(browser.request("POST", "/test", {"service": "wardrift", "csrf": token,
                                                         "wardrift_key": "wdk_new_key_123456"})[1])
    assert reply == {"ok": True, "detail": "ok for wardrift"}
    assert accounts.load(setup.dir / "accounts.json")["wardrift_key"] == "wdk_new_key_123456"
    assert browser.request("POST", "/save", {"csrf": token})[2]["Location"] == "/?nothing"
    browser.request("POST", "/remove", {"csrf": token, "service": "wigle"})
    assert accounts.ready(accounts.load(setup.dir / "accounts.json")) == ["wdgwars", "wardrift"]


def test_logs_ignore_list_and_upload_button(setup):
    browser = unlocked(setup)
    page = browser.request("GET", "/")[1]
    token = page.split('name="csrf" value="')[1].split('"')[0]
    assert "20261118-110640" in page and "summary" in page and "WiGLE" in page and ">Old<" in page
    status, body, headers = browser.request("GET", "/log/quartermaster_20261118-110640.wigle.csv")
    assert status == 200 and body.startswith("WigleWifi-1.6,") and "attachment" in headers["Content-Disposition"]
    for path in ("/log/../secret.txt", "/log/..%2Fsecret.txt", "/log/secret.txt", "/log/accounts.json"):
        assert browser.request("GET", path)[0] == 404
    browser.request("POST", "/ignore", {"csrf": token, "ignore": "Home WiFi\r\n\r\nAA:BB:CC:DD:EE:FF\nHome WiFi\n"})
    assert setup.cfg["ignore"] == ["Home WiFi", "AA:BB:CC:DD:EE:FF"] and setup.events[-1] == setup.cfg["ignore"]
    browser.request("POST", "/upload", {"csrf": token})
    assert setup.events[-1] == "upload"
    assert browser.request("POST", "/save", {"csrf": token, "wdgwars_key": "k" * 40000})[0] == 413


def test_qr_codes_match_the_reference_encoder():
    expected = {"Q": (21, "c0ebe41c9d39d6d8"), "http://192.168.1.50:8642/#123456": (25, "6333a0e91835e314"),
                "http://192.168.100.123:8642/#654321": (29, "5d934c4520704712"),
                "Quartermaster " * 5: (33, "33e26fe974635d7e"), "x" * 106: (37, "2132e1b8ef0d3c67"),
                "Z" * 134: (41, "d373c5c18af07327")}
    for text, (size, digest) in expected.items():
        check = hashlib.sha256()
        for mask in range(8):
            grid = qr.encode(text, mask=mask)
            assert len(grid) == size and all(len(row) == size for row in grid)
            for row in grid:
                check.update(bytes(row))
        assert check.hexdigest()[:16] == digest
        assert qr.encode(text) in [qr.encode(text, mask=mask) for mask in range(8)]
    with pytest.raises(ValueError):
        qr.encode("x" * 135)
    assert qr.image("Q", scale=3, border=2).size == (75, 75)


def test_usb_adapter_without_an_interface_is_reported(tmp_path):
    def device(port, vendor, product, name, interface=None):
        node = tmp_path / port
        (node / (port + ":1.0")).mkdir(parents=True)
        (node / "idVendor").write_text(vendor + "\n")
        (node / "idProduct").write_text(product + "\n")
        (node / "product").write_text(name + "\n")
        if interface:
            (node / (port + ":1.0") / "net" / interface).mkdir(parents=True)

    device("1-1.1", "0e8d", "7961", "Wireless_Device", "wlan1")
    device("1-1.2", "0e8d", "7961", "Wireless_Device")
    device("1-1.3", "10c4", "ea60", "CP2102N USB to UART Bridge Controller")
    device("1-1.4", "2357", "0138", "802.11ac NIC")
    device("usb1", "1d6b", "0002", "DWC OTG Controller")
    stalled = hardware.stalled_usb_adapters(tmp_path)
    assert [(s["port"], s["usb_id"]) for s in stalled] == [("1-1.2", "0e8d:7961"), ("1-1.4", "2357:0138")]
    assert stalled[0]["label"].startswith("MT7921AU")


def test_local_addresses_prefers_wifi():
    class Result:
        returncode = 0
        stdout = ("2: eth0    inet 10.0.0.9/24 brd 10.0.0.255 scope global eth0\n"
                  "3: wlan0    inet 192.168.1.50/24 brd 192.168.1.255 scope global dynamic noprefixroute wlan0\n"
                  "4: docker0    inet 172.17.0.1/16 scope global docker0\n")

    assert hardware.local_addresses(lambda *a, **k: Result()) == ["192.168.1.50", "10.0.0.9"]
