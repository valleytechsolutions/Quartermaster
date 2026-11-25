import base64
import gzip
import http.server
import json
import threading
import time

import pytest

from quartermaster import accounts, adsb, uploads, wigle
from tests.test_core import FIX, NOW

KEYS = {"wigle_name": "AIDabcdef0123456789", "wigle_token": "0123456789abcdef", "wdgwars_key": "f" * 64,
        "wardrift_key": "wdk_live_1234567890"}


class Site:
    """One local server standing in for all three upload sites."""

    def __init__(self):
        self.requests, self.replies = [], {}
        site = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def answer(self):
                body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
                site.requests.append({"method": self.command, "path": self.path,
                                      "headers": {k.lower(): v for k, v in self.headers.items()}, "body": body})
                status, payload = site.replies.get((self.command, self.path), (404, {"message": "not found"}))
                data = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            do_GET = do_POST = answer

            def log_message(self, *args):
                pass

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = "http://127.0.0.1:%d" % self.server.server_address[1]
        self.endpoints = {name: self.url for name in uploads.SERVICES}
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def accept_everything(self):
        self.replies.update({
            ("POST", "/api/v2/file/upload"): (200, {"success": True, "results": {"transids": [{"transId": "T-1"}]}}),
            ("POST", "/api/v2/upload-csv"): (202, {"ok": True, "job_id": 77}),
            ("POST", "/v1/ingest/signals"): (200, {"item_count": 2})})

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def site():
    server = Site()
    yield server
    server.close()


def make_log(directory, name="quartermaster_20261118-110640.wigle.csv", rows=2):
    directory.mkdir(parents=True, exist_ok=True)
    writer = wigle.WigleWriter(directory / name, "13")
    for i in range(rows):
        writer.write("WIFI", {"mac": "AA:BB:CC:DD:EE:%02X" % i, "name": "net, %d" % i}, -60 - i, FIX, NOW + i)
    writer.close()
    return directory / name


def test_wigle_upload_is_a_multipart_file_with_basic_auth(site, tmp_path):
    log = make_log(tmp_path)
    site.accept_everything()
    outcome = uploads.upload("wigle", log, KEYS, site.endpoints)
    assert outcome.state == uploads.DONE and outcome.ref == "T-1"
    request = site.requests[0]
    expected = base64.b64encode(b"AIDabcdef0123456789:0123456789abcdef").decode()
    assert request["headers"]["authorization"] == "Basic " + expected
    boundary = request["headers"]["content-type"].split("boundary=")[1]
    head, _, rest = request["body"].partition(b"\r\n\r\n")
    assert head == ('--%s\r\nContent-Disposition: form-data; name="file"; filename="%s"\r\nContent-Type: text/csv'
                    % (boundary, log.name)).encode()
    assert rest == log.read_bytes() + ("\r\n--%s--\r\n" % boundary).encode()
    assert int(request["headers"]["content-length"]) == len(request["body"])
    assert request["headers"]["user-agent"].startswith("Quartermaster/")


def test_wdgwars_upload_and_its_answers(site, tmp_path):
    log = make_log(tmp_path)
    site.accept_everything()
    outcome = uploads.upload("wdgwars", log, KEYS, site.endpoints)
    assert outcome.state == uploads.DONE and outcome.ref == "77"
    assert site.requests[0]["headers"]["x-api-key"] == "f" * 64 and log.read_bytes() in site.requests[0]["body"]
    site.replies[("POST", "/api/v2/upload-csv")] = (409, {"error": "duplicate_upload", "duplicate_at": "2026"})
    assert uploads.upload("wdgwars", log, KEYS, site.endpoints).state == uploads.DUPLICATE
    site.replies[("POST", "/api/v2/upload-csv")] = (401, {"error": "bad key"})
    assert uploads.upload("wdgwars", log, KEYS, site.endpoints).state == uploads.AUTH
    site.replies[("POST", "/api/v2/upload-csv")] = (503, b"busy")
    assert uploads.upload("wdgwars", log, KEYS, site.endpoints).state == uploads.RETRY
    # An older server without the v2 route still gets the file.
    del site.replies[("POST", "/api/v2/upload-csv")]
    site.replies[("POST", "/api/upload-csv")] = (200, {"ok": True})
    assert uploads.upload("wdgwars", log, KEYS, site.endpoints).state == uploads.DONE


