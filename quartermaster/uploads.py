"""Send finished logs to WiGLE, WDGWars and Wardrift, and remember what each site already has.

Every file is sent as plain WigleWifi CSV over verified HTTPS. A file counts as
delivered to a site once that site accepts it or says it already has it. Anything
else is kept and tried again later. Aircraft files go to WDGWars and Wardrift only.
"""

import http.client
import json
import os
from pathlib import Path
import socket
import ssl
import threading
import time
import urllib.parse
import uuid

from . import __version__, accounts as keys, adsb, faults, wigle

ENDPOINTS = {"wigle": "https://api.wigle.net", "wdgwars": "https://wdgwars.pl", "wardrift": "https://wardrift.net"}
SERVICES = tuple(ENDPOINTS)
AIRCRAFT_SERVICES = ("wdgwars", "wardrift")
WARDRIFT_MAX_BYTES = 10 * 1024 * 1024
USER_AGENT = "Quartermaster/%s (CardputerZero)" % __version__

DONE, DUPLICATE, REJECTED, AUTH, RETRY = "done", "duplicate", "rejected", "auth", "retry"
SETTLED = (DONE, DUPLICATE, REJECTED)
BACKOFF = (60, 120, 300, 600, 900)
IDLE_CHECK = 60


class Outcome:
    __slots__ = ("state", "detail", "ref")

    def __init__(self, state, detail="", ref=""):
        self.state, self.detail, self.ref = state, str(detail)[:160], str(ref)[:64]

    def __repr__(self):
        return "Outcome(%r, %r, %r)" % (self.state, self.detail, self.ref)


def _request(method, url, headers, parts=(), timeout=60):
    """Send bytes and files in order as one body. Returns (status, first 64 KiB of the reply)."""
    target = urllib.parse.urlsplit(url)
    length = sum(len(part) if isinstance(part, bytes) else os.path.getsize(part) for part in parts)
    if target.scheme == "https":
        conn = http.client.HTTPSConnection(target.hostname, target.port or 443, timeout=timeout,
                                           context=ssl.create_default_context())
    else:
        conn = http.client.HTTPConnection(target.hostname, target.port or 80, timeout=timeout)
    try:
        conn.putrequest(method, target.path or "/", skip_accept_encoding=True)
        for name, value in headers.items():
            conn.putheader(name, value)
        conn.putheader("User-Agent", USER_AGENT)
        conn.putheader("Accept", "application/json")
        conn.putheader("Connection", "close")
        if method != "GET":
            conn.putheader("Content-Length", str(length))
        conn.endheaders()
        for part in parts:
            if isinstance(part, bytes):
                conn.send(part)
                continue
            with open(part, "rb") as source:
                while True:
                    chunk = source.read(65536)
                    if not chunk:
                        break
                    conn.send(chunk)
        response = conn.getresponse()
        return response.status, response.read(65536)
    finally:
        conn.close()


def sites(name):
    """The sites a file is for: aircraft files are not for WiGLE."""
    return AIRCRAFT_SERVICES if adsb.is_aircraft(name) else SERVICES


def _multipart(path, content_type="text/csv"):
    boundary = "----Quartermaster" + uuid.uuid4().hex
    head = ('--%s\r\nContent-Disposition: form-data; name="file"; filename="%s"\r\n'
            'Content-Type: %s\r\n\r\n' % (boundary, path.name, content_type)).encode("ascii")
    tail = ("\r\n--%s--\r\n" % boundary).encode("ascii")
    return "multipart/form-data; boundary=" + boundary, [head, path, tail]


def _json(body):
    try:
        data = json.loads(body.decode("utf-8", "replace"))
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _reason(data):
    for field in ("message", "error", "detail"):
        value = data.get(field)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _refused(site, status, data):
    """Map a status that is not a success to what should happen with the file."""
    reason = _reason(data)
    if status in (401, 403):
        return Outcome(AUTH, "%s did not accept the key" % site)
    if status in (408, 425, 429) or status >= 500:
        return Outcome(RETRY, "%s is busy (%d). Will try again" % (site, status))
    if status == 413:
        return Outcome(REJECTED, "%s says the file is too large" % site)
    return Outcome(REJECTED, "%s refused the file (%d)%s" % (site, status, ": " + reason if reason else ""))


