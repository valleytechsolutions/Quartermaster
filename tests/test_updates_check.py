import json
import socket
import urllib.error

from quartermaster import updates_check
from quartermaster.updates_check import GITHUB, LATEST, OFFLINE, RELEASES, REGISTRY, STORE, UNKNOWN, check


class Web:
    """Answers each address with its body, or raises what it is given for it. Anything else is not reachable."""

    def __init__(self, **answers):
        self.answers = {REGISTRY: answers.get("store"), RELEASES: answers.get("github")}
        self.asked = []

    def __call__(self, url, current):
        self.asked.append((url, current))
        answer = self.answers.get(url)
        if answer is None:
            raise urllib.error.URLError(socket.gaierror(-3, "Temporary failure in name resolution"))
        if isinstance(answer, Exception):
            raise answer
        return answer if isinstance(answer, bytes) else json.dumps(answer).encode()


def registry(*apps):
    return {"generated": "2026-11-20", "apps": list(apps)}


OURS = {"id": "quartermaster", "title": "Quartermaster", "package": "quartermaster", "share_code": "WARD"}
OTHER = {"id": "snake", "title": "Snake", "package": "cpz-snake", "version": "9.9.9"}


def test_a_newer_version_in_the_store_says_to_press_7_in_the_appstore():
    web = Web(store=registry(OTHER, dict(OURS, version="1.1.1")), github={"tag_name": "v1.1.2"})
    assert check("1.1.0", web) == (STORE, "1.1.1", "1.1.1 is ready. Open the AppStore and press 7 to update it.")
    assert web.asked == [(REGISTRY, "1.1.0")]


def test_a_newer_release_only_on_github_says_it_reaches_the_store_after_review():
    web = Web(store=registry(OTHER), github={"tag_name": "v1.1.1", "name": "Quartermaster 1.1.1"})
    assert check("1.1.0", web) == (GITHUB, "1.1.1", "1.1.1 is on GitHub. It reaches the AppStore after review.")
    web = Web(store=registry(dict(OURS, version="1.1.0")), github={"tag_name": "v1.2.0"})
    assert check("1.1.0", web)[:2] == (GITHUB, "1.2.0")
    assert check("1.1.0", Web(github={"tag_name": "1.1.1"}))[:2] == (GITHUB, "1.1.1")


def test_the_same_or_an_older_version_everywhere_is_the_latest():
    web = Web(store=registry(dict(OURS, version="1.1.0")), github={"tag_name": "v1.0.4"})
    assert check("1.1.0", web) == (LATEST, "1.1.0", "You have the latest version (1.1.0).")
    assert check("1.1.0", Web(github={"tag_name": "v1.1.0"}))[0] == LATEST
    assert check("1.1.0", Web(store=registry(dict(OURS, version="1.0.9"))))[0] == LATEST


def test_without_a_network_it_asks_whether_wifi_is_connected():
    assert check("1.1.0", Web()) == (OFFLINE, None, "Could not check. Is Wi-Fi connected?")
    timeout = Web(store=socket.timeout("timed out"), github=OSError(101, "Network is unreachable"))
    assert check("1.1.0", timeout)[0] == OFFLINE


def test_answers_that_make_no_sense_are_never_taken_for_a_version():
    for body in (b"<html>Rate limit</html>", b"\xff\xfe", b"[1, 2", b"null", b'"1.2.0"', b"{}",
                 json.dumps({"apps": "quartermaster"}).encode(), json.dumps({"apps": [5, None]}).encode(),
                 json.dumps(registry(dict(OURS, version="latest"))).encode(),
                 json.dumps(registry(dict(OURS, version=["9.0"]))).encode()):
        assert check("1.1.0", Web(store=body, github=body)) == (UNKNOWN, None, "Could not check. Try again later.")
    assert check("1.1.0", Web(store=b"{", github={"tag_name": "v1.1.1"}))[0] == GITHUB
    huge = b'{"tag_name": "v9.0.0"}' + b" " * updates_check.LARGEST
    assert check("1.1.0", Web(store=huge, github=huge))[0] == UNKNOWN


def test_github_with_no_release_yet_is_an_answer_not_a_missing_network():
    missing = urllib.error.HTTPError(RELEASES, 404, "Not Found", {}, None)
    assert check("1.1.0", Web(github=missing))[0] == UNKNOWN
    assert check("1.1.0", Web(store=registry(dict(OURS, version="1.1.0")), github=missing))[0] == LATEST


def test_the_app_is_found_in_the_registry_by_any_name_the_appstore_accepts():
    for entry in ({"download": {"package": "quartermaster"}}, {"deb_package": "quartermaster"},
                  {"package": " Quartermaster "}, {"id": "quartermaster"}, {"uuid": "quartermaster"},
                  {"share_code": "ward"}, {"title": "Quartermaster"}):
        assert updates_check.store_version(registry(OTHER, dict(entry, version="v2.0.1"))) == "2.0.1", entry
    assert updates_check.store_version([dict(OURS, version="2.0")]) == "2.0"
    assert updates_check.store_version(registry(OTHER, {"title": "Quartermaster Lite", "version": "5"})) is None
    named, packaged = {"title": "Quartermaster", "version": "7.0"}, {"package": "quartermaster", "version": "1.2"}
    assert updates_check.store_version(registry(named, packaged)) == "1.2"


def test_versions_compare_in_debian_order():
    newer = updates_check.newer
    assert newer("1.10.0", "1.9.9") and newer("1.1.0", "1.1") and newer("v1.0.5", "1.0.4")
    assert newer("1.1.0-2", "1.1.0-1") and newer("1:0.1", "9.9") and newer("1.1.0-1.1", "1.1.0-1")
    assert not newer("1.1.0", "1.1.0") and not newer("1.1.0", "v1.1.0-0") and not newer("1.0.9", "1.1.0")
    assert not newer("1.2.0-beta", "1.1.0") and not newer("", "1.0") and not newer(None, "1.0")
    assert updates_check.version_key("v1.0.4") == (0, (1, 0, 4), (0,))


def test_the_request_names_quartermaster_and_nothing_else(monkeypatch):
    seen = []

    class Answer:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            pass

        def read(self, size):
            seen.append(size)
            return b"{}"

    def urlopen(request, timeout):
        seen.append((request.full_url, dict(request.header_items()), timeout, request.data))
        return Answer()
    monkeypatch.setattr(updates_check.urllib.request, "urlopen", urlopen)
    assert updates_check.fetch(RELEASES, "1.1.0") == b"{}"
    assert seen == [(RELEASES, {"User-agent": "Quartermaster/1.1.0", "Accept": "application/json"}, 6.0, None),
                    updates_check.LARGEST + 1]