def test_wardrift_upload_is_the_raw_csv(site, tmp_path):
    log = make_log(tmp_path)
    site.accept_everything()
    outcome = uploads.upload("wardrift", log, KEYS, site.endpoints)
    assert outcome.state == uploads.DONE and "2 signals" in outcome.detail
    request = site.requests[0]
    assert request["body"] == log.read_bytes() and request["headers"]["content-type"] == "text/csv"
    assert request["headers"]["x-file-name"] == log.name and request["headers"]["x-api-key"] == KEYS["wardrift_key"]
    assert "content-encoding" not in request["headers"]
    site.replies[("POST", "/v1/ingest/signals")] = (200, {"duplicate": True})
    assert uploads.upload("wardrift", log, KEYS, site.endpoints).state == uploads.DUPLICATE
    site.replies[("POST", "/v1/ingest/signals")] = (409, {"message": "sequence conflict"})
    conflict = uploads.upload("wardrift", log, KEYS, site.endpoints)
    assert conflict.state == uploads.REJECTED and "sequence conflict" in conflict.detail
    site.replies[("POST", "/v1/ingest/signals")] = (400, {"message": "file did not contain supported signal rows"})
    assert uploads.upload("wardrift", log, KEYS, site.endpoints).state == uploads.REJECTED


def test_wardrift_size_limit_is_checked_before_sending(site, tmp_path, monkeypatch):
    log = make_log(tmp_path)
    monkeypatch.setattr(uploads, "WARDRIFT_MAX_BYTES", 10)
    assert uploads.upload("wardrift", log, KEYS, site.endpoints).state == uploads.REJECTED
    assert site.requests == []


def test_key_checks(site):
    site.replies[("GET", "/api/v2/profile/user")] = (200, {"success": True, "userid": "blackbeard"})
    site.replies[("GET", "/api/me")] = (200, {"username": "anne"})
    site.replies[("POST", "/v1/ingest/signals")] = (400, {"message": "no supported observations"})
    assert uploads.test("wigle", KEYS, site.endpoints).detail == "Signed in to WiGLE as blackbeard"
    assert uploads.test("wdgwars", KEYS, site.endpoints).detail == "Signed in to WDGWars as anne"
    wardrift = uploads.test("wardrift", KEYS, site.endpoints)
    assert wardrift.state == uploads.DONE
    body = site.requests[-1]["body"].decode().splitlines()
    assert body[0].startswith("WigleWifi-1.6,appRelease=Quartermaster") and body[1].startswith("MAC,SSID,") \
        and len(body) == 2
    site.replies[("GET", "/api/v2/profile/user")] = (401, {"success": False, "message": "no"})
    site.replies[("POST", "/v1/ingest/signals")] = (403, {})
    assert uploads.test("wigle", KEYS, site.endpoints).state == uploads.AUTH
    assert uploads.test("wardrift", KEYS, site.endpoints).state == uploads.AUTH
    site.replies[("GET", "/api/me")] = (500, {})
    assert uploads.test("wdgwars", KEYS, site.endpoints).state == uploads.RETRY
    assert uploads.test("wigle", dict(KEYS, wigle_token=""), site.endpoints).state == uploads.AUTH


def test_no_connection_is_retried_not_lost(tmp_path):
    log = make_log(tmp_path)
    dead = {name: "http://127.0.0.1:9" for name in uploads.SERVICES}
    for service in uploads.SERVICES:
        assert uploads.upload(service, log, KEYS, dead).state == uploads.RETRY


def uploader(tmp_path, site, cfg=None, active=None):
    path = tmp_path / "accounts.json"
    accounts.save(KEYS, path)
    return uploads.Uploader(tmp_path, cfg or {}, active=active, accounts_path=path, endpoints=site.endpoints,
                            clock=lambda: NOW)