def _guarded(call):
    try:
        return call()
    except ssl.SSLCertVerificationError as exc:
        text = str(exc)
        if "not yet valid" in text or "expired" in text:
            return Outcome(RETRY, "The clock is not set yet. Will try again")
        return Outcome(RETRY, "Secure connection failed. Will try again")
    except socket.gaierror:
        return Outcome(RETRY, "No internet connection")
    except (TimeoutError, socket.timeout):
        return Outcome(RETRY, "No answer in time. Will try again")
    except (http.client.HTTPException, OSError) as exc:
        return Outcome(RETRY, "Connection problem: %s" % (getattr(exc, "strerror", None) or type(exc).__name__))


def _wigle_upload(path, accounts, base):
    content_type, parts = _multipart(path)
    status, body = _request("POST", base + "/api/v2/file/upload",
                            {"Authorization": "Basic " + keys.wigle_basic(accounts), "Content-Type": content_type},
                            parts, timeout=180)
    data = _json(body)
    if status == 200 and data.get("success") is not False:
        results = data.get("results") if isinstance(data.get("results"), dict) else {}
        ids = [t.get("transId") for t in results.get("transids") or () if isinstance(t, dict) and t.get("transId")]
        return Outcome(DONE, "WiGLE has it" + (" (%s)" % ids[0] if ids else ""), ids[0] if ids else "")
    if status == 200:
        reason = _reason(data)
        if "already" in reason.lower() or "duplicate" in reason.lower():
            return Outcome(DUPLICATE, "WiGLE already had it")
        return Outcome(REJECTED, "WiGLE refused the file%s" % (": " + reason if reason else ""))
    return _refused("WiGLE", status, data)


def _wigle_test(accounts, base):
    status, body = _request("GET", base + "/api/v2/profile/user",
                            {"Authorization": "Basic " + keys.wigle_basic(accounts)}, timeout=30)
    data = _json(body)
    if status == 200 and data.get("success") is not False:
        user = data.get("userid") or data.get("user") or ""
        return Outcome(DONE, "Signed in to WiGLE%s" % (" as %s" % user if isinstance(user, str) and user else ""))
    if status == 200:
        return Outcome(AUTH, "WiGLE did not accept the name and token")
    outcome = _refused("WiGLE", status, data)
    return Outcome(RETRY, "WiGLE could not check the key (%d)" % status) if outcome.state == REJECTED else outcome


def _wdgwars_upload(path, accounts, base, file_type="text/csv"):
    headers = {"X-API-Key": accounts["wdgwars_key"]}
    for route in ("/api/v2/upload-csv", "/api/upload-csv"):
        content_type, parts = _multipart(path, file_type)
        status, body = _request("POST", base + route, dict(headers, **{"Content-Type": content_type}), parts,
                                timeout=180)
        if status not in (404, 405):
            break
    data = _json(body)
    if status == 409 and b"duplicate" in body.lower():
        return Outcome(DUPLICATE, "WDGWars already had it")
    if status in (200, 201, 202) and data.get("ok") is not False and not data.get("error"):
        job = data.get("job_id")
        return Outcome(DONE, "WDGWars has it" + (" (job %s)" % job if job else ""), job or "")
    if status in (200, 201, 202):
        return Outcome(REJECTED, "WDGWars refused the file: %s" % (_reason(data) or "no reason given"))
    return _refused("WDGWars", status, data)


