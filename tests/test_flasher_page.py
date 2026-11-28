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
CHIPS = flasher.manifest_chips(INDEX)
FAMILIES = {"esp32c5": "ESP32-C5", "esp32c3": "ESP32-C3", "esp32c6": "ESP32-C6", "esp32s3": "ESP32-S3"}


def builds(name):
    data = json.loads((PAGE / name).read_text())
    assert data["name"] == "Quartermaster Node" and data["version"] == INDEX["version"]
    found = {build["chipFamily"]: [(p["path"], p["offset"]) for p in build["parts"]] for build in data["builds"]}
    assert sorted(found) == sorted(FAMILIES[chip] for chip in CHIPS)
    return data, {chip: found[FAMILIES[chip]] for chip in CHIPS}


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_the_page_flashes_every_chip_quartermaster_ships():
    assert sorted(CHIPS) == ["esp32c3", "esp32c5", "esp32c6", "esp32s3"]


def test_first_install_writes_the_merged_image_of_each_chip_at_zero():
    data, written = builds(flasher_page.FIRST)
    assert data["new_install_prompt_erase"] is True and data["new_install_improv_wait_time"] == 0
    for chip, parts in written.items():
        assert parts == [(CHIPS[chip]["merged"]["file"], 0)]
        assert sha(PAGE / parts[0][0]) == CHIPS[chip]["merged"]["sha256"]


def test_update_writes_the_app_of_each_chip_at_ota_0_and_clears_otadata():
    data, written = builds(flasher_page.UPDATE)
    assert data["new_install_prompt_erase"] is False and data["new_install_improv_wait_time"] == 0
    for chip, parts in written.items():
        offsets = CHIPS[chip]["partition_offsets"]
        assert parts[1:] == [(flasher_page.OTADATA, offsets["otadata"])]
        assert parts[0][1] == offsets["ota_0"] == CHIPS[chip]["app"]["offset"]
        assert sha(PAGE / parts[0][0]) == CHIPS[chip]["app"]["sha256"]
        merged = (PAGE / CHIPS[chip]["merged"]["file"]).read_bytes()
        assert merged[offsets["ota_0"]:] == (PAGE / parts[0][0]).read_bytes()
    assert (PAGE / flasher_page.OTADATA).read_bytes() == b"\xff" * flasher.OTADATA_SIZE


def test_every_manifest_path_is_a_file_next_to_it():
    for name in (flasher_page.FIRST, flasher_page.UPDATE):
        for parts in builds(name)[1].values():
            for path, offset in parts:
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
    merged = source / CHIPS["esp32c3"]["merged"]["file"]
    merged.write_bytes(merged.read_bytes()[:-1] + b"\x00")
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