def test_uploader_sends_each_log_to_each_site_once(site, tmp_path):
    first = make_log(tmp_path / "logs", "quartermaster_20261118-110640.wigle.csv")
    second = make_log(tmp_path / "logs", "quartermaster_20261118-120000.wigle.csv")
    active = make_log(tmp_path / "logs", "quartermaster_20261118-130000.wigle.csv")
    make_log(tmp_path / "logs", "quartermaster_20261118-140000.wigle.csv", rows=0)
    site.accept_everything()
    up = uploader(tmp_path, site, active=lambda: active)
    assert up.run_pass() is False
    sent = [(r["path"], r["headers"].get("x-file-name") or r["body"].split(b'filename="')[1].split(b'"')[0].decode())
            for r in site.requests]
    assert len(sent) == 6 and {name for _, name in sent} == {first.name, second.name}
    assert up.status()["waiting"] == 0 and up.status()["services"]["wigle"]["sent"] == 2
    assert up.marks(first.name) == {"wigle": "done", "wdgwars": "done", "wardrift": "done"}
    up.run_pass()
    assert len(site.requests) == 6
    # A new uploader reads the same ledger, so nothing is sent twice after a restart.
    again = uploader(tmp_path, site, active=lambda: active)
    assert again.run_pass() is False and len(site.requests) == 6
    # Once the wardrive ends, its own log goes out too.
    again.active = lambda: None
    again.run_pass()
    assert len(site.requests) == 9 and again.marks(active.name)["wardrift"] == "done"


def test_uploader_handles_bad_keys_busy_sites_and_refusals(site, tmp_path):
    log = make_log(tmp_path / "logs")
    site.accept_everything()
    site.replies[("POST", "/api/v2/file/upload")] = (401, {"success": False})
    site.replies[("POST", "/api/v2/upload-csv")] = (503, {})
    site.replies[("POST", "/v1/ingest/signals")] = (400, {"message": "bad rows"})
    up = uploader(tmp_path, site)
    assert up.run_pass() is True
    status = up.status()["services"]
    assert status["wigle"]["blocked"] and status["wdgwars"]["waiting"] == 1 and status["wardrift"]["refused"] == 1
    assert up.marks(log.name) == {"wigle": "waiting", "wdgwars": "waiting", "wardrift": "rejected"}
    # The rejected key is not hammered, the busy site is tried again, the refused file is left alone.
    site.accept_everything()
    count = len(site.requests)
    assert up.run_pass() is False
    assert [r["path"] for r in site.requests[count:]] == ["/api/v2/upload-csv"]
    # Saving the keys again unblocks WiGLE, and "send again" reopens the refused file.
    accounts.save(KEYS, tmp_path / "accounts.json")
    up._stamp = None
    up.ledger.reopen(log.name)
    assert up.run_pass() is False
    assert up.marks(log.name) == {"wigle": "done", "wdgwars": "done", "wardrift": "done"}


def test_only_sites_with_keys_are_used(site, tmp_path):
    log = make_log(tmp_path / "logs")
    site.accept_everything()
    accounts.save({"wdgwars_key": "f" * 64}, tmp_path / "accounts.json")
    up = uploads.Uploader(tmp_path, {}, accounts_path=tmp_path / "accounts.json", endpoints=site.endpoints)
    up.run_pass()
    assert [r["path"] for r in site.requests] == ["/api/v2/upload-csv"]
    assert up.marks(log.name) == {"wigle": "", "wdgwars": "done", "wardrift": ""}


def test_missing_auto_upload_setting_uses_upstream_default(tmp_path):
    up = uploads.Uploader(tmp_path, {}, accounts_path=tmp_path / "accounts.json")
    assert up.status()["auto"] is True

    calls = []

    class Wake:
        waits = 0

        def wait(self, delay):
            self.waits += 1
            if self.waits == 2:
                up._stop.set()
            return self.waits == 1

        def clear(self):
            pass

    up._wake = Wake()
    up.run_pass = lambda: calls.append(True)
    up._loop()
    assert calls == [True]


def test_accounts_file_is_private_and_takes_the_encoded_wigle_string(tmp_path):
    path = tmp_path / "keys" / "accounts.json"
    encoded = base64.b64encode(b"AIDabcdef0123456789:0123456789abcdef").decode()
    saved = accounts.update({"wigle_encoded": " %s\n" % encoded, "wdgwars_key": " ab cd\t12 "}, path)
    assert saved["wigle_name"] == "AIDabcdef0123456789" and saved["wigle_token"] == "0123456789abcdef"
    assert saved["wdgwars_key"] == "abcd12" and path.stat().st_mode & 0o777 == 0o600
    assert accounts.ready(accounts.load(path)) == ["wigle", "wdgwars"]
    assert accounts.masked("0123456789abcdef") == "••••cdef" and accounts.masked("") == ""
    assert accounts.split_encoded("not base64!") is None and accounts.split_encoded("AIDabcdef0123456789") is None