def _wdgwars_test(accounts, base):
    status, body = _request("GET", base + "/api/me", {"X-API-Key": accounts["wdgwars_key"]}, timeout=30)
    data = _json(body)
    if status == 200:
        user = data.get("username")
        if not isinstance(user, str) and isinstance(data.get("user"), dict):
            user = data["user"].get("username")
        return Outcome(DONE, "Signed in to WDGWars%s" % (" as %s" % user if isinstance(user, str) and user else ""))
    outcome = _refused("WDGWars", status, data)
    return Outcome(RETRY, "WDGWars could not check the key (%d)" % status) if outcome.state == REJECTED else outcome


def _wardrift_headers(accounts, name):
    return {"X-API-Key": accounts["wardrift_key"], "Content-Type": "text/csv", "X-File-Name": name}


def _wardrift_upload(path, accounts, base):
    if path.stat().st_size > WARDRIFT_MAX_BYTES:
        return Outcome(REJECTED, "Too large for Wardrift (10 MB limit)")
    status, body = _request("POST", base + "/v1/ingest/signals", _wardrift_headers(accounts, path.name), [path],
                            timeout=180)
    data = _json(body)
    if status == 200:
        if data.get("duplicate") is True:
            return Outcome(DUPLICATE, "Wardrift already had it")
        count = data.get("item_count")
        return Outcome(DONE, "Wardrift has it" + (" (%s signals)" % count if isinstance(count, int) else ""))
    return _refused("Wardrift", status, data)


def _wardrift_aircraft(path, accounts, base):
    """Aircraft go to Wardrift as gzipped JSON with their times counted back from now, in parts of 5000 at most."""
    try:
        data = adsb.load(path)
    except (OSError, EOFError, ValueError):
        return Outcome(REJECTED, "The aircraft file cannot be read")
    now = time.time()
    headers = {"X-API-Key": accounts["wardrift_key"], "Content-Type": "application/gzip"}
    counted, duplicates, parts = 0, 0, 0
    for body in adsb.batches(data, now):
        name = adsb.part_name(path.name, parts + 1)
        status, reply = _request("POST", base + "/v1/ingest/signals", dict(headers, **{"X-File-Name": name}), [body],
                                 timeout=180)
        answer = _json(reply)
        if status != 200:
            return _refused("Wardrift", status, answer)
        parts += 1
        duplicates += answer.get("duplicate") is True
        if isinstance(answer.get("item_count"), int):
            counted += answer["item_count"]
    if not parts:
        return Outcome(REJECTED, "The aircraft file holds no aircraft")
    newest = max((data["now"] - item["seen"] for item in data["aircraft"]
                  if isinstance(item, dict) and isinstance(item.get("seen"), (int, float))), default=data["now"])
    if duplicates == parts:
        return Outcome(DUPLICATE, "Wardrift already had it")
    detail = "Wardrift has it" + (" (%d aircraft)" % counted if counted else "")
    if now - newest > adsb.CREDIT_SECONDS:
        detail += ". Over 3 days old, so it earns no points"
    return Outcome(DONE, detail)


def _wardrift_test(accounts, base):
    # Wardrift has no key-check call. A log with no rows is refused with 400, but only after the key passes.
    lines = "%s\n%s\n" % (",".join(wigle.pre_header()), ",".join(wigle.COLUMNS))
    status, body = _request("POST", base + "/v1/ingest/signals",
                            _wardrift_headers(accounts, "quartermaster_keycheck.wigle.csv"),
                            [lines.encode("utf-8")], timeout=30)
    if status in (200, 400):
        return Outcome(DONE, "Wardrift accepted the key")
    outcome = _refused("Wardrift", status, _json(body))
    return Outcome(RETRY, "Wardrift could not check the key (%d)" % status) if outcome.state == REJECTED else outcome


_UPLOAD = {"wigle": _wigle_upload, "wdgwars": _wdgwars_upload, "wardrift": _wardrift_upload}
_TEST = {"wigle": _wigle_test, "wdgwars": _wdgwars_test, "wardrift": _wardrift_test}
_AIRCRAFT = {"wdgwars": lambda path, accounts, base: _wdgwars_upload(path, accounts, base, "application/gzip"),
             "wardrift": _wardrift_aircraft}


