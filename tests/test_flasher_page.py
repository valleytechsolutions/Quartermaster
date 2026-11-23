import hashlib
import json
from pathlib import Path
import re
import shutil
import sys

import pytest

from quartermaster import flasher

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import flasher_page  # noqa: E402

PAGE = ROOT / "docs" / "flasher"
INDEX = json.loads(Path(flasher.MANIFEST).read_text())


def parts(name):
    data = json.loads((PAGE / name).read_text())
    assert data["name"] == "Quartermaster Node" and data["version"] == INDEX["version"]
    (build,) = data["builds"]
    assert build["chipFamily"] == "ESP32-C5"
    return data, [(p["path"], p["offset"]) for p in build["parts"]]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_first_install_writes_the_merged_image_at_zero():
    data, written = parts(flasher_page.FIRST)
    assert data["new_install_prompt_erase"] is True and data["new_install_improv_wait_time"] == 0
    assert written == [(INDEX["merged"]["file"], 0)]
    assert sha(PAGE / written[0][0]) == INDEX["merged"]["sha256"]


def test_update_writes_the_app_at_ota_0_and_clears_otadata():
    data, written = parts(flasher_page.UPDATE)
    assert data["new_install_prompt_erase"] is False and data["new_install_improv_wait_time"] == 0
    offsets = INDEX["partition_offsets"]
    assert written == [(INDEX["app"]["file"], offsets["ota_0"]), (flasher_page.OTADATA, offsets["otadata"])]
    assert INDEX["app"]["offset"] == offsets["ota_0"]
    assert sha(PAGE / written[0][0]) == INDEX["app"]["sha256"]
    assert (PAGE / flasher_page.OTADATA).read_bytes() == b"\xff" * flasher.OTADATA_SIZE


def test_every_manifest_path_is_a_file_next_to_it():
    for name in (flasher_page.FIRST, flasher_page.UPDATE):
        for path, offset in parts(name)[1]:
            assert "/" not in path and (PAGE / path).is_file() and offset % flasher.SECTOR == 0


def test_regenerating_changes_nothing():
    assert flasher_page.differences(PAGE) == []
    assert sorted(p.name for p in PAGE.iterdir()) == sorted(flasher_page.files())


def test_write_replaces_old_images(tmp_path):
    (tmp_path / "quartermaster-node-0.9.0-app.bin").write_bytes(b"old")
    (tmp_path / "index.html").write_text("old")
    assert flasher_page.differences(tmp_path) != []
    flasher_page.write(tmp_path)
    assert flasher_page.differences(tmp_path) == []
    assert not (tmp_path / "quartermaster-node-0.9.0-app.bin").exists()


def test_write_keeps_files_that_are_not_ours(tmp_path):
    for name in ("keep.txt", "README.md", "CNAME", ".nojekyll"):
        (tmp_path / name).write_text("mine")
    (tmp_path / "manifest-old.json").write_text("{}")
    flasher_page.write(tmp_path)
    assert flasher_page.differences(tmp_path) == []
    assert not (tmp_path / "manifest-old.json").exists()
    for name in ("keep.txt", "README.md", "CNAME", ".nojekyll"):
        assert (tmp_path / name).read_text() == "mine"


def test_the_page_says_first_install_always_clears_settings():
    page = " ".join(flasher_page.files()["index.html"].decode().split())
    assert "First install always clears the node's light, country and hub. Its name stays in Quartermaster." in page
    assert "It keeps the node's light, country and hub." in page and "name, light" not in page
    assert re.search(r"\.wait\{visibility:hidden;animation:late 0s \d+s forwards\}", page)


def test_damaged_firmware_is_refused(tmp_path):
    source = tmp_path / "node"
    shutil.copytree(Path(flasher.MANIFEST).parent, source)
    app = source / INDEX["app"]["file"]
    app.write_bytes(app.read_bytes()[:-1] + b"\x00")
    with pytest.raises(SystemExit):
        flasher_page.files(source / "quartermaster-node.json")


def test_the_page_loads_only_the_pinned_installer():
    page = (PAGE / "index.html").read_text()
    assert re.findall(r"<script[^>]*src=\"([^\"]+)\"", page) == [flasher_page.ESP_WEB_TOOLS]
    assert "esp-web-tools@10.5.2/" in flasher_page.ESP_WEB_TOOLS
    assert not re.search(r"<link|@import|url\(", page)
    assert page.count("<script") == 1
    assert 'manifest="%s"' % flasher_page.FIRST in page and 'manifest="%s"' % flasher_page.UPDATE in page
    assert "Firmware %s" % INDEX["version"] in page


def test_the_page_carries_the_licenses_of_what_the_firmware_includes():
    out = flasher_page.files()
    page = out["index.html"].decode()
    assert '<a href="notices.txt">their licenses</a>' in page
    notices = out[flasher_page.NOTICES].decode()
    assert "ESP-IDF v5.5.6" in notices and "Apache License" in notices and "NimBLE" in notices