def test_a_log_started_while_the_folder_is_listed_is_not_sent(site, tmp_path):
    finished = make_log(tmp_path / "logs", "quartermaster_20261118-110640.wigle.csv")
    started = make_log(tmp_path / "logs", "quartermaster_20261118-120000.wigle.csv")
    answers = iter([None, started])
    up = uploader(tmp_path, site, active=lambda: next(answers, started))
    assert up.logs() == [finished]


def make_aircraft(directory, now, name="quartermaster_20261118-110640.aircraft.json.gz", count=2):
    from tests.fakes import plane
    directory.mkdir(parents=True, exist_ok=True)
    sky = adsb.Sky()
    sky.update({"aircraft": [plane("%06x" % (0xa1b2c3 + i), 25.2, -77.7 + i / 100) for i in range(count)]}, now - 60,
               (FIX["latitude"], FIX["longitude"]))
    adsb.write(directory / name, sky, now)
    return directory / name


def test_wdgwars_takes_the_aircraft_file_gzipped_on_its_csv_route(site, tmp_path):
    path = make_aircraft(tmp_path, NOW)
    site.accept_everything()
    outcome = uploads.upload("wdgwars", path, KEYS, site.endpoints)
    assert outcome.state == uploads.DONE and outcome.ref == "77"
    request = site.requests[0]
    assert request["path"] == "/api/v2/upload-csv" and request["headers"]["x-api-key"] == KEYS["wdgwars_key"]
    boundary = request["headers"]["content-type"].split("boundary=")[1]
    head, _, rest = request["body"].partition(b"\r\n\r\n")
    assert head == ('--%s\r\nContent-Disposition: form-data; name="file"; filename="%s"\r\n'
                    'Content-Type: application/gzip' % (boundary, path.name)).encode()
    assert rest == path.read_bytes() + ("\r\n--%s--\r\n" % boundary).encode()
    assert json.loads(gzip.decompress(path.read_bytes()))["now"] == NOW
    site.replies[("POST", "/api/v2/upload-csv")] = (409, {"error": "duplicate_upload"})
    assert uploads.upload("wdgwars", path, KEYS, site.endpoints).state == uploads.DUPLICATE
    site.replies[("POST", "/api/v2/upload-csv")] = (200, {"ok": False, "error": "bad json"})
    assert uploads.upload("wdgwars", path, KEYS, site.endpoints).state == uploads.REJECTED


def test_wardrift_takes_aircraft_as_gzipped_json_with_the_times_counted_from_the_upload(site, tmp_path):
    made = time.time() - 3600
    path = make_aircraft(tmp_path, made)
    stored = adsb.load(path)
    site.replies[("POST", "/v1/ingest/signals")] = (200, {"item_count": 2})
    before = time.time()
    outcome = uploads.upload("wardrift", path, KEYS, site.endpoints)
    assert outcome.state == uploads.DONE and outcome.detail == "Wardrift has it (2 aircraft)"
    request, = site.requests
    assert request["path"] == "/v1/ingest/signals"
    assert {k: request["headers"][k] for k in ("content-type", "x-file-name", "x-api-key")} == {
        "content-type": "application/gzip", "x-file-name": "quartermaster_20261118-110640.aircraft.json.gz",
        "x-api-key": KEYS["wardrift_key"]}
    assert "content-encoding" not in request["headers"]
    body = json.loads(gzip.decompress(request["body"]))
    assert list(body) == ["now", "aircraft"] and before <= body["now"] <= time.time()
    assert [a["hex"] for a in body["aircraft"]] == ["a1b2c3", "a1b2c4"]
    for sent, kept in zip(body["aircraft"], stored["aircraft"]):
        assert sent["seen"] == pytest.approx(kept["seen"] + body["now"] - made, abs=0.06)
        assert sent["seen_pos"] == pytest.approx(kept["seen_pos"] + body["now"] - made, abs=0.06)
        assert sent["seen"] >= 3600 and {k: v for k, v in sent.items() if not k.startswith("seen")} == \
            {k: v for k, v in kept.items() if not k.startswith("seen")}