def upload(service, path, accounts, endpoints=None):
    path = Path(path)
    if adsb.is_aircraft(path) and service not in AIRCRAFT_SERVICES:
        return Outcome(REJECTED, "Not for %s" % keys.SERVICE_NAMES[service])
    base = (endpoints or ENDPOINTS)[service]
    send = _AIRCRAFT[service] if adsb.is_aircraft(path) else _UPLOAD[service]
    return _guarded(lambda: send(path, accounts, base))


def test(service, accounts, endpoints=None):
    """Check a site's key without uploading anything. DONE means the key works."""
    if not keys.configured(accounts, service):
        return Outcome(AUTH, "No %s key saved yet" % keys.SERVICE_NAMES[service])
    base = (endpoints or ENDPOINTS)[service]
    return _guarded(lambda: _TEST[service](accounts, base))


class Ledger:
    """Which site has which file. One small JSON file beside the logs."""

    def __init__(self, path):
        self.path = Path(path)
        self.lock = threading.Lock()
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        self.data = data if isinstance(data, dict) else {}

    def entry(self, name, service):
        with self.lock:
            entry = self.data.get(name, {}).get(service)
            return dict(entry) if isinstance(entry, dict) else {}

    def state(self, name, service):
        return self.entry(name, service).get("state", "")

    def settled(self, name, service):
        return self.state(name, service) in SETTLED

    def record(self, name, service, outcome, now):
        with self.lock:
            entry = self.data.setdefault(name, {}).setdefault(service, {})
            entry.update(state=outcome.state, detail=outcome.detail, ref=outcome.ref, time=int(now),
                         tries=int(entry.get("tries", 0)) + 1)
            self._save()

    def reopen(self, name=None):
        """Let refused files be sent again: one file, or all of them."""
        with self.lock:
            for file_name, services in self.data.items():
                if name in (None, file_name):
                    for service in [s for s, e in services.items() if e.get("state") == REJECTED]:
                        del services[service]
            self._save()

    def forget(self, name):
        with self.lock:
            if self.data.pop(name, None) is not None:
                self._save()

    def _save(self):
        tmp = self.path.with_name("." + self.path.name)
        try:
            fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as out:
                json.dump(self.data, out, indent=1, sort_keys=True)
            os.replace(tmp, self.path)
        except OSError:
            pass


class Uploader:
    """Background sender. `kick()` asks for a pass now; otherwise it looks for work once a minute."""

    def __init__(self, root, cfg, active=None, accounts_path=None, endpoints=None, clock=time.time, sender=upload):
        self.logs_dir = Path(root) / "logs"
        self.cfg = cfg
        self.active = active or (lambda: None)
        self.accounts_path = accounts_path
        self.endpoints = endpoints
        self.clock = clock
        self.sender = sender
        self.logs_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.ledger = Ledger(Path(root) / "uploads.json")
        self.current = None
        self.blocked = {}
        self.last = {}
        self.next_try = None
        self.failures = 0
        self._stamp = None
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._manual = False
        self._thread = None
        self._pass = threading.Lock()
        self._with_rows = set()

    def start(self):
        if self._thread is None:
            self._thread = threading.Thread(target=self._loop, daemon=True, name="uploader")
            self._thread.start()

    def stop(self):
        self._stop.set()
        self._wake.set()
        if self._thread is not None:
            self._thread.join(timeout=3)

    def kick(self, manual=False):
        self._manual = self._manual or manual
        self._wake.set()

    def send_again(self, name=None):
        self.ledger.reopen(name)
        self.kick(manual=True)

    def _loop(self):
        delay = 8
        while not self._stop.is_set():
            self._wake.wait(delay)
            if self._stop.is_set():
                return
            manual, self._manual = self._manual, False
            self._wake.clear()
            if not manual and not self.cfg.get("auto_upload", True):
                delay = IDLE_CHECK
                continue
            try:
                retry = self.run_pass()
            except Exception:
                faults.note("Uploads")
                retry = True
            self.failures = self.failures + 1 if retry else 0
            delay = BACKOFF[min(self.failures, len(BACKOFF)) - 1] if retry else IDLE_CHECK
            self.next_try = self.clock() + delay if retry else None

    def logs(self):
        """Finished logs with at least one row, and aircraft files, oldest first. The file still being written is left
        out."""
        before = self.active()
        try:
            files = sorted(list(self.logs_dir.glob(wigle.LOG_GLOB)) + list(self.logs_dir.glob(adsb.FILE_GLOB)))
        except OSError:
            return []
        active = {before, self.active()}
        return [path for path in files if path not in active and self._has_rows(path)]

    def _has_rows(self, path):
        if adsb.is_aircraft(path):
            return True
        if path.name not in self._with_rows and wigle.data_rows(path, limit=1):
            self._with_rows.add(path.name)
        return path.name in self._with_rows

    def _accounts(self):
        accounts = keys.load(self.accounts_path)
        stamp = keys.stamp(self.accounts_path)
        if stamp != self._stamp:
            self._stamp = stamp
            self.blocked.clear()
        return accounts

    def run_pass(self):
        """Try every file that a site still lacks. Returns True when something has to be retried."""
        with self._pass:
            accounts = self._accounts()
            services = [s for s in keys.ready(accounts) if s not in self.blocked]
            retry = False
            try:
                for path in self.logs():
                    waiting = [s for s in services if s in sites(path.name) and not self.ledger.settled(path.name, s)]
                    if not waiting:
                        continue
                    for service in waiting:
                        if self._stop.is_set():
                            return retry
                        self.current = (path.name, service)
                        outcome = self.sender(service, path, accounts, self.endpoints)
                        if not path.exists():
                            continue
                        self.ledger.record(path.name, service, outcome, self.clock())
                        self.last[service] = (outcome.state, outcome.detail, self.clock())
                        if outcome.state == AUTH:
                            self.blocked[service] = True
                            services.remove(service)
                        elif outcome.state == RETRY:
                            retry = True
                            services.remove(service)
                    if not services:
                        break
            finally:
                self.current = None
            return retry

    def marks(self, name, accounts=None):
        """Per site: "done", "rejected", "waiting", "" when no key is saved for it, or "none": not a file for it."""
        accounts = accounts or keys.load(self.accounts_path)
        out = {}
        for service in SERVICES:
            state = self.ledger.state(name, service)
            if service not in sites(name):
                out[service] = "none"
            elif state in (DONE, DUPLICATE):
                out[service] = "done"
            elif not keys.configured(accounts, service):
                out[service] = ""
            else:
                out[service] = "rejected" if state == REJECTED else "waiting"
        return out

    def status(self):
        accounts = keys.load(self.accounts_path)
        files = [path.name for path in self.logs()]
        services = {}
        for service in SERVICES:
            states = [self.ledger.state(name, service) for name in files if service in sites(name)]
            ready = keys.configured(accounts, service)
            state, detail, when = self.last.get(service, ("", "", None))
            services[service] = {
                "name": keys.SERVICE_NAMES[service], "configured": ready, "blocked": service in self.blocked,
                "sent": sum(1 for s in states if s in (DONE, DUPLICATE)),
                "refused": sum(1 for s in states if s == REJECTED),
                "waiting": sum(1 for s in states if s not in SETTLED) if ready else 0,
                "last_state": state, "last": detail, "last_time": when}
        waiting = sum(s["waiting"] for s in services.values())
        return {"files": len(files), "services": services, "waiting": waiting, "current": self.current,
                "uploading": self.current is not None, "next_try": self.next_try,
                "auto": bool(self.cfg.get("auto_upload", True)), "keys": len(keys.ready(accounts))}