def test_wardrift_gets_at_most_5000_aircraft_a_request_and_says_when_they_are_too_old_for_points(
        site, tmp_path, monkeypatch):
    monkeypatch.setattr(adsb, "WARDRIFT_BATCH", 2)
    path = make_aircraft(tmp_path, time.time() - 4 * 86400, count=5)
    site.replies[("POST", "/v1/ingest/signals")] = (200, {"item_count": 2})
    outcome = uploads.upload("wardrift", path, KEYS, site.endpoints)
    assert [len(json.loads(gzip.decompress(r["body"]))["aircraft"]) for r in site.requests] == [2, 2, 1]
    assert [r["headers"]["x-file-name"] for r in site.requests] == [
        "quartermaster_20261118-110640.aircraft.json.gz", "quartermaster_20261118-110640-part2.aircraft.json.gz",
        "quartermaster_20261118-110640-part3.aircraft.json.gz"]
    assert outcome.state == uploads.DONE and outcome.detail == \
        "Wardrift has it (6 aircraft). Over 3 days old, so it earns no points"
    fresh = make_aircraft(tmp_path, time.time() - 71 * 3600, name="quartermaster_20261118-120000.aircraft.json.gz")
    assert "points" not in uploads.upload("wardrift", fresh, KEYS, site.endpoints).detail


def test_wardrift_answers_for_aircraft_are_handled_like_logs(site, tmp_path):
    path = make_aircraft(tmp_path, time.time())
    site.replies[("POST", "/v1/ingest/signals")] = (200, {"duplicate": True})
    assert uploads.upload("wardrift", path, KEYS, site.endpoints).state == uploads.DUPLICATE
    site.replies[("POST", "/v1/ingest/signals")] = (401, {})
    assert uploads.upload("wardrift", path, KEYS, site.endpoints).state == uploads.AUTH
    site.replies[("POST", "/v1/ingest/signals")] = (503, {})
    assert uploads.upload("wardrift", path, KEYS, site.endpoints).state == uploads.RETRY
    count = len(site.requests)
    broken = tmp_path / "quartermaster_20261118-130000.aircraft.json.gz"
    broken.write_bytes(b"not gzip")
    assert uploads.upload("wardrift", broken, KEYS, site.endpoints).state == uploads.REJECTED
    assert len(site.requests) == count


def test_aircraft_are_never_sent_to_wigle(site, tmp_path):
    log = make_log(tmp_path / "logs")
    path = make_aircraft(tmp_path / "logs", NOW)
    site.accept_everything()
    refused = uploads.upload("wigle", path, KEYS, site.endpoints)
    assert refused.state == uploads.REJECTED and refused.detail == "Not for WiGLE" and site.requests == []
    up = uploader(tmp_path, site)
    assert up.logs() == [path, log]
    assert up.run_pass() is False
    sent = sorted((r["path"], r["headers"].get("x-file-name") or r["body"].split(b'filename="')[1].split(b'"')[0]
                   .decode()) for r in site.requests)
    assert sent == sorted([("/api/v2/file/upload", log.name), ("/api/v2/upload-csv", log.name),
                           ("/v1/ingest/signals", log.name), ("/api/v2/upload-csv", path.name),
                           ("/v1/ingest/signals", path.name)])
    assert up.marks(path.name) == {"wigle": "none", "wdgwars": "done", "wardrift": "done"}
    assert up.ledger.entry(path.name, "wigle") == {}
    status = up.status()
    assert status["files"] == 2 and status["waiting"] == 0
    assert status["services"]["wigle"]["sent"] == 1 and status["services"]["wardrift"]["sent"] == 2
    up.run_pass()
    assert len(site.requests) == 5


def test_the_3_day_note_goes_by_when_the_aircraft_were_heard_not_when_the_file_was_written(site, tmp_path):
    from tests.fakes import plane
    sky = adsb.Sky()
    sky.update({"aircraft": [plane("a1b2c3", 25.2, -77.7)]}, time.time() - 4 * 86400,
               (FIX["latitude"], FIX["longitude"]))
    path = tmp_path / "quartermaster_20261118-110640.aircraft.json.gz"
    adsb.write(path, sky, time.time())
    site.replies[("POST", "/v1/ingest/signals")] = (200, {"item_count": 1})
    assert uploads.upload("wardrift", path, KEYS, site.endpoints).detail == \
        "Wardrift has it (1 aircraft). Over 3 days old, so it earns no points"
